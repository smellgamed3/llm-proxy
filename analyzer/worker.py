from __future__ import annotations

import logging
import sqlite3
import time
from typing import Any, Callable

import orjson

from .body_reader import BodyReader
from .config import AnalyzerConfig
from .cost import CostCalculator
from .extractors.anthropic import AnthropicExtractor
from .extractors.base import BaseExtractor
from .extractors.generic import GenericExtractor
from .extractors.openai_compat import OpenAICompatExtractor
from .fingerprint import Fingerprinter
from .parallel import ParallelProcessor, process_record_cpu, resolve_num_workers
from .store import AnalyticsStore

logger = logging.getLogger("analyzer.worker")


class AnalyzerWorker:
    """Main worker that reads from raw.db and writes to analytics.db."""

    def __init__(
        self,
        config: AnalyzerConfig,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
        stop_requested: Callable[[], bool] | None = None,
    ):
        self.config = config
        self.progress_callback = progress_callback
        self.stop_requested = stop_requested
        self.analytics_store = AnalyticsStore(config.analytics_db)
        self.body_reader = BodyReader(config.bodies_dir)
        self.fingerprinter = Fingerprinter()
        self.cost_calculator = CostCalculator(config.pricing_file)
        self.extractors: list[BaseExtractor] = [
            OpenAICompatExtractor(),
            AnthropicExtractor(),
            GenericExtractor(),
        ]

        # 持久 raw.db 连接，避免每轮轮询重新 connect + close
        self._raw_conn: sqlite3.Connection | None = None

        # Multi-process support
        self._num_workers = resolve_num_workers(config.num_workers)
        self._parallel: ParallelProcessor | None = None
        if self._num_workers > 1:
            self._parallel = ParallelProcessor(self._num_workers, config.pricing_file)
            logger.info(
                "Parallel processing enabled with %d workers", self._num_workers
            )
        else:
            logger.info("Single-process mode")

    def run(self) -> None:
        if self.config.mode == "incremental":
            start_seq = self.analytics_store.get_watermark()
            logger.info("Incremental mode: resuming from seq %d", start_seq)
            try:
                self._process_loop(start_seq)
            finally:
                self._shutdown_pool()
                self._close_raw_conn()
                self.body_reader.close()
            return

        try:
            self.run_once()
        finally:
            self._shutdown_pool()
            self._close_raw_conn()
            self.body_reader.close()

    def _shutdown_pool(self) -> None:
        if self._parallel is not None:
            self._parallel.shutdown()

    def run_once(self) -> dict[str, int]:
        was_full = self.config.mode == "full"
        if was_full:
            logger.info("Full mode: resetting analytics store")
            self.analytics_store.reset()
            start_seq = 0
        elif self.config.mode == "range":
            start_seq = self._seq_for_timestamp(self.config.since)
            logger.info("Range mode: starting from seq %d", start_seq)
        else:
            start_seq = self.analytics_store.get_watermark()
            logger.info("Run-once incremental catch-up from seq %d", start_seq)

        result = self._process_available(start_seq)

        if was_full:
            logger.info("Full mode: rebuilding FTS index")
            self.analytics_store.rebuild_fts()
        return result

    def _process_loop(self, start_seq: int) -> None:
        seq = start_seq
        batch_size = self.config.batch_size or self.config.min_batch_size
        poll_interval = self.config.min_poll_interval
        
        while True:
            batch = self._fetch_batch(seq, limit=batch_size)
            if not batch:
                # No data: increase interval exponentially (backoff)
                poll_interval = min(poll_interval * 1.5, self.config.max_poll_interval)
                batch_size = self.config.min_batch_size
                time.sleep(poll_interval)
                continue

            # Have data: reset interval and potentially grow batch size
            poll_interval = self.config.min_poll_interval
            seq, processed = self._process_batch(batch, seq)
            logger.debug(
                "Processed batch of %d records, watermark now %d", processed, seq
            )

            # Adaptive sizing: if we filled the batch, try larger next time
            if len(batch) == batch_size:
                batch_size = min(batch_size * 2, self.config.max_batch_size)
                logger.debug("Growing batch size to %d", batch_size)
            else:
                batch_size = self.config.min_batch_size

    def _process_available(self, start_seq: int) -> dict[str, int]:
        seq = start_seq
        processed_total = 0
        until = self.config.until if self.config.mode == "range" else None
        workload = self._describe_workload(start_seq, until=until)
        batch_size = self.config.batch_size or self.config.min_batch_size
        self._emit_progress(
            processed_rows=0,
            total_rows=workload["total_rows"],
            current_seq=seq,
            target_seq=workload["target_seq"],
            last_timestamp=None,
        )

        # 多进程 pipeline：重叠 body I/O 与 CPU 处理
        if self._parallel is not None:
            return self._process_available_pipeline(seq, until, workload, batch_size)

        # 单进程模式：保持原有串行方式
        while True:
            if self._should_stop():
                logger.info("Analyzer stop requested before fetching next batch")
                return {
                    "processed": processed_total,
                    "last_seq": seq,
                    "total_rows": workload["total_rows"],
                    "target_seq": workload["target_seq"],
                    "stopped": True,
                }
            batch = self._fetch_batch(seq, until=until, limit=batch_size)
            if not batch:
                logger.info("No more records to process. Exiting.")
                self._emit_progress(
                    processed_rows=processed_total,
                    total_rows=workload["total_rows"],
                    current_seq=seq,
                    target_seq=workload["target_seq"],
                    last_timestamp=None,
                )
                return {
                    "processed": processed_total,
                    "last_seq": seq,
                    "total_rows": workload["total_rows"],
                    "target_seq": workload["target_seq"],
                    "stopped": False,
                }

            seq, processed = self._process_batch(batch, seq)
            processed_total += processed
            self._emit_progress(
                processed_rows=processed_total,
                total_rows=workload["total_rows"],
                current_seq=seq,
                target_seq=workload["target_seq"],
                last_timestamp=batch[-1].get("timestamp"),
            )

            # Adaptive sizing: if we filled the batch, try larger next time
            if len(batch) == batch_size:
                batch_size = min(batch_size * 2, self.config.max_batch_size)
                logger.debug("Growing batch size to %d", batch_size)

    def _process_available_pipeline(
        self,
        start_seq: int,
        until: str | None,
        workload: dict[str, int],
        start_batch_size: int,
    ) -> dict[str, int]:
        """Pipeline 模式：重叠 body I/O 与 CPU 处理实现最大吞吐。

        流程（每轮循环）：
          1. 读取当前 batch 的 body 并提交到 pool（I/O）
          2. 读取下一个 batch 的 body（I/O，与 pool CPU 重叠）
          3. collect 当前 batch 结果（等待剩余 CPU）
          4. 写入 DB
          5. 提交下一个 batch 到 pool
          → 回到步骤 1（body I/O 与 CPU 持续重叠）
        """
        seq = start_seq
        processed_total = 0
        batch_size = start_batch_size

        # Prime: 第一个 batch — 无重叠
        batch = self._fetch_batch(seq, until=until, limit=batch_size)
        if not batch:
            self._emit_progress(
                processed_rows=0,
                total_rows=workload["total_rows"],
                current_seq=seq,
                target_seq=workload["target_seq"],
                last_timestamp=None,
            )
            return {
                "processed": 0,
                "last_seq": seq,
                "total_rows": workload["total_rows"],
                "target_seq": workload["target_seq"],
                "stopped": False,
            }

        tasks = self._build_tasks_from_batch(batch)
        futures = self._parallel.submit_batch(tasks)
        last_seq = batch[-1]["seq"]

        while True:
            if self._should_stop():
                logger.info("Analyzer stop requested")
                return {
                    "processed": processed_total,
                    "last_seq": last_seq,
                    "total_rows": workload["total_rows"],
                    "target_seq": workload["target_seq"],
                    "stopped": True,
                }

            # Step A: 读取下一个 batch 的 body（I/O，与 pool CPU 并行）
            next_batch = self._fetch_batch(last_seq, until=until, limit=batch_size)
            if next_batch:
                next_tasks = self._build_tasks_from_batch(next_batch)

            # Step B: 收集当前 batch 结果（阻塞等待）
            results = self._parallel.collect_results(futures, len(batch))

            # Step C: 写入当前 batch
            processed = self._write_batch_results(results, batch, last_seq)
            processed_total += processed
            seq = last_seq

            self._emit_progress(
                processed_rows=processed_total,
                total_rows=workload["total_rows"],
                current_seq=seq,
                target_seq=workload["target_seq"],
                last_timestamp=batch[-1].get("timestamp"),
            )

            if not next_batch:
                break

            # Step D: 提交下一个 batch
            futures = self._parallel.submit_batch(next_tasks)
            batch = next_batch
            last_seq = next_batch[-1]["seq"]

            # 自适应 batch size
            if len(batch) == batch_size:
                batch_size = min(batch_size * 2, self.config.max_batch_size)
                logger.debug("Growing batch size to %d", batch_size)

        return {
            "processed": processed_total,
            "last_seq": seq,
            "total_rows": workload["total_rows"],
            "target_seq": workload["target_seq"],
            "stopped": False,
        }

    def _describe_workload(self, start_seq: int, until: str | None = None) -> dict[str, int]:
        try:
            conn = self._get_raw_conn()
            query = (
                "SELECT COUNT(*) AS total_rows, MAX(seq) AS target_seq "
                "FROM raw_requests WHERE seq > ? AND status_code IS NOT NULL"
            )
            params: list[object] = [start_seq]
            if until:
                query += " AND timestamp <= ?"
                params.append(until)
            row = conn.execute(query, params).fetchone()
            return {
                "total_rows": int(row["total_rows"] or 0) if row else 0,
                "target_seq": int(row["target_seq"] or start_seq) if row else start_seq,
            }
        except Exception as e:
            logger.warning("Failed to describe workload: %s", e)
            return {"total_rows": 0, "target_seq": start_seq}

    def _emit_progress(
        self,
        *,
        processed_rows: int,
        total_rows: int,
        current_seq: int,
        target_seq: int,
        last_timestamp: str | None,
    ) -> None:
        if self.progress_callback is None:
            return
        self.progress_callback(
            {
                "processed_rows": processed_rows,
                "total_rows": total_rows,
                "current_seq": current_seq,
                "target_seq": target_seq,
                "last_timestamp": last_timestamp,
            }
        )

    def _should_stop(self) -> bool:
        if self.stop_requested is None:
            return False
        try:
            return bool(self.stop_requested())
        except Exception:
            return False

    def _process_batch(self, batch: list[dict], current_seq: int) -> tuple[int, int]:
        if self._parallel is not None:
            tasks = self._build_tasks_from_batch(batch)
            results = self._parallel.process_batch(tasks)
        else:
            dates_to_refresh: set[str] = set()
            results = self._process_batch_single(batch, dates_to_refresh)

        watermark_seq = batch[-1]["seq"] if batch else current_seq
        processed = self._write_batch_results(results, batch, watermark_seq)
        return watermark_seq, processed

    def _process_batch_single(
        self,
        batch: list[dict],
        dates_to_refresh: set[str],
    ) -> list[dict[str, Any] | None]:
        """Process tasks in the current process (single-process fallback)."""
        results: list[dict[str, Any] | None] = []
        for record in batch:
            results.append(self._process_record(record, dates_to_refresh))
        return results

    def _process_record(
        self,
        record: dict,
        dates_to_refresh: set[str],
    ) -> dict[str, Any] | None:
        """Single-record processing kept for admin/test compatibility."""
        try:
            request_ref = record.get("request_body_ref")
            response_ref = record.get("response_body_ref")
            request_body = self.body_reader.read(request_ref) if request_ref else None
            response_body = self.body_reader.read(response_ref) if response_ref else None
            result = process_record_cpu(
                record,
                request_body,
                response_body,
                self.extractors,
                self.cost_calculator,
                self.fingerprinter,
            )
            if result and result.get("date"):
                dates_to_refresh.add(result["date"])
            return result
        except Exception as e:
            logger.error(
                "Error processing record %s: %s",
                record.get("id"),
                e,
                exc_info=True,
            )
            return None

    def _build_tasks_from_batch(
        self,
        batch: list[dict],
    ) -> list[tuple[dict, str | None, str | None]]:
        """读取 body 并构造 worker 任务元组。"""
        body_refs: list[str] = []
        for record in batch:
            if record.get("request_body_ref"):
                body_refs.append(record["request_body_ref"])
            if record.get("response_body_ref"):
                body_refs.append(record["response_body_ref"])
        bodies = self.body_reader.read_batch(body_refs) if body_refs else {}
        tasks: list[tuple[dict, str | None, str | None]] = []
        for record in batch:
            req_body = bodies.get(record.get("request_body_ref")) if record.get("request_body_ref") else None
            resp_body = bodies.get(record.get("response_body_ref")) if record.get("response_body_ref") else None
            tasks.append((record, req_body, resp_body))
        return tasks

    def _write_batch_results(
        self,
        results: list[dict[str, Any] | None],
        batch: list[dict],
        watermark_seq: int,
    ) -> int:
        """将处理结果写入 analytics DB。返回处理的记录数。"""
        dates_to_refresh: set[str] = set()
        conv_list: list[dict] = []
        template_list: list[tuple[str, str, str | None, float | None]] = []
        processed = 0

        for i, result in enumerate(results):
            record = batch[i]
            processed += 1
            if result is None:
                logger.error("Failed to process record %s", record.get("id"))
                continue
            conv_list.append(result["conv_data"])
            if result["template_info"]:
                template_list.append(result["template_info"])
            if result["date"]:
                dates_to_refresh.add(result["date"])

        # 构建 daily_stats 行
        daily_rows: list[tuple] = []
        for conv in conv_list:
            ts = conv.get("timestamp", "")
            date = ts[:10] if ts else ""
            if not date:
                continue
            model = conv.get("model") or "unknown"
            provider = conv.get("provider") or "unknown"
            is_success = 1 if conv.get("status") == "success" else 0
            is_error = 1 if conv.get("status") != "success" else 0
            daily_rows.append((
                date, model, provider,
                1, is_success, is_error,
                conv.get("total_tokens") or 0,
                conv.get("prompt_tokens") or 0,
                conv.get("completion_tokens") or 0,
                conv.get("cost_usd") or 0.0,
                conv.get("duration_ms") or 0.0,
            ))

        # 组合事务：upsert + daily_stats + watermark 原子写入
        try:
            self.analytics_store.commit_batch_with_watermark(
                conv_list, template_list, daily_rows, watermark_seq, processed,
            )
        except Exception as e:
            logger.error(
                "Failed to commit batch at seq %d: %s, falling back to refresh",
                watermark_seq, e,
            )
            for date in dates_to_refresh:
                try:
                    self.analytics_store.refresh_daily_stats(date)
                except Exception as e2:
                    logger.warning(
                        "Failed to refresh daily stats for %s: %s", date, e2,
                    )

        return processed

    def _get_raw_conn(self) -> sqlite3.Connection:
        """复用持久 raw.db 连接，避免每轮轮询重新 connect + close。"""
        if self._raw_conn is None:
            conn = sqlite3.connect(self.config.raw_db, timeout=10)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            self._raw_conn = conn
        return self._raw_conn

    def _close_raw_conn(self) -> None:
        if self._raw_conn is not None:
            self._raw_conn.close()
            self._raw_conn = None

    def _fetch_batch(self, after_seq: int, until: str | None = None, limit: int | None = None) -> list[dict]:
        if limit is None:
            limit = self.config.batch_size
        try:
            conn = self._get_raw_conn()
            query = """SELECT * FROM raw_requests WHERE seq > ? AND status_code IS NOT NULL"""
            params: list[object] = [after_seq]
            if until:
                query += " AND timestamp <= ?"
                params.append(until)
            query += " ORDER BY seq ASC LIMIT ?"
            params.append(limit)
            rows = conn.execute(
                query,
                params,
            ).fetchall()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.error("Failed to fetch batch: %s", e)
            return []

    def _select_extractor(
        self, path: str, method: str, headers: dict
    ) -> BaseExtractor:
        for extractor in self.extractors:
            if extractor.can_handle(path, method, headers):
                return extractor
        return self.extractors[-1]  # GenericExtractor is always last

    def _seq_for_timestamp(self, since: str | None) -> int:
        """Find the first seq at or after the given ISO timestamp."""
        if not since:
            return 0
        try:
            conn = self._get_raw_conn()
            row = conn.execute(
                "SELECT MIN(seq) FROM raw_requests WHERE timestamp >= ?",
                (since,),
            ).fetchone()
            if row and row[0] is not None:
                return row[0] - 1
        except Exception as e:
            logger.warning("Failed to find seq for timestamp %s: %s", since, e)
        return 0
