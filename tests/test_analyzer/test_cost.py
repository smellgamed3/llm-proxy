"""Tests for CostCalculator."""
from __future__ import annotations

import time
from pathlib import Path

import pytest
import yaml

from analyzer.cost import CostCalculator


@pytest.fixture
def pricing_file(tmp_path: Path) -> Path:
    p = tmp_path / "pricing.yaml"
    p.write_text(yaml.dump({
        "models": {
            "gpt-4o": {"input_per_1m": 2.50, "output_per_1m": 10.00},
            "gpt-4o-mini": {"input_per_1m": 0.15, "output_per_1m": 0.60},
        },
        "default": {"input_per_1m": 1.00, "output_per_1m": 5.00},
    }))
    return p


class TestCostCalculation:
    def test_known_model(self, pricing_file: Path):
        calc = CostCalculator(str(pricing_file))
        cost = calc.calculate("gpt-4o", prompt_tokens=1000, completion_tokens=500)
        # 1000/1M * 2.50 + 500/1M * 10.00 = 0.0025 + 0.005 = 0.0075
        assert cost is not None
        assert abs(cost - 0.0075) < 1e-7

    def test_unknown_model_uses_default(self, pricing_file: Path):
        calc = CostCalculator(str(pricing_file))
        cost = calc.calculate("unknown-model", prompt_tokens=1000, completion_tokens=1000)
        # 1000/1M * 1.00 + 1000/1M * 5.00 = 0.001 + 0.005 = 0.006
        assert cost is not None
        assert abs(cost - 0.006) < 1e-7

    def test_no_tokens_returns_none(self, pricing_file: Path):
        calc = CostCalculator(str(pricing_file))
        assert calc.calculate("gpt-4o", None, None) is None

    def test_zero_tokens(self, pricing_file: Path):
        calc = CostCalculator(str(pricing_file))
        cost = calc.calculate("gpt-4o", 0, 0)
        assert cost == 0.0

    def test_none_model_uses_default(self, pricing_file: Path):
        calc = CostCalculator(str(pricing_file))
        cost = calc.calculate(None, 1000, 0)
        assert cost is not None
        assert cost > 0

    def test_missing_pricing_file(self, tmp_path: Path):
        calc = CostCalculator(str(tmp_path / "nonexistent.yaml"))
        result = calc.calculate("gpt-4o", 1000, 500)
        assert result is None

    def test_hot_reload(self, pricing_file: Path):
        calc = CostCalculator(str(pricing_file))
        # Initial cost
        cost1 = calc.calculate("gpt-4o-mini", 1000, 1000)

        # Update pricing file with different rates
        import time
        time.sleep(0.01)  # ensure mtime changes
        pricing_file.write_text(yaml.dump({
            "models": {
                "gpt-4o-mini": {"input_per_1m": 1.00, "output_per_1m": 2.00},
            },
            "default": {"input_per_1m": 1.00, "output_per_1m": 5.00},
        }))
        # Touch to ensure mtime changes
        pricing_file.touch()

        cost2 = calc.calculate("gpt-4o-mini", 1000, 1000)
        # Costs should differ because pricing changed
        assert cost1 != cost2
