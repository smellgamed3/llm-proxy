"""Tests for BodyReader."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from analyzer.body_reader import BodyReader
from app.config import Config
from app.recorder import Recorder


@pytest.fixture
def recorder(tmp_path: Path) -> Recorder:
    cfg = Config(log_dir=str(tmp_path / "logs"))
    return Recorder(cfg)


class TestBodyReader:
    def test_read_existing_ref(self, recorder: Recorder):
        rid = recorder.new_request_id()
        recorder.record_request(rid, "POST", "/v1/chat", "", {}, b'{"hello": "world"}')
        
        reader = BodyReader(str(recorder.bodies_dir))
        ref = f"{rid}:request"
        content = reader.read(ref)
        assert content is not None
        assert "hello" in content

    def test_read_missing_ref_returns_none(self, recorder: Recorder):
        reader = BodyReader(str(recorder.bodies_dir))
        assert reader.read("nonexistent:request") is None

    def test_read_from_empty_dir(self, tmp_path: Path):
        reader = BodyReader(str(tmp_path / "bodies"))
        assert reader.read("any:ref") is None

    def test_read_multiple_refs(self, recorder: Recorder):
        ids = []
        for i in range(3):
            rid = recorder.new_request_id()
            recorder.record_request(rid, "POST", "/v1/chat", "", {}, f'body {i}'.encode())
            ids.append(rid)

        reader = BodyReader(str(recorder.bodies_dir))
        for i, rid in enumerate(ids):
            content = reader.read(f"{rid}:request")
            assert content is not None
            assert f"body {i}" in content

    def test_manifest_used_for_lookup(self, recorder: Recorder):
        rid = recorder.new_request_id()
        recorder.record_request(rid, "POST", "/v1/chat", "", {}, b'manifest test body')

        manifest = recorder.bodies_dir / "manifest.jsonl"
        assert manifest.exists()

        reader = BodyReader(str(recorder.bodies_dir))
        content = reader.read(f"{rid}:request")
        assert content == "manifest test body"

    def test_fallback_scan_when_manifest_missing(self, recorder: Recorder):
        rid = recorder.new_request_id()
        recorder.record_request(rid, "POST", "/v1/chat", "", {}, b'fallback test')

        # Remove manifest to force fallback
        manifest = recorder.bodies_dir / "manifest.jsonl"
        manifest.unlink()

        reader = BodyReader(str(recorder.bodies_dir))
        content = reader.read(f"{rid}:request")
        assert content is not None
        assert "fallback test" in content
