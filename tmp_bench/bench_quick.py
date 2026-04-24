"""Quick 1000-row benchmark to identify bottleneck distribution."""
from __future__ import annotations

import os, sqlite3, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analyzer.config import AnalyzerConfig
from analyzer.worker import AnalyzerWorker

DATA = os.path.join(os.path.dirname(__file__), "data")
RAW_DB = os.path.join(DATA, "logs", "raw.db")
BODIES = os.path.join(DATA, "logs", "bodies")
PRICING = os.path.join(os.path.dirname(os.path.dirname(__file__)), "pricing.yaml")


class Timed(AnalyzerWorker):
    phase_times: dict[str, float] = {}

    def _process_batch(self, batch, current_seq):
        t0 = time.perf_counter()
        if self._parallel is not None:
            refs = []
            for r in batch:
                if r.get("request_body_ref"): refs.append(r["request_body_ref"])
                if r.get("response_body_ref"): refs.append(r["response_body_ref"])
            t1 = time.perf_counter()
            bodies = self.body_reader.read_batch(refs) if refs else {}
            t2 = time.perf_counter()
            tasks = [(r, bodies.get(r.get("request_body_ref")) if r.get("request_body_ref") else None,
                       bodies.get(r.get("response_body_ref")) if r.get("response_body_ref") else None) for r in batch]
            t3 = time.perf_counter()
            results = self._parallel.process_batch(tasks)
            t4 = time.perf_counter()
        else:
            dates = set()
            t1 = time.perf_counter()
            results = self._process_batch_single(batch, dates)
            t2 = t3 = t4 = time.perf_counter()

        conv_list, tmpl_list, dates_set = [], [], set()
        for i, res in enumerate(results):
            if res is None: continue
            conv_list.append(res["conv_data"])
            if res["template_info"]: tmpl_list.append(res["template_info"])
            if res["date"]: dates_set.add(res["date"])
        self.analytics_store.upsert_conversations_batch(conv_list)
        self.analytics_store.upsert_prompt_templates_batch(tmpl_list)
        seq = batch[-1]["seq"]
        self.analytics_store.set_watermark(seq, len(batch))

        daily = []
        for c in conv_list:
            ts = c.get("timestamp",""); d = ts[:10]
            if not d: continue
            daily.append((d, c.get("model") or "unknown", c.get("provider") or "unknown",
                1, 1 if c.get("status")=="success" else 0, 1 if c.get("status")!="success" else 0,
                c.get("total_tokens") or 0, c.get("prompt_tokens") or 0,
                c.get("completion_tokens") or 0, c.get("cost_usd") or 0.0, c.get("duration_ms") or 0.0))
        if daily:
            self.analytics_store.increment_daily_stats_batch(daily)
        t5 = time.perf_counter()

        self.phase_times["body_read"] = self.phase_times.get("body_read", 0) + (t2 - t1)
        self.phase_times["cpu"] = self.phase_times.get("cpu", 0) + (t4 - t3)
        self.phase_times["db_write"] = self.phase_times.get("db_write", 0) + (t5 - t4)
        self.phase_times["fetch"] = self.phase_times.get("fetch", 0) + (t1 - t0)
        return seq, len(batch)


def run(label, workers, batch, limit_rows=1000):
    adb = os.path.join(DATA, "analytics", f"q_{label}.db")
    for f in [adb, adb+"-wal", adb+"-shm"]:
        if os.path.exists(f): os.remove(f)

    cfg = AnalyzerConfig(raw_db=RAW_DB, analytics_db=adb, bodies_dir=BODIES,
        pricing_file=PRICING, mode="full", batch_size=batch, min_batch_size=batch,
        max_batch_size=batch, num_workers=workers)
    w = Timed(cfg)
    w.phase_times = {}

    # Override to only process first N rows
    orig = w._fetch_batch
    fetched = [0]
    def limited_fetch(after_seq, until=None, limit=None):
        if fetched[0] >= limit_rows:
            return []
        rows = orig(after_seq, until=until, limit=limit)
        fetched[0] += len(rows)
        return rows
    w._fetch_batch = limited_fetch

    t0 = time.perf_counter()
    w.run_once()
    elapsed = time.perf_counter() - t0

    p = w.phase_times
    n = fetched[0]
    print(f"\n=== {label} workers={workers} batch={batch} rows={n} ===")
    print(f"Total: {elapsed:.2f}s  rows/s={n/elapsed:.0f}")
    for k, v in sorted(p.items()):
        print(f"  {k:12s}: {v:.2f}s ({v/elapsed*100:.0f}%)")

    for f in [adb, adb+"-wal", adb+"-shm"]:
        if os.path.exists(f): os.remove(f)


if __name__ == "__main__":
    cpu = os.cpu_count() or 4
    print(f"CPU: {cpu}")
    run("single_b100", workers=1, batch=100, limit_rows=1000)
    run("parallel_b5000", workers=0, batch=5000, limit_rows=5000)
    print("\nDone.")
