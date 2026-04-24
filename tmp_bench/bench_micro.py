"""Micro-benchmark: isolate bottleneck by phase."""
from __future__ import annotations

import os
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analyzer.config import AnalyzerConfig
from analyzer.worker import AnalyzerWorker

DATA = os.path.join(os.path.dirname(__file__), "data")
RAW_DB = os.path.join(DATA, "logs", "raw.db")
BODIES = os.path.join(DATA, "logs", "bodies")
PRICING = os.path.join(os.path.dirname(os.path.dirname(__file__)), "pricing.yaml")


class TimingWorker(AnalyzerWorker):
    """Wraps _process_batch to time each phase."""

    def _process_batch(self, batch, current_seq):
        t0 = time.perf_counter()

        # Phase 1: body reading
        if self._parallel is not None:
            body_refs = []
            for r in batch:
                if r.get("request_body_ref"):
                    body_refs.append(r["request_body_ref"])
                if r.get("response_body_ref"):
                    body_refs.append(r["response_body_ref"])
            t1 = time.perf_counter()
            bodies = self.body_reader.read_batch(body_refs) if body_refs else {}
            t2 = time.perf_counter()
            tasks = []
            for r in batch:
                rb = bodies.get(r.get("request_body_ref")) if r.get("request_body_ref") else None
                rp = bodies.get(r.get("response_body_ref")) if r.get("response_body_ref") else None
                tasks.append((r, rb, rp))
            t3 = time.perf_counter()
            results = self._parallel.process_batch(tasks)
            t4 = time.perf_counter()
        else:
            dates = set()
            results = self._process_batch_single(batch, dates)
            t1 = t2 = t3 = time.perf_counter()
            t4 = time.perf_counter()

        # Phase 3: DB writes
        conv_list = []
        template_list = []
        dates_to_refresh = set()
        for i, result in enumerate(results):
            record = batch[i]
            if result is None:
                continue
            conv_list.append(result["conv_data"])
            if result["template_info"]:
                template_list.append(result["template_info"])
            if result["date"]:
                dates_to_refresh.add(result["date"])

        self.analytics_store.upsert_conversations_batch(conv_list)
        self.analytics_store.upsert_prompt_templates_batch(template_list)
        seq = batch[-1]["seq"] if batch else current_seq
        self.analytics_store.set_watermark(seq, len(batch))

        daily_rows = []
        for conv in conv_list:
            ts = conv.get("timestamp", "")
            date = ts[:10] if ts else ""
            if not date:
                continue
            model = conv.get("model") or "unknown"
            provider = conv.get("provider") or "unknown"
            daily_rows.append((
                date, model, provider,
                1, 1 if conv.get("status") == "success" else 0,
                1 if conv.get("status") != "success" else 0,
                conv.get("total_tokens") or 0, conv.get("prompt_tokens") or 0,
                conv.get("completion_tokens") or 0, conv.get("cost_usd") or 0.0,
                conv.get("duration_ms") or 0.0,
            ))
        if daily_rows:
            self.analytics_store.increment_daily_stats_batch(daily_rows)

        t5 = time.perf_counter()

        n = len(batch)
        self._timings["body_read"] += t2 - t1
        self._timings["cpu"] += t4 - t3
        self._timings["db_write"] += t5 - t4
        self._timings["overhead"] += (t1 - t0) + (t5 - t4 - (t5 - t4))
        self._timings["batches"] += 1
        self._timings["records"] += n
        return seq, n


def make_cfg(workers: int, batch: int) -> AnalyzerConfig:
    tag = f"w{workers}_b{batch}"
    adb = os.path.join(DATA, "analytics", f"bench_{tag}.db")
    for f in [adb, adb + "-wal", adb + "-shm"]:
        if os.path.exists(f):
            os.remove(f)
    return AnalyzerConfig(
        raw_db=RAW_DB, analytics_db=adb, bodies_dir=BODIES,
        pricing_file=PRICING, mode="full",
        batch_size=batch, min_batch_size=batch, max_batch_size=batch,
        num_workers=workers,
    )


def bench(label: str, workers: int, batch: int) -> None:
    cfg = make_cfg(workers, batch)
    worker = TimingWorker(cfg)
    worker._timings = {"body_read": 0, "cpu": 0, "db_write": 0, "overhead": 0, "batches": 0, "records": 0}

    t0 = time.perf_counter()
    result = worker.run_once()
    elapsed = time.perf_counter() - t0

    t = worker._timings
    n = t["records"]
    print(f"\n=== {label} (workers={workers}, batch={batch}) ===")
    print(f"Total: {elapsed:.2f}s  records={n}  rows/s={n/elapsed:.0f}")
    print(f"  Body read:  {t['body_read']:.2f}s ({t['body_read']/elapsed*100:.0f}%)")
    print(f"  CPU/parse:  {t['cpu']:.2f}s ({t['cpu']/elapsed*100:.0f}%)")
    print(f"  DB writes:  {t['db_write']:.2f}s ({t['db_write']/elapsed*100:.0f}%)")
    print(f"  Batches:    {t['batches']}")

    adb = cfg.analytics_db
    for f in [adb, adb + "-wal", adb + "-shm"]:
        if os.path.exists(f):
            os.remove(f)


if __name__ == "__main__":
    cpu = os.cpu_count() or 4
    rows = sqlite3.connect(RAW_DB).execute(
        "SELECT COUNT(*) FROM raw_requests WHERE status_code IS NOT NULL"
    ).fetchone()[0]
    print(f"CPU: {cpu}  Rows: {rows}")

    bench("single_b100", workers=1, batch=100)
    bench("single_b5000", workers=1, batch=5000)
    bench("parallel_b5000", workers=0, batch=5000)
    print("\nDone.")
