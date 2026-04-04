from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import time
from typing import Any, Callable

from .body_reader import BodyReader
from .config import AnalyzerConfig
from .cost import CostCalculator
from .extractors.anthropic import AnthropicExtractor
from .extractors.base import BaseExtractor
from .extractors.generic import GenericExtractor
from .extractors.openai_compat import OpenAICompatExtractor
from .fingerprint import Fingerprinter
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

    def run(self) -> None:
        if self.config.mode == "incremental":
            start_seq = self.analytics_store.get_watermark()
            logger.info("Incremental mode: resuming from seq %d", start_seq)
            self._process_loop(start_seq)
            return

        self.run_once()

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
        while True:
            batch = self._fetch_batch(seq)
            if not batch:
                time.sleep(self.config.interval)
                continue

            seq, processed = self._process_batch(batch, seq)
            logger.debug("Processed batch of %d records, watermark now %d", processed, seq)

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
        for record in batch:
            if self._should_stop():
                logger.info("Analyzer stop requested during batch at seq %s", seq)
                break
            try:
                self._process_record(record, dates_to_refresh)
                seq = record["seq"]
                processed += 1
            except Exception as e:
                logger.error("Error processing record %s: %s", record.get("id"), e, exc_info=True)
                seq = record["seq"]
                processed += 1

        self.analytics_store.set_watermark(seq, processed)

        for date in dates_to_refresh:
            try:
                self.analytics_store.refresh_daily_stats(date)
            except Exception as e:
                logger.warning("Failed to refresh daily stats for %s: %s", date, e)

        return seq, processed

    def _fetch_batch(self, after_seq: int, until: str | None = None) -> list[dict]:
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
            params.append(self.config.batch_size)
            rows = conn.execute(
                query,
                params,
            ).fetchall()
            conn.close()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.error("Failed to fetch batch: %s", e)
            return []

    def _process_record(self, record: dict, dates_to_refresh: set[str]) -> None:
        # Read request and response bodies
        request_body = None
        if record.get("request_body_ref"):
            request_body = self.body_reader.read(record["request_body_ref"])

        response_body = None
        if record.get("response_body_ref"):
            response_body = self.body_reader.read(record["response_body_ref"])

        # Build request headers dict
        request_headers: dict = {}
        if record.get("request_headers"):
            try:
                request_headers = json.loads(record["request_headers"])
            except Exception:
                pass

        # Select extractor
        path = record.get("path", "")
        method = record.get("method", "")
        extractor = self._select_extractor(path, method, request_headers)
        result = extractor.extract(record, request_body, response_body)

        # Calculate cost
        cost = self.cost_calculator.calculate(
            result.model, result.prompt_tokens, result.completion_tokens
        )

        # Fingerprint
        template_id = self.fingerprinter.fingerprint(result.system_prompt)

        # Extract api_key_hash from raw record or compute from headers
        api_key_hash = record.get("api_key_hash")
        if not api_key_hash:
            auth = request_headers.get("authorization") or request_headers.get("Authorization") or ""
            if auth.lower().startswith("bearer "):
                key = auth[7:].strip()
            else:
                key = (request_headers.get("x-api-key") or request_headers.get("X-Api-Key") or "").strip()
            if key:
                api_key_hash = hashlib.sha256(key.encode()).hexdigest()[:32]

        # Build conversation data
        timestamp = record.get("timestamp", "")
        date = timestamp[:10] if timestamp else ""

        tools_list_json = json.dumps(result.tools_list) if result.tools_list else None

        conv_data: dict = {
            "id": record["id"],
            "seq": record.get("seq"),
            "timestamp": timestamp,
            "path": path,
            "method": method,
            "provider": result.provider,
            "model": result.model,
            "request_type": result.request_type,
            "status": result.status,
            "error_type": result.error_type,
            "error_message": result.error_message,
            "status_code": record.get("status_code"),
            "is_stream": record.get("is_stream", 0),
            "duration_ms": record.get("duration_ms"),
            "client_ip": record.get("client_ip"),
            "upstream_url": record.get("upstream_url"),
            "prompt_tokens": result.prompt_tokens,
            "completion_tokens": result.completion_tokens,
            "total_tokens": result.total_tokens,
            "cost_usd": cost,
            "template_id": template_id,
            "finish_reason": result.finish_reason,
            "has_tools": 1 if result.has_tools else 0,
            "tools_list": tools_list_json,
            "messages_count": result.messages_count,
            "temperature": result.temperature,
            "max_tokens": result.max_tokens,
            "system_prompt": result.system_prompt,
            "user_prompt": result.user_prompt,
            "assistant_response": result.assistant_response,
            "api_key_hash": api_key_hash,
        }

        self.analytics_store.upsert_conversation(conv_data)

        if template_id and result.system_prompt:
            self.analytics_store.upsert_prompt_template(
                template_id, result.system_prompt, conv_data
            )

        if date:
            dates_to_refresh.add(date)

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
