from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class AnalyzerConfig:
    raw_db: str = "/data/logs/raw.db"
    analytics_db: str = "/data/analytics/analytics.db"
    bodies_dir: str = "/data/logs/bodies"
    pricing_file: str = "/etc/llm-proxy/pricing.yaml"
    interval: int = 5  # seconds between incremental polls
    batch_size: int = 100
    mode: str = "incremental"  # incremental / full / range
    since: str | None = None   # ISO date for range mode
    until: str | None = None   # ISO date for range mode


def load_analyzer_config() -> AnalyzerConfig:
    return AnalyzerConfig(
        raw_db=os.getenv("RAW_DB", "/data/logs/raw.db"),
        analytics_db=os.getenv("ANALYTICS_DB", "/data/analytics/analytics.db"),
        bodies_dir=os.getenv("BODIES_DIR", "/data/logs/bodies"),
        pricing_file=os.getenv("PRICING_FILE", "/etc/llm-proxy/pricing.yaml"),
        interval=int(os.getenv("ANALYZER_INTERVAL", "5")),
        batch_size=int(os.getenv("ANALYZER_BATCH_SIZE", "100")),
    )
