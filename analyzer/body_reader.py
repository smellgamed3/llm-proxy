from __future__ import annotations

import logging
from pathlib import Path

import orjson

logger = logging.getLogger("analyzer.body_reader")


class BodyReader:
    """Reads body content from JSONL shards in the bodies directory."""

    def __init__(self, bodies_dir: str):
        self.bodies_dir = Path(bodies_dir)
        self._manifest_cache: dict[str, tuple[str, int, int]] | None = None
        """Cached manifest: ``{ref: (shard_filename, offset, length)}``.
        Built on first access and reused across all subsequent batch/single reads."""

    def read(self, ref: str) -> str | None:
        """Read body content by ref. Uses manifest first, falls back to scanning all JSONL files."""
        if not self.bodies_dir.exists():
            return None

        # Try manifest first
        manifest_path = self.bodies_dir / "manifest.jsonl"
        if manifest_path.exists():
            result = self._read_via_manifest(ref, manifest_path)
            if result is not None:
                return result

        # Fall back to scanning all JSONL shards
        return self._scan_all(ref)

    def read_batch(self, refs: list[str]) -> dict[str, str | None]:
        """Read multiple body refs efficiently in a single pass.

        Groups lookups by shard file to minimise repeated file I/O.
        Returns ``{ref: data_or_None}``.
        """
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

    # ------------------------------------------------------------------
    # Manifest cache management
    # ------------------------------------------------------------------

    def _ensure_manifest_cache(self) -> dict[str, tuple[str, int, int]]:
        """Build and cache the manifest index on first call.

        Parses manifest.jsonl once and stores ``{ref: (shard_filename, offset, length)}``
        in memory.  Subsequent lookups are O(1) dict lookups — no more
        full-scanning the 11 MB / 96K-line manifest every batch.
        """
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
        """Clear the cached manifest so the next read rebuilds it from disk.

        Call this when new bodies are written (e.g. after a checkpoint) and
        the manifest has grown.
        """
        self._manifest_cache = None

    # ------------------------------------------------------------------
    # Single-ref helpers (original)
    # ------------------------------------------------------------------

    def _read_via_manifest(self, ref: str, manifest_path: Path) -> str | None:
        """Look up ref in cached manifest and read at specified offset."""
        cache = self._ensure_manifest_cache()
        entry = cache.get(ref)
        if entry is None:
            return None
        fname, offset, length = entry
        shard_file = self.bodies_dir / fname
        if not shard_file.exists():
            return None
        try:
            with open(shard_file, "rb") as f:
                f.seek(offset)
                raw = f.read(length)
            record = orjson.loads(raw)
            return record.get("data")
        except Exception as e:
            logger.debug("Manifest read failed for ref %s: %s", ref, e)
            return None

    def _scan_all(self, ref: str) -> str | None:
        """Scan all JSONL shard files for the given ref."""
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
        """Look up multiple refs via cached manifest, grouped by shard file."""
        cache = self._ensure_manifest_cache()
        file_entries: dict[str, list[tuple[str, int, int]]] = {}
        for ref in ref_set:
            entry = cache.get(ref)
            if entry is not None:
                fname, offset, length = entry
                file_entries.setdefault(fname, []).append((ref, offset, length))

        for fname, entries in file_entries.items():
            shard_file = self.bodies_dir / fname
            if not shard_file.exists():
                continue
            try:
                with open(shard_file, "rb") as f:
                    for ref, offset, length in entries:
                        f.seek(offset)
                        raw = f.read(length)
                        record = orjson.loads(raw)
                        results[ref] = record.get("data")
            except Exception as e:
                logger.debug("Batch manifest read failed for %s: %s", fname, e)

    def _scan_all_batch(
        self,
        ref_set: set[str],
        results: dict[str, str | None],
    ) -> None:
        """Scan JSONL shards once, collecting all matching refs."""
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
                    # Quick substring check before full parse
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
