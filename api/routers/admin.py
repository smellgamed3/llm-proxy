from __future__ import annotations

import os
import shutil
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from analyzer.config import load_analyzer_config
from analyzer.worker import AnalyzerWorker
from analyzer.store import AnalyticsStore
from api.dependencies import get_analytics_db, get_raw_db

router = APIRouter(tags=["admin"])


class RerunRequest(BaseModel):
    mode: Literal["incremental", "full", "range"] = "incremental"
    since: str | None = None
    until: str | None = None


class AnalyzerSyncManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._store = AnalyticsStore(os.getenv("ANALYTICS_DB", "/data/analytics/analytics.db"))
        self._state = self._new_state()

    def _new_state(self) -> dict[str, Any]:
        return {
            "status": "idle",
            "is_running": False,
            "job_id": None,
            "mode": None,
            "since": None,
            "until": None,
            "progress": 0.0,
            "processed_rows": 0,
            "total_rows": 0,
            "remaining_rows": 0,
            "current_seq": 0,
            "target_seq": 0,
            "last_timestamp": None,
            "started_at": None,
            "finished_at": None,
            "error": None,
            "stop_requested": False,
        }

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._state)

    def start(self, request: RerunRequest) -> dict[str, Any]:
        with self._lock:
            if self._state["is_running"]:
                raise RuntimeError("analyzer sync already running")

            started_at = datetime.now(timezone.utc).isoformat()
            self._stop_event.clear()
            self._state = self._new_state()
            job_id = self._store.create_sync_job(
                mode=request.mode,
                since=request.since,
                until=request.until,
                status="running",
                started_at=started_at,
            )
            self._state.update(
                {
                    "status": "running",
                    "is_running": True,
                    "job_id": job_id,
                    "mode": request.mode,
                    "since": request.since,
                    "until": request.until,
                    "started_at": started_at,
                }
            )
            payload = request.model_dump()
            self._thread = threading.Thread(
                target=self._run_job,
                args=(payload,),
                name="analyzer-sync",
                daemon=True,
            )
            self._thread.start()
            return dict(self._state)

    def _run_job(self, payload: dict[str, Any]) -> None:
        request = RerunRequest(**payload)
        try:
            config = load_analyzer_config()
            config.mode = request.mode
            config.since = request.since
            config.until = request.until
            result = AnalyzerWorker(
                config,
                progress_callback=self._update_progress,
                stop_requested=self._stop_event.is_set,
            ).run_once()
            with self._lock:
                total_rows = int(result.get("total_rows") or self._state.get("total_rows") or 0)
                processed_rows = int(result.get("processed") or self._state.get("processed_rows") or 0)
                remaining_rows = max(total_rows - processed_rows, 0)
                stopped = bool(result.get("stopped"))
                final_status = "stopped" if stopped else "completed"
                progress = 1.0 if total_rows == 0 and not stopped else (0.0 if total_rows == 0 else min(processed_rows / total_rows, 1.0))
                finished_at = datetime.now(timezone.utc).isoformat()
                self._state.update(
                    {
                        "status": final_status,
                        "is_running": False,
                        "progress": progress,
                        "processed_rows": processed_rows,
                        "total_rows": total_rows,
                        "remaining_rows": remaining_rows,
                        "current_seq": int(result.get("last_seq") or self._state.get("current_seq") or 0),
                        "target_seq": int(result.get("target_seq") or self._state.get("target_seq") or 0),
                        "finished_at": finished_at,
                        "error": None,
                    }
                )
                if self._state["job_id"] is not None:
                    self._store.update_sync_job(
                        int(self._state["job_id"]),
                        status=final_status,
                        progress=progress,
                        processed_rows=processed_rows,
                        total_rows=total_rows,
                        remaining_rows=remaining_rows,
                        current_seq=self._state["current_seq"],
                        target_seq=self._state["target_seq"],
                        last_timestamp=self._state.get("last_timestamp"),
                        finished_at=finished_at,
                        error=None,
                        stop_requested=1 if self._state.get("stop_requested") else 0,
                    )
        except Exception as exc:
            with self._lock:
                finished_at = datetime.now(timezone.utc).isoformat()
                self._state.update(
                    {
                        "status": "failed",
                        "is_running": False,
                        "finished_at": finished_at,
                        "error": str(exc),
                    }
                )
                if self._state["job_id"] is not None:
                    self._store.update_sync_job(
                        int(self._state["job_id"]),
                        status="failed",
                        progress=self._state.get("progress") or 0.0,
                        processed_rows=self._state.get("processed_rows") or 0,
                        total_rows=self._state.get("total_rows") or 0,
                        remaining_rows=self._state.get("remaining_rows") or 0,
                        current_seq=self._state.get("current_seq") or 0,
                        target_seq=self._state.get("target_seq") or 0,
                        last_timestamp=self._state.get("last_timestamp"),
                        finished_at=finished_at,
                        error=str(exc),
                        stop_requested=1 if self._state.get("stop_requested") else 0,
                    )

    def stop(self) -> dict[str, Any]:
        with self._lock:
            if not self._state["is_running"]:
                raise RuntimeError("no analyzer sync is running")
            self._stop_event.set()
            self._state.update(
                {
                    "status": "stopping",
                    "stop_requested": True,
                }
            )
            if self._state["job_id"] is not None:
                self._store.update_sync_job(
                    int(self._state["job_id"]),
                    status="stopping",
                    stop_requested=1,
                )
            return dict(self._state)

    def _update_progress(self, payload: dict[str, Any]) -> None:
        with self._lock:
            total_rows = int(payload.get("total_rows") or 0)
            processed_rows = int(payload.get("processed_rows") or 0)
            remaining_rows = max(total_rows - processed_rows, 0)
            progress = 0.0 if total_rows <= 0 else min(processed_rows / total_rows, 1.0)
            self._state.update(
                {
                    "progress": progress,
                    "processed_rows": processed_rows,
                    "total_rows": total_rows,
                    "remaining_rows": remaining_rows,
                    "current_seq": int(payload.get("current_seq") or 0),
                    "target_seq": int(payload.get("target_seq") or 0),
                    "last_timestamp": payload.get("last_timestamp"),
                }
            )
            if self._state["job_id"] is not None:
                self._store.update_sync_job(
                    int(self._state["job_id"]),
                    status=self._state["status"],
                    progress=progress,
                    processed_rows=processed_rows,
                    total_rows=total_rows,
                    remaining_rows=remaining_rows,
                    current_seq=self._state["current_seq"],
                    target_seq=self._state["target_seq"],
                    last_timestamp=self._state.get("last_timestamp"),
                    stop_requested=1 if self._state.get("stop_requested") else 0,
                )

    def history(self, limit: int = 20) -> list[dict[str, Any]]:
        return self._store.list_sync_jobs(limit=limit)

    def retry(self, job_id: int) -> dict[str, Any]:
        """Start a new sync job reusing params from a previous (non-running) job."""
        with self._lock:
            if self._state["is_running"]:
                raise RuntimeError("another analyzer sync is already running")
        job = self._store.get_sync_job(job_id)
        if job is None:
            raise KeyError(f"job {job_id} not found")
        if job["status"] in {"running", "stopping"}:
            raise RuntimeError(f"job {job_id} is still active; stop it first")
        req = RerunRequest(
            mode=job["mode"] or "incremental",
            since=job["since"],
            until=job["until"],
        )
        return self.start(req)


def get_sync_manager(request: Request) -> AnalyzerSyncManager:
    return request.app.state.analyzer_sync_manager


def _file_meta(path_str: str) -> dict[str, Any]:
    path = Path(path_str)
    exists = path.exists()
    size_bytes = path.stat().st_size if exists else 0
    return {
        "path": path_str,
        "exists": exists,
        "file_size_bytes": size_bytes,
    }


def _build_status(
    analytics_db: sqlite3.Connection,
    raw_db: sqlite3.Connection,
    sync_manager: AnalyzerSyncManager,
) -> dict[str, Any]:
    wm = analytics_db.execute("SELECT * FROM watermark WHERE id = 1").fetchone()
    conv_count = analytics_db.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
    template_count = analytics_db.execute("SELECT COUNT(*) FROM prompt_templates").fetchone()[0]
    analytics_daily = analytics_db.execute("SELECT COUNT(*) FROM daily_stats").fetchone()[0]
    latest_analytics_ts = analytics_db.execute(
        "SELECT MAX(timestamp) FROM conversations"
    ).fetchone()[0]

    raw_summary = raw_db.execute(
        """
        SELECT
            COUNT(*) AS total_rows,
            SUM(CASE WHEN status_code IS NOT NULL THEN 1 ELSE 0 END) AS finalized_rows,
            SUM(CASE WHEN status_code IS NULL THEN 1 ELSE 0 END) AS pending_rows,
            SUM(CASE WHEN status_code >= 400 OR error IS NOT NULL THEN 1 ELSE 0 END) AS error_rows,
            MIN(timestamp) AS first_timestamp,
            MAX(timestamp) AS last_timestamp,
            MAX(seq) AS max_seq,
            AVG(COALESCE(duration_ms, 0)) AS avg_duration_ms,
            SUM(COALESCE(request_body_size, 0) + COALESCE(response_body_size, 0)) AS payload_bytes
        FROM raw_requests
        """
    ).fetchone()
    backlog_rows = raw_db.execute(
        "SELECT COUNT(*) FROM raw_requests WHERE seq > ? AND status_code IS NOT NULL",
        (wm["seq"] if wm else 0,),
    ).fetchone()[0]

    raw_path = os.getenv("RAW_DB", "/data/logs/raw.db")
    analytics_path = os.getenv("ANALYTICS_DB", "/data/analytics/analytics.db")
    raw_db_status = {
        **_file_meta(raw_path),
        "total_rows": raw_summary["total_rows"] or 0,
        "finalized_rows": raw_summary["finalized_rows"] or 0,
        "pending_rows": raw_summary["pending_rows"] or 0,
        "error_rows": raw_summary["error_rows"] or 0,
        "backlog_rows": backlog_rows,
        "first_timestamp": raw_summary["first_timestamp"],
        "last_timestamp": raw_summary["last_timestamp"],
        "max_seq": raw_summary["max_seq"] or 0,
        "avg_duration_ms": round(raw_summary["avg_duration_ms"] or 0.0, 2),
        "payload_bytes": raw_summary["payload_bytes"] or 0,
    }
    analytics_db_status = {
        **_file_meta(analytics_path),
        "conversation_count": conv_count,
        "template_count": template_count,
        "daily_stats_rows": analytics_daily,
        "watermark_seq": wm["seq"] if wm else 0,
        "records_processed": wm["processed"] if wm else 0,
        "last_updated_at": wm["updated_at"] if wm else None,
        "latest_conversation_timestamp": latest_analytics_ts,
    }
    return {
        "watermark_seq": wm["seq"] if wm else 0,
        "records_processed": wm["processed"] if wm else 0,
        "conversation_count": conv_count,
        "template_count": template_count,
        "raw_db": raw_db_status,
        "analytics_db": analytics_db_status,
        "worker": sync_manager.snapshot(),
    }


@router.get("/admin/status")
@router.get("/admin/analyzer/status")
def get_status(
    analytics_db: sqlite3.Connection = Depends(get_analytics_db),
    raw_db: sqlite3.Connection = Depends(get_raw_db),
    sync_manager: AnalyzerSyncManager = Depends(get_sync_manager),
) -> dict[str, Any]:
    """Return analytics system status."""
    return _build_status(analytics_db, raw_db, sync_manager)


@router.get("/admin/analyzer/job")
def get_analyzer_job(
    sync_manager: AnalyzerSyncManager = Depends(get_sync_manager),
) -> dict[str, Any]:
    return sync_manager.snapshot()


@router.get("/admin/analyzer/history")
def get_analyzer_history(
    limit: int = 20,
    sync_manager: AnalyzerSyncManager = Depends(get_sync_manager),
) -> list[dict[str, Any]]:
    return sync_manager.history(limit=limit)


@router.post("/admin/analyzer/stop")
def stop_analyzer_sync(
    sync_manager: AnalyzerSyncManager = Depends(get_sync_manager),
) -> dict[str, Any]:
    try:
        state = sync_manager.stop()
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return {
        "status": "stopping",
        "job": state,
    }


@router.post("/admin/reset")
def reset_analytics() -> dict:
    """Reset analytics database (clear all derived data)."""
    db_path = os.getenv("ANALYTICS_DB", "/data/analytics/analytics.db")
    store = AnalyticsStore(db_path)
    store.reset()
    return {"status": "reset complete"}


@router.post("/admin/analyzer/sync", status_code=status.HTTP_202_ACCEPTED)
def start_analyzer_sync(
    request: RerunRequest,
    sync_manager: AnalyzerSyncManager = Depends(get_sync_manager),
) -> dict[str, Any]:
    try:
        state = sync_manager.start(request)
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return {
        "status": "started",
        "job": state,
    }


@router.post("/admin/analyzer/rerun")
def rerun_analyzer(request: RerunRequest) -> dict:
    """Run analyzer in one-shot mode for full/range/incremental catch-up."""
    config = load_analyzer_config()
    config.mode = request.mode
    config.since = request.since
    config.until = request.until
    result = AnalyzerWorker(config).run_once()
    return {
        "status": "completed",
        "mode": request.mode,
        "processed": result["processed"],
        "last_seq": result["last_seq"],
        "since": request.since,
        "until": request.until,
    }


@router.post("/admin/analyzer/retry/{job_id}", status_code=status.HTTP_202_ACCEPTED)
def retry_analyzer_sync(
    job_id: int,
    sync_manager: AnalyzerSyncManager = Depends(get_sync_manager),
) -> dict[str, Any]:
    """Retry a previous sync job with the same parameters."""
    try:
        state = sync_manager.retry(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return {
        "status": "started",
        "job": state,
    }


# ── Backup ────────────────────────────────────────────────────────────────


def _backup_dir() -> Path:
    return Path(os.getenv("BACKUP_DIR", "/data/backups"))


def _create_backup() -> dict[str, Any]:
    raw_src = Path(os.getenv("RAW_DB", "/data/logs/raw.db"))
    analytics_src = Path(os.getenv("ANALYTICS_DB", "/data/analytics/analytics.db"))
    backup_dir = _backup_dir()
    backup_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    files: list[dict[str, Any]] = []
    for src in (raw_src, analytics_src):
        if not src.exists():
            continue
        dest = backup_dir / f"{src.stem}_{ts}.db"
        shutil.copy2(src, dest)
        files.append({"name": dest.name, "size_bytes": dest.stat().st_size})

    # Prune old backups, keep last 14 per database name
    _prune_backups(backup_dir, keep=14)

    return {"timestamp": ts, "files": files, "backup_dir": str(backup_dir)}


def _prune_backups(backup_dir: Path, keep: int = 14) -> None:
    for stem in ("raw", "analytics"):
        old = sorted(backup_dir.glob(f"{stem}_*.db"), reverse=True)
        for path in old[keep:]:
            try:
                path.unlink()
            except OSError:
                pass


def _list_backups() -> list[dict[str, Any]]:
    backup_dir = _backup_dir()
    if not backup_dir.exists():
        return []
    entries = []
    for f in sorted(backup_dir.glob("*.db"), reverse=True):
        entries.append({
            "name": f.name,
            "size_bytes": f.stat().st_size,
            "modified_at": datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc).isoformat(),
        })
    return entries


@router.post("/admin/backup")
def create_backup() -> dict[str, Any]:
    """Create a timestamped backup of raw.db and analytics.db."""
    return _create_backup()


@router.get("/admin/backups")
def list_backups() -> list[dict[str, Any]]:
    """List available backup files."""
    return _list_backups()
