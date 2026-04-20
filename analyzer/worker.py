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
            return

        try:
            self.run_once()
        finally:
            self._shutdown_pool()

    def _shutdown_pool(self) -> None:
        if self._parallel is not None:
            self._parallel.shutdown()

    def run_once(self) -> dict[str, int]:
        if self.config.mode == "full":
            logger.info("Full mode: resetting analytics store")
            self.analytics_store.reset()
            start_seq = 0
        elif self.config.mode == "range":
            start_seq = self._seq_for_timestamp(self.config.since)
            logger.info("Range mode: starting from seq %d", start_seq)
        else:
            start_seq = self.analytics_store.get_watermark()
            logger.info("Run-once incremental catch-up from seq %d", start_seq)

        return self._process_available(start_seq)

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
        self._emit_progress(
            processed_rows=0,
            total_rows=workload["total_rows"],
            current_seq=seq,
            target_seq=workload["target_seq"],
            last_timestamp=None,
        )
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
            batch = self._fetch_batch(seq, until=until)
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

    def _describe_workload(self, start_seq: int, until: str | None = None) -> dict[str, int]:
        try:
            conn = sqlite3.connect(self.config.raw_db, timeout=10)
            conn.row_factory = sqlite3.Row
            query = (
                "SELECT COUNT(*) AS total_rows, MAX(seq) AS target_seq "
                "FROM raw_requests WHERE seq > ? AND status_code IS NOT NULL"
            )
            params: list[object] = [start_seq]
            if until:
                query += " AND timestamp <= ?"
                params.append(until)
            row = conn.execute(query, params).fetchone()
            conn.close()
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
        seq = current_seq
        dates_to_refresh: set[str] = set()
        processed = 0

        # --- Step 1: Batch-read body files (I/O, main process) ---
        body_refs: list[str] = []
        for record in batch:
            if record.get("request_body_ref"):
                body_refs.append(record["request_body_ref"])
            if record.get("response_body_ref"):
                body_refs.append(record["response_body_ref"])

        bodies = self.body_reader.read_batch(body_refs) if body_refs else {}

        # --- Step 2: CPU processing (parallel or single-process) ---
        tasks: list[tuple[dict, str | None, str | None]] = []
        for record in batch:
            req_body = bodies.get(record.get("request_body_ref")) if record.get("request_body_ref") else None
            resp_body = bodies.get(record.get("response_body_ref")) if record.get("response_body_ref") else None
            tasks.append((record, req_body, resp_body))

        if self._parallel is not None:
            results = self._parallel.process_batch(tasks)
        else:
            results = self._process_batch_single(tasks)

        # --- Step 3: Batch-write results (I/O, main process) ---
        conv_list: list[dict] = []
        template_list: list[tuple[str, str, str | None, float | None]] = []

        for i, result in enumerate(results):
            record = batch[i]
            seq = record["seq"]
            processed += 1

            if result is None:
                logger.error("Failed to process record %s", record.get("id"))
                continue

            conv_list.append(result["conv_data"])
            if result["template_info"]:
                template_list.append(result["template_info"])
            if result["date"]:
                dates_to_refresh.add(result["date"])

        self.analytics_store.upsert_conversations_batch(conv_list)
        self.analytics_store.upsert_prompt_templates_batch(template_list)
        self.analytics_store.set_watermark(seq, processed)

        for date in dates_to_refresh:
            try:
                self.analytics_store.refresh_daily_stats(date)
            except Exception as e:
                logger.warning("Failed to refresh daily stats for %s: %s", date, e)

        return seq, processed

    def _process_batch_single(
        self, tasks: list[tuple[dict, str | None, str | None]]
    ) -> list[dict[str, Any] | None]:
        """Process tasks in the current process (single-process fallback)."""
        results: list[dict[str, Any] | None] = []
        for record, req_body, resp_body in tasks:
            try:
                result = process_record_cpu(
                    record,
                    req_body,
                    resp_body,
                    self.extractors,
                    self.cost_calculator,
                    self.fingerprinter,
                )
                results.append(result)
            except Exception as e:
                logger.error(
                    "Error processing record %s: %s",
                    record.get("id"),
                    e,
                    exc_info=True,
                )
                results.append(None)
        return results

    def _fetch_batch(self, after_seq: int, until: str | None = None, limit: int | None = None) -> list[dict]:
        if limit is None:
            limit = self.config.batch_size
        try:
            conn = sqlite3.connect(self.config.raw_db, timeout=10)
            conn.row_factory = sqlite3.Row
            # Only process finalized HTTP records. If we analyze rows before
            # record_response updates status/body refs, conversation fields can be
            # permanently empty because watermark moves past them.
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
            conn.close()
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
            conn = sqlite3.connect(self.config.raw_db, timeout=10)
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT MIN(seq) FROM raw_requests WHERE timestamp >= ?",
                (since,),
            ).fetchone()
            conn.close()
            if row and row[0] is not None:
                return row[0] - 1
        except Exception as e:
            logger.warning("Failed to find seq for timestamp %s: %s", since, e)
        return 0
