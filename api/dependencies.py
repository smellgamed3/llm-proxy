from __future__ import annotations

import os
import sqlite3
from typing import Generator


def get_analytics_db() -> Generator[sqlite3.Connection, None, None]:
    db_path = os.getenv("ANALYTICS_DB", "/data/analytics/analytics.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def get_raw_db() -> Generator[sqlite3.Connection, None, None]:
    db_path = os.getenv("RAW_DB", "/data/logs/raw.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def get_bodies_dir() -> str:
    return os.getenv("BODIES_DIR", "/data/logs/bodies")
