from __future__ import annotations

import hmac
import os
import sqlite3
import threading
from dataclasses import dataclass, field
from typing import Generator, Optional

from fastapi import Depends, Header, HTTPException, Query, status


def _make_pooled_conn(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, check_same_thread=False, timeout=15)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-65536")
    conn.execute("PRAGMA mmap_size=268435456")
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.row_factory = sqlite3.Row
    return conn


class _ConnectionPool:
    def __init__(self):
        self._lock = threading.Lock()
        self._analytics: sqlite3.Connection | None = None
        self._raw: sqlite3.Connection | None = None

    def get_analytics(self) -> sqlite3.Connection:
        if self._analytics is None:
            with self._lock:
                if self._analytics is None:
                    db_path = os.getenv("ANALYTICS_DB", "/data/analytics/analytics.db")
                    self._analytics = _make_pooled_conn(db_path)
        return self._analytics

    def get_raw(self) -> sqlite3.Connection:
        if self._raw is None:
            with self._lock:
                if self._raw is None:
                    db_path = os.getenv("RAW_DB", "/data/logs/raw.db")
                    self._raw = _make_pooled_conn(db_path)
        return self._raw


_pool = _ConnectionPool()


@dataclass
class AuthContext:
    """Resolved auth context for the current request."""
    is_admin: bool = False
    key_hashes: list[str] = field(default_factory=list)

    def where_clause(self, col: str = "api_key_hash") -> tuple[str, list[str]]:
        """Return (SQL fragment, params) for filtering by key_hashes.

        Admin sees everything (empty WHERE). Regular users get an IN clause.
        """
        if self.is_admin:
            return "", []
        if not self.key_hashes:
            # No keys → match nothing
            return f"{col} = '__none__'", []
        placeholders = ",".join("?" for _ in self.key_hashes)
        return f"{col} IN ({placeholders})", list(self.key_hashes)


def resolve_auth(
    authorization: Optional[str] = Header(default=None),
    key_hashes: Optional[str] = Query(default=None, alias="key_hashes"),
) -> AuthContext:
    """Resolve the auth context from request headers / query params.

    Auth model:
    - ``ADMIN_KEY_HASH`` env var: the hash that grants full access.
    - ``Authorization: Bearer <key_hash>`` or ``?key_hashes=h1,h2``: scoped access.
    - ``DASHBOARD_API_KEY`` env var (legacy): if set, must match Bearer token.
    """
    admin_hash = os.getenv("ADMIN_KEY_HASH", "").strip()
    legacy_key = os.getenv("DASHBOARD_API_KEY", "").strip()

    bearer_token: str = ""
    if authorization and authorization.lower().startswith("bearer "):
        bearer_token = authorization[7:].strip()

    # Legacy gate: if DASHBOARD_API_KEY is set, the bearer token must match it
    if legacy_key:
        if not bearer_token or not hmac.compare_digest(bearer_token, legacy_key):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or missing API key",
                headers={"WWW-Authenticate": "Bearer"},
            )
        # Legacy mode: treat as admin (backward compatible)
        return AuthContext(is_admin=True)

    # Collect hashes from bearer token and query param
    hashes: list[str] = []
    if bearer_token:
        hashes.append(bearer_token)
    if key_hashes:
        for h in key_hashes.split(","):
            h = h.strip()
            if h and h not in hashes:
                hashes.append(h)

    # If no hashes provided at all, reject
    if not hashes:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key hash required. Provide via Authorization header or key_hashes query param.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Check admin
    is_admin = any(
        admin_hash and hmac.compare_digest(h, admin_hash) for h in hashes
    )

    return AuthContext(is_admin=is_admin, key_hashes=hashes)


def get_analytics_db() -> Generator[sqlite3.Connection, None, None]:
    yield _pool.get_analytics()


def get_raw_db() -> Generator[sqlite3.Connection, None, None]:
    yield _pool.get_raw()


def get_bodies_dir() -> str:
    return os.getenv("BODIES_DIR", "/data/logs/bodies")


# 模块级 BodyReader 缓存（按路径区分，避免测试环境的路径冲突）
_body_readers: dict[str, BodyReader] = {}
_body_reader_lock = threading.Lock()

def get_body_reader(bodies_dir: str = Depends(get_bodies_dir)) -> BodyReader:
    from analyzer.body_reader import BodyReader
    if bodies_dir not in _body_readers:
        with _body_reader_lock:
            if bodies_dir not in _body_readers:
                _body_readers[bodies_dir] = BodyReader(bodies_dir)
    return _body_readers[bodies_dir]
