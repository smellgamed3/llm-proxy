"""Tests for config loading and recording filter logic."""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from app.config import Config, FilterRule, RecordingFilter, load_config


class TestFilterRule:
    def test_prefix_match(self):
        r = FilterRule("/v1/chat")
        assert r.matches("/v1/chat/completions")
        assert r.matches("/v1/chat")
        assert not r.matches("/v1/embeddings")
        assert not r.matches("/health")

    def test_regex_match(self):
        r = FilterRule(r"^/v1/(chat|embeddings)", regex=True)
        assert r.matches("/v1/chat/completions")
        assert r.matches("/v1/embeddings")
        assert not r.matches("/v1/models")
        assert not r.matches("/health")

    def test_regex_compiled_cached(self):
        r = FilterRule(r"^/v1/", regex=True)
        r.matches("/v1/a")
        compiled = r._compiled
        r.matches("/v1/b")
        assert r._compiled is compiled  # same object


class TestRecordingFilter:
    def test_no_rules_records_everything(self):
        f = RecordingFilter()
        assert f.should_record("/anything")
        assert f.should_record("/v1/chat/completions")
        assert f.should_record("/health")

    def test_include_restricts_to_matching(self):
        f = RecordingFilter(include=[FilterRule("/v1/chat")])
        assert f.should_record("/v1/chat/completions")
        assert not f.should_record("/v1/embeddings")
        assert not f.should_record("/health")

    def test_exclude_blocks_matching(self):
        f = RecordingFilter(exclude=[FilterRule("/health"), FilterRule("/metrics")])
        assert f.should_record("/v1/chat/completions")
        assert not f.should_record("/health")
        assert not f.should_record("/metrics")

    def test_exclude_overrides_include(self):
        """A path matching both include and exclude must NOT be recorded."""
        f = RecordingFilter(
            include=[FilterRule("/v1")],
            exclude=[FilterRule("/v1/internal")],
        )
        assert f.should_record("/v1/chat")
        assert not f.should_record("/v1/internal/debug")

    def test_regex_exclude(self):
        f = RecordingFilter(exclude=[FilterRule(r"^/internal/", regex=True)])
        assert not f.should_record("/internal/state")
        assert f.should_record("/v1/chat")

    def test_multiple_include_rules(self):
        f = RecordingFilter(include=[FilterRule("/v1/chat"), FilterRule("/v1/embeddings")])
        assert f.should_record("/v1/chat/completions")
        assert f.should_record("/v1/embeddings")
        assert not f.should_record("/v1/models")


class TestLoadConfig:
    def test_defaults(self, tmp_path: Path):
        cfg = load_config(config_path=str(tmp_path / "nonexistent.yaml"))
        assert cfg.upstream_url == "http://localhost:8080"
        assert cfg.listen_port == 9090
        assert cfg.log_level == "INFO"

    def test_yaml_overrides(self, tmp_path: Path):
        yaml_file = tmp_path / "config.yaml"
        yaml_file.write_text(textwrap.dedent("""
            upstream_url: http://my-upstream:1234
            log_level: DEBUG
            recording:
              include:
                - /v1/chat
              exclude:
                - /health
                - pattern: "^/internal/"
                  regex: true
        """))
        cfg = load_config(config_path=str(yaml_file))
        assert cfg.upstream_url == "http://my-upstream:1234"
        assert cfg.log_level == "DEBUG"
        assert len(cfg.recording_filter.include) == 1
        assert cfg.recording_filter.include[0].pattern == "/v1/chat"
        assert len(cfg.recording_filter.exclude) == 2
        assert cfg.recording_filter.exclude[1].regex is True

    def test_trailing_slash_stripped(self, tmp_path: Path):
        yaml_file = tmp_path / "config.yaml"
        yaml_file.write_text("upstream_url: http://upstream/\n")
        cfg = load_config(config_path=str(yaml_file))
        assert not cfg.upstream_url.endswith("/")

    def test_log_dir_created(self, tmp_path: Path):
        log_dir = tmp_path / "nested" / "logs"
        cfg = Config(log_dir=str(log_dir))
        assert log_dir.exists()
