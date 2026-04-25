"""Migrate body data from JSONL shard files into raw.db inline BLOB columns.

Usage:
    python scripts/migrate_bodies_to_db.py /path/to/raw.db /path/to/bodies

This is a one-time migration. After running:
  - raw.db has request_body / response_body BLOB columns
  - JSONL shard files remain untouched (can be removed later)
  - analyzer can read body directly from SQLite, bypassing FUSE file I/O
"""
from __future__ import annotations

import json
import logging
import sqlite3
import sys
import time
import zlib
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
)
logger = logging.getLogger("migrate_bodies")

_COMPRESS_LEVEL = 6


def _add_columns(conn: sqlite3.Connection) -> bool:
    """添加 request_body / response_body BLOB 列（如不存在）。"""
    existing = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(raw_requests)").fetchall()
    }
    added = False
    for col in ("request_body", "response_body"):
        if col not in existing:
            logger.info("Adding column: %s BLOB", col)
            conn.execute(f"ALTER TABLE raw_requests ADD COLUMN {col} BLOB")
            added = True
    return added


def _load_manifest(bodies_dir: Path) -> dict[str, tuple[str, int, int]]:
    """加载 manifest.jsonl 到内存。"""
    manifest_path = bodies_dir / "manifest.jsonl"
    if not manifest_path.exists():
        logger.warning("manifest.jsonl not found at %s", manifest_path)
        return {}
    cache: dict[str, tuple[str, int, int]] = {}
    with open(manifest_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            entry = json.loads(line)
            ref = entry.get("ref")
            if ref:
                cache[ref] = (entry["file"], entry["offset"], entry["length"])
    logger.info("Loaded manifest: %d entries", len(cache))
    return cache


def _read_body_from_shard(
    bodies_dir: Path, fname: str, offset: int, length: int
) -> str | None:
    """从 shard 文件中读取指定位置的 body 数据。"""
    shard_path = bodies_dir / fname
    if not shard_path.exists():
        logger.warning("Shard file not found: %s", shard_path)
        return None
    try:
        with open(shard_path, "rb") as f:
            f.seek(offset)
            raw = f.read(length)
        record = json.loads(raw)
        return record.get("data")
    except Exception as e:
        logger.warning("Failed to read from %s offset %d: %s", fname, offset, e)
        return None


def _get_body_for_record(
    conn: sqlite3.Connection,
    record_id: str,
    direction: str,
    manifest: dict[str, tuple[str, int, int]],
    bodies_dir: Path,
) -> bytes | None:
    """获取并压缩指定记录的 body。"""
    ref = f"{record_id}:{direction}"
    entry = manifest.get(ref)
    if entry is None:
        return None
    fname, offset, length = entry
    body_text = _read_body_from_shard(bodies_dir, fname, offset, length)
    if body_text is None:
        return None
    return zlib.compress(body_text.encode("utf-8"), _COMPRESS_LEVEL)


def migrate(bodies_dir: str, raw_db: str) -> None:
    """主迁移逻辑。"""
    bodies_path = Path(bodies_dir)
    db_path = Path(raw_db)

    if not db_path.exists():
        logger.error("raw.db not found: %s", db_path)
        sys.exit(1)

    logger.info("raw.db: %s", db_path.resolve())
    logger.info("bodies: %s", bodies_path.resolve())

    conn = sqlite3.connect(raw_db, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=OFF")  # migration only, speed > safety

    # 1. Add columns
    _add_columns(conn)

    # 2. Load manifest
    manifest = _load_manifest(bodies_path)
    if not manifest:
        logger.warning("No manifest data. Migration may be incomplete.")

    # 3. Count records needing migration
    total = conn.execute(
        "SELECT COUNT(*) AS c FROM raw_requests "
        "WHERE request_body IS NULL AND (request_body_ref IS NOT NULL OR response_body_ref IS NOT NULL)"
    ).fetchone()["c"]
    logger.info("Records needing migration: %d", total)

    if total == 0:
        logger.info("Nothing to migrate (all bodies already inline).")
        conn.close()
        return

    # 4. Batch migrate
    batch_size = 1000
    migrated = 0
    errors = 0
    skipped = 0
    t0 = time.monotonic()

    page = 0
    while True:
        rows = conn.execute(
            "SELECT id, request_body_ref, response_body_ref FROM raw_requests "
            "WHERE request_body IS NULL AND (request_body_ref IS NOT NULL OR response_body_ref IS NOT NULL) "
            "ORDER BY seq ASC LIMIT ? OFFSET ?",
            (batch_size, page * batch_size),
        ).fetchall()
        if not rows:
            break
        page += 1

        updates = []
        for row in rows:
            rid = row["id"]
            req_body = None
            resp_body = None

            if row["request_body_ref"]:
                req_body = _get_body_for_record(
                    conn, rid, "request", manifest, bodies_path
                )
                if req_body is None:
                    skipped += 1

            if row["response_body_ref"]:
                resp_body = _get_body_for_record(
                    conn, rid, "response", manifest, bodies_path
                )
                if resp_body is None:
                    skipped += 1

            if req_body is not None or resp_body is not None:
                updates.append((req_body, resp_body, rid))

        # Batch UPDATE
        if updates:
            try:
                conn.executemany(
                    "UPDATE raw_requests SET request_body = ?, response_body = ? WHERE id = ?",
                    updates,
                )
                conn.commit()
            except Exception as e:
                logger.error("Batch update failed at offset %d: %s", page * batch_size, e)
                errors += len(updates)
                continue

        migrated += len(updates)

        pct = migrated * 100 / total if total else 0
        elapsed = time.monotonic() - t0
        rate = migrated / elapsed if elapsed else 0
        logger.info(
            "Progress: %d/%d (%.1f%%) | errors=%d skipped=%d | %.0f rec/s",
            migrated, total, pct, errors, skipped, rate,
        )

    elapsed = time.monotonic() - t0
    logger.info("=" * 60)
    logger.info("Migration complete!")
    logger.info("  Migrated: %d records", migrated)
    logger.info("  Errors:   %d", errors)
    logger.info("  Skipped:  %d (ref not in manifest or shard missing)", skipped)
    logger.info("  Elapsed:  %.1fs", elapsed)
    logger.info("  Rate:     %.0f rec/s", migrated / elapsed if elapsed else 0)

    # 5. Verify
    remaining = conn.execute(
        "SELECT COUNT(*) AS c FROM raw_requests "
        "WHERE (request_body_ref IS NOT NULL AND request_body IS NULL) "
        "OR (response_body_ref IS NOT NULL AND response_body IS NULL)"
    ).fetchone()["c"]
    if remaining:
        logger.warning(
            "%d records still have missing body data (ref without inline body). "
            "These may be from deleted shard files.",
            remaining,
        )
    else:
        logger.info("All bodies accounted for. ✓")

    conn.close()


if __name__ == "__main__":
    if len(sys.argv) not in (3, 4):
        print("Usage: python scripts/migrate_bodies_to_db.py <raw.db> <bodies_dir>")
        print("Example: python scripts/migrate_bodies_to_db.py /data/logs/raw.db /data/logs/bodies")
        sys.exit(1)

    raw_db = sys.argv[1]
    bodies_dir = sys.argv[2]
    migrate(bodies_dir, raw_db)
