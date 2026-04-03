from __future__ import annotations

import json
import logging
from pathlib import Path

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

    def _read_via_manifest(self, ref: str, manifest_path: Path) -> str | None:
        """Look up ref in manifest and read at specified offset."""
        try:
            for line in manifest_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                entry = json.loads(line)
                if entry.get("ref") != ref:
                    continue
                shard_file = self.bodies_dir / entry["file"]
                if not shard_file.exists():
                    continue
                with open(shard_file, "rb") as f:
                    f.seek(entry["offset"])
                    raw = f.read(entry["length"])
                record = json.loads(raw.decode("utf-8"))
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
                    record = json.loads(line)
                    if record.get("ref") == ref:
                        return record.get("data")
            except Exception as e:
                logger.debug("Error scanning %s: %s", jsonl_file, e)
        return None
