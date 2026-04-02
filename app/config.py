from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

import yaml


@dataclass
class FilterRule:
    """A single path filter rule."""
    pattern: str
    regex: bool = False
    _compiled: re.Pattern | None = field(default=None, repr=False, compare=False)

    def matches(self, path: str) -> bool:
        if self.regex:
            if self._compiled is None:
                self._compiled = re.compile(self.pattern)
            return bool(self._compiled.search(path))
        return path.startswith(self.pattern)


@dataclass
class RecordingFilter:
    """Path-based recording filter configuration.

    Logic:
    - If `include` is non-empty, ONLY paths matching include rules are recorded.
    - If `exclude` is non-empty, paths matching exclude rules are NOT recorded.
    - `include` is evaluated first; `exclude` is then applied as override.
    """
    include: List[FilterRule] = field(default_factory=list)
    exclude: List[FilterRule] = field(default_factory=list)

    def should_record(self, path: str) -> bool:
        # If include rules exist, path must match at least one
        if self.include:
            if not any(rule.matches(path) for rule in self.include):
                return False
        # If path matches any exclude rule, skip recording
        if any(rule.matches(path) for rule in self.exclude):
            return False
        return True


@dataclass
class Config:
    upstream_url: str = "http://localhost:8080"
    listen_host: str = "0.0.0.0"
    listen_port: int = 9090
    log_dir: str = "/data/logs"
    log_level: str = "INFO"
    max_body_log_size: int = 10 * 1024 * 1024  # 10MB
    preserve_host: bool = True
    recording_filter: RecordingFilter = field(default_factory=RecordingFilter)

    def __post_init__(self):
        try:
            Path(self.log_dir).mkdir(parents=True, exist_ok=True)
        except OSError as e:
            import warnings
            warnings.warn(f"Could not create log directory '{self.log_dir}': {e}")


def _parse_filter_rules(raw: list | None) -> List[FilterRule]:
    if not raw:
        return []
    rules = []
    for item in raw:
        if isinstance(item, str):
            rules.append(FilterRule(pattern=item))
        elif isinstance(item, dict):
            rules.append(FilterRule(
                pattern=item["pattern"],
                regex=item.get("regex", False),
            ))
    return rules


def _parse_bool(value: str | bool | None, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return value.strip().lower() in {"1", "true", "yes", "on"}


def load_config(config_path: str | None = None) -> Config:
    """Load configuration from environment variables + optional YAML config file."""
    cfg = Config(
        upstream_url=os.getenv("UPSTREAM_URL", Config.upstream_url),
        listen_host=os.getenv("LISTEN_HOST", Config.listen_host),
        listen_port=int(os.getenv("LISTEN_PORT", str(Config.listen_port))),
        log_dir=os.getenv("LOG_DIR", Config.log_dir),
        log_level=os.getenv("LOG_LEVEL", Config.log_level),
        max_body_log_size=int(os.getenv("MAX_BODY_LOG_SIZE", str(Config.max_body_log_size))),
        preserve_host=_parse_bool(os.getenv("PRESERVE_HOST"), Config.preserve_host),
    )

    # Load YAML config file for filter rules and overrides
    config_file = config_path or os.getenv("CONFIG_FILE", "/etc/llm-proxy/config.yaml")
    p = Path(config_file)
    if p.exists():
        with open(p) as f:
            data = yaml.safe_load(f) or {}

        # Override basic settings if present in YAML
        if "upstream_url" in data:
            cfg.upstream_url = data["upstream_url"]
        if "listen_port" in data:
            cfg.listen_port = int(data["listen_port"])
        if "log_dir" in data:
            cfg.log_dir = data["log_dir"]
        if "log_level" in data:
            cfg.log_level = data["log_level"]
        if "max_body_log_size" in data:
            cfg.max_body_log_size = int(data["max_body_log_size"])
        if "preserve_host" in data:
            cfg.preserve_host = _parse_bool(data["preserve_host"], cfg.preserve_host)

        # Parse recording filter
        rec = data.get("recording", {})
        cfg.recording_filter = RecordingFilter(
            include=_parse_filter_rules(rec.get("include")),
            exclude=_parse_filter_rules(rec.get("exclude")),
        )

    # Ensure upstream_url has no trailing slash
    cfg.upstream_url = cfg.upstream_url.rstrip("/")

    return cfg
