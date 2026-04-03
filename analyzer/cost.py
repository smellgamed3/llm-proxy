from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger("analyzer.cost")


class CostCalculator:
    """Calculates request cost based on token usage and pricing configuration."""

    def __init__(self, pricing_file: str):
        self.pricing_file = Path(pricing_file)
        self._pricing: dict = {}
        self._mtime: float = 0.0
        self._maybe_reload()

    def calculate(
        self,
        model: str | None,
        prompt_tokens: int | None,
        completion_tokens: int | None,
    ) -> float | None:
        """Returns cost in USD or None if token info is unavailable."""
        if prompt_tokens is None and completion_tokens is None:
            return None

        self._maybe_reload()
        models = self._pricing.get("models", {})
        default = self._pricing.get("default", {})

        pricing = models.get(model) if model else None
        if pricing is None:
            pricing = default
        if not pricing:
            return None

        input_rate = pricing.get("input_per_1m", 0.0)
        output_rate = pricing.get("output_per_1m", 0.0)

        cost = 0.0
        if prompt_tokens:
            cost += (prompt_tokens / 1_000_000) * input_rate
        if completion_tokens:
            cost += (completion_tokens / 1_000_000) * output_rate

        return round(cost, 8)

    def _maybe_reload(self) -> None:
        """Hot reload: re-read file if mtime has changed."""
        if not self.pricing_file.exists():
            return
        try:
            mtime = self.pricing_file.stat().st_mtime
            if mtime <= self._mtime:
                return
            with open(self.pricing_file, "r", encoding="utf-8") as f:
                self._pricing = yaml.safe_load(f) or {}
            self._mtime = mtime
            logger.debug("Loaded pricing from %s", self.pricing_file)
        except Exception as e:
            logger.warning("Failed to load pricing file %s: %s", self.pricing_file, e)
