from __future__ import annotations

import logging
from pathlib import Path

import orjson

logger = logging.getLogger("analyzer.body_reader")


class BodyReader:
    """Reads body content from JSONL shards in the bodies directory."""

    def __init__(self, bodies_dir: str):
        self.bodies_dir = Path(bodies_dir)

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
    # Single-ref helpers (original)
    # ------------------------------------------------------------------

    def _read_via_manifest(self, ref: str, manifest_path: Path) -> str | None:
        """Look up ref in manifest and read at specified offset."""
        try:
            for line in manifest_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                entry = orjson.loads(line)
                if entry.get("ref") != ref:
                    continue
                shard_file = self.bodies_dir / entry["file"]
                if not shard_file.exists():
                    continue
                with open(shard_file, "rb") as f:
                    f.seek(entry["offset"])
                    raw = f.read(entry["length"])
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
        """Look up multiple refs via manifest, grouped by shard file."""
        try:
            file_entries: dict[str, list[tuple[str, int, int]]] = {}
            for line in manifest_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                entry = orjson.loads(line)
                ref = entry.get("ref")
                if ref in ref_set:
                    fname = entry["file"]
                    file_entries.setdefault(fname, []).append(
                        (ref, entry["offset"], entry["length"])
                    )

            for fname, entries in file_entries.items():
                shard_file = self.bodies_dir / fname
                if not shard_file.exists():
                    continue
                with open(shard_file, "rb") as f:
                    for ref, offset, length in entries:
                        f.seek(offset)
                        raw = f.read(length)
                        record = orjson.loads(raw)
                        results[ref] = record.get("data")
        except Exception as e:
            logger.debug("Batch manifest read failed: %s", e)

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
