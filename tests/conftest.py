"""
Shared fixtures and helpers for tests.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from app.config import Config, RecordingFilter, FilterRule
from app.recorder import Recorder


# ── helpers ──────────────────────────────────────────────────────────────────

def db_rows(recorder: Recorder, table: str, **where) -> list[dict]:
    """Fetch all rows from a SQLite table as dicts, optionally filtering."""
    conn = sqlite3.connect(str(recorder.db_path))
    conn.row_factory = sqlite3.Row
    if where:
        clauses = " AND ".join(f"{k}=?" for k in where)
        rows = conn.execute(
            f"SELECT * FROM {table} WHERE {clauses}", list(where.values())
        ).fetchall()
    else:
        rows = conn.execute(f"SELECT * FROM {table}").fetchall()
    return [dict(r) for r in rows]


def jsonl_bodies(recorder: Recorder) -> list[dict]:
    """Parse all JSONL body records from the bodies directory (excluding manifest)."""
    results = []
    bodies_dir = recorder.bodies_dir
    if not bodies_dir.exists():
        return results
    for jsonl_file in sorted(bodies_dir.glob("*.jsonl")):
        if jsonl_file.name == "manifest.jsonl":
            continue
        for line in jsonl_file.read_text(encoding="utf-8").splitlines():
            if line.strip():
                results.append(json.loads(line))
    return results


# ── fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_log_dir(tmp_path: Path) -> Path:
    return tmp_path / "logs"


@pytest.fixture
def base_config(tmp_log_dir: Path) -> Config:
    return Config(
        upstream_url="http://testserver-upstream",
        log_dir=str(tmp_log_dir),
    )


@pytest.fixture
def recorder(base_config: Config) -> Recorder:
    return Recorder(base_config)
