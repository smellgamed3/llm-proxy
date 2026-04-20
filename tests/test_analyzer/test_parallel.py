"""Unit tests for analyzer.parallel.resolve_num_workers."""

from unittest.mock import patch

from analyzer.parallel import resolve_num_workers


class TestResolveNumWorkers:
    """Tests for automatic CPU core detection and worker count resolution."""

    # ------------------------------------------------------------------
    # configured=0 (auto mode): max(1, cpu_count - 1)
    # ------------------------------------------------------------------

    def test_auto_mode_8_cpus(self):
        with patch("os.cpu_count", return_value=8):
            assert resolve_num_workers(0) == 7

    def test_auto_mode_4_cpus(self):
        with patch("os.cpu_count", return_value=4):
            assert resolve_num_workers(0) == 3

    def test_auto_mode_2_cpus(self):
        with patch("os.cpu_count", return_value=2):
            assert resolve_num_workers(0) == 1

    def test_auto_mode_1_cpu_clamps_to_1(self):
        """Single-core machine must not produce 0 workers."""
        with patch("os.cpu_count", return_value=1):
            assert resolve_num_workers(0) == 1

    def test_auto_mode_cpu_count_none_fallback(self):
        """When os.cpu_count() returns None, fallback to 2 → 1 worker."""
        with patch("os.cpu_count", return_value=None):
            assert resolve_num_workers(0) == 1

    def test_auto_mode_negative_configured(self):
        """Any negative value also triggers auto mode."""
        with patch("os.cpu_count", return_value=8):
            assert resolve_num_workers(-1) == 7

    # ------------------------------------------------------------------
    # configured=1: single-process mode (never spawns a pool)
    # ------------------------------------------------------------------

    def test_single_process_mode(self):
        assert resolve_num_workers(1) == 1

    def test_single_process_mode_ignores_cpu_count(self):
        """configured=1 must always return 1 regardless of CPU count."""
        with patch("os.cpu_count", return_value=64):
            assert resolve_num_workers(1) == 1

    # ------------------------------------------------------------------
    # configured=N (N>1): explicit override
    # ------------------------------------------------------------------

    def test_explicit_2_workers(self):
        assert resolve_num_workers(2) == 2

    def test_explicit_4_workers(self):
        assert resolve_num_workers(4) == 4

    def test_explicit_large_number(self):
        assert resolve_num_workers(16) == 16
