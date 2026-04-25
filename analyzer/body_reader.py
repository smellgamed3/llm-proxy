from __future__ import annotations

import logging
import time
from pathlib import Path

import orjson

logger = logging.getLogger("analyzer.body_reader")

_MAX_OPEN_FILES = 50
_FILE_IDLE_TIMEOUT = 30.0


class BodyReader:
    """Reads body content from JSONL shards in the bodies directory.

    关键优化：跨 batch 文件句柄缓存。

    每次 batch 处理时打开 shard 文件 → seek → read → close，
    但在 Docker FUSE 上每次 open/close 都是昂贵的 gRPC 远程调用。
    优化：保持文件句柄打开，后续 batch 继续复用，节省 open 开销。

    Docker for Mac FUSE 延迟对比（估算）：
    - 原方案 196k 次 open+seek+read+close → 大量 FUSE 操作
    - 句柄缓存后 421 次 open + 98k 次 seek+read → 显著减少 FUSE 调用
    """

    def __init__(self, bodies_dir: str):
        self.bodies_dir = Path(bodies_dir)
        self._manifest_cache: dict[str, tuple[str, int, int]] | None = None
        self._open_handles: dict[str, tuple[object, float]] = {}
        """``{fname: (file_obj, last_access_time)}``"""

    def read(self, ref: str) -> str | None:
        """Read body content by ref."""
        if not self.bodies_dir.exists():
            return None
        manifest_path = self.bodies_dir / "manifest.jsonl"
        if manifest_path.exists():
            result = self._read_via_manifest(ref, manifest_path)
            if result is not None:
                return result
        return self._scan_all(ref)

    def read_batch(self, refs: list[str]) -> dict[str, str | None]:
        """Read multiple body refs using cached file handles."""
        if not refs or not self.bodies_dir.exists():
            return {ref: None for ref in refs}

        ref_set = set(refs)
        results: dict[str, str | None] = {ref: None for ref in refs}

        manifest_path = self.bodies_dir / "manifest.jsonl"
        if manifest_path.exists():
            self._read_batch_via_manifest(ref_set, results, manifest_path)
            ref_set = {r for r in ref_set if results[r] is None}
            if not ref_set:
                return results
        if ref_set:
            self._scan_all_batch(ref_set, results)
        return results

    def close(self) -> None:
        """Close all cached file handles."""
        for handle, _ in self._open_handles.values():
            try:
                handle.close()
            except Exception:
                pass
        self._open_handles.clear()

    # ------------------------------------------------------------------
    # File handle cache
    # ------------------------------------------------------------------

    def _get_handle(self, fname: str) -> object | None:
        """获取 shard 文件句柄，优先从缓存取。

        句柄在 ``close()`` 或 idle 超时后自动关闭。跨 batch 复用
        大幅降低 Docker FUSE open/close 开销。
        """
        now = time.monotonic()

        if fname in self._open_handles:
            handle, _ = self._open_handles[fname]
            self._open_handles[fname] = (handle, now)
            return handle

        # 清理过期空闲句柄
        stale = [
            fn for fn, (_, atime) in self._open_handles.items()
            if now - atime > _FILE_IDLE_TIMEOUT
        ]
        for fn in stale:
            try:
                self._open_handles[fn][0].close()
            except Exception:
                pass
            del self._open_handles[fn]

        # LRU 淘汰
        while len(self._open_handles) >= _MAX_OPEN_FILES:
            oldest_fn = min(self._open_handles, key=lambda fn: self._open_handles[fn][1])
            try:
                self._open_handles[oldest_fn][0].close()
            except Exception:
                pass
            del self._open_handles[oldest_fn]

        shard_file = self.bodies_dir / fname
        if not shard_file.exists():
            return None
        try:
            handle = open(shard_file, "rb")
            self._open_handles[fname] = (handle, now)
            return handle
        except Exception as e:
            logger.debug("Failed to open %s: %s", fname, e)
            return None

    # ------------------------------------------------------------------
    # Manifest cache
    # ------------------------------------------------------------------

    def _ensure_manifest_cache(self) -> dict[str, tuple[str, int, int]]:
        if self._manifest_cache is not None:
            return self._manifest_cache
        cache: dict[str, tuple[str, int, int]] = {}
        manifest_path = self.bodies_dir / "manifest.jsonl"
        if not manifest_path.exists():
            self._manifest_cache = cache
            return cache
        try:
            for line in manifest_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                entry = orjson.loads(line)
                ref = entry.get("ref")
                cache[ref] = (entry["file"], entry["offset"], entry["length"])
        except Exception as e:
            logger.debug("Failed to build manifest cache: %s", e)
        self._manifest_cache = cache
        logger.info("Built manifest cache: %d entries", len(cache))
        return cache

    def invalidate_manifest_cache(self) -> None:
        self._manifest_cache = None

    # ------------------------------------------------------------------
    # Single-ref read
    # ------------------------------------------------------------------

    def _read_via_manifest(self, ref: str, manifest_path: Path) -> str | None:
        cache = self._ensure_manifest_cache()
        entry = cache.get(ref)
        if entry is None:
            return None
        fname, offset, length = entry
        handle = self._get_handle(fname)
        if handle is None:
            return None
        try:
            handle.seek(offset)
            raw = handle.read(length)
            record = orjson.loads(raw)
            return record.get("data")
        except Exception as e:
            logger.debug("Manifest read failed for ref %s: %s", ref, e)
            return None

    def _scan_all(self, ref: str) -> str | None:
        for jsonl_file in sorted(self.bodies_dir.glob("*.jsonl")):
            if jsonl_file.name == "manifest.jsonl":
                continue
            try:
                for line in jsonl_file.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    record = orjson.loads(line)
                    if record.get("ref") == ref:
                        return record.get("data")
            except Exception as e:
                logger.debug("Error scanning %s: %s", jsonl_file, e)
        return None

    # ------------------------------------------------------------------
    # Batch helpers
    # ------------------------------------------------------------------

    def _read_batch_via_manifest(
        self,
        ref_set: set[str],
        results: dict[str, str | None],
        manifest_path: Path,
    ) -> None:
        """批量读取 body，使用缓存句柄跨 batch 复用。

        核心优化：同一个 shard 文件在多个 batch 间保持打开状态，
        避免 Docker FUSE 上每次 open/close 的 gRPC 开销。
        """
        cache = self._ensure_manifest_cache()
        file_entries: dict[str, list[tuple[str, int, int]]] = {}
        for ref in ref_set:
            entry = cache.get(ref)
            if entry is not None:
                fname, offset, length = entry
                file_entries.setdefault(fname, []).append((ref, offset, length))

        for fname, entries in file_entries.items():
            handle = self._get_handle(fname)
            if handle is None:
                continue
            try:
                for ref, offset, length in entries:
                    handle.seek(offset)
                    raw = handle.read(length)
                    record = orjson.loads(raw)
                    results[ref] = record.get("data")
            except Exception as e:
                logger.debug("Batch manifest read failed for %s: %s", fname, e)

    def _scan_all_batch(
        self,
        ref_set: set[str],
        results: dict[str, str | None],
    ) -> None:
        remaining = set(ref_set)
        for jsonl_file in sorted(self.bodies_dir.glob("*.jsonl")):
            if jsonl_file.name == "manifest.jsonl":
                continue
            if not remaining:
                break
            try:
                for line in jsonl_file.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    if not any(r in line for r in remaining):
                        continue
                    record = orjson.loads(line)
                    actual_ref = record.get("ref")
                    if actual_ref in remaining:
                        results[actual_ref] = record.get("data")
                        remaining.discard(actual_ref)
                        if not remaining:
                            break
            except Exception as e:
                logger.debug("Error scanning %s: %s", jsonl_file, e)
