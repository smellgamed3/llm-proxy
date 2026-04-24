"""Benchmark: measure single-process vs parallel analyzer throughput.

Usage:
    uv run python tmp_bench/bench.py
"""
from __future__ import annotations

import os
import sqlite3
import sys
import time

# Ensure project root on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analyzer.config import AnalyzerConfig
from analyzer.worker import AnalyzerWorker

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
RAW_DB = os.path.join(DATA_DIR, "logs", "raw.db")
BODIES_DIR = os.path.join(DATA_DIR, "logs", "bodies")
PRICING = os.path.join(os.path.dirname(os.path.dirname(__file__)), "pricing.yaml")


def run_bench(label: str, num_workers: int, batch_size: int = 5000) -> None:
    analytics_db = os.path.join(DATA_DIR, "analytics", f"bench_{label}.db")
    # Remove old
    for f in [analytics_db, analytics_db + "-wal", analytics_db + "-shm"]:
        if os.path.exists(f):
            os.remove(f)

    cfg = AnalyzerConfig(
        raw_db=RAW_DB,
        analytics_db=analytics_db,
        bodies_dir=BODIES_DIR,
        pricing_file=PRICING,
        mode="full",
        batch_size=batch_size,
        min_batch_size=batch_size,
        max_batch_size=batch_size,
        num_workers=num_workers,
    )
    worker = AnalyzerWorker(cfg)
    t0 = time.perf_counter()
    result = worker.run_once()
    elapsed = time.perf_counter() - t0

    processed = result.get("processed", 0)
    rps = processed / elapsed if elapsed > 0 else 0
    print(
        f"[{label}] workers={num_workers} batch={batch_size} "
        f"processed={processed} elapsed={elapsed:.2f}s "
        f"rows/s={rps:.0f}"
    )

    # Cleanup
    for f in [analytics_db, analytics_db + "-wal", analytics_db + "-shm"]:
        if os.path.exists(f):
            os.remove(f)


if __name__ == "__main__":
    cpu_count = os.cpu_count() or 4
    print(f"CPU cores: {cpu_count}")
    print(f"Data rows: {sqlite3.connect(RAW_DB).execute('SELECT COUNT(*) FROM raw_requests WHERE status_code IS NOT NULL').fetchone()[0]}")
    print()

    # Test 1: single process, batch=100 (old default behavior)
    run_bench("single_b100", num_workers=1, batch_size=100)

    # Test 2: single process, batch=5000
    run_bench("single_b5000", num_workers=1, batch_size=5000)

    # Test 3: parallel (auto workers), batch=5000
    run_bench("parallel_b5000", num_workers=0, batch_size=5000)

    print("\nDone.")
