"""Tests for Fingerprinter."""
from __future__ import annotations

from analyzer.fingerprint import Fingerprinter


class TestFingerprinter:
    def test_same_prompt_same_fingerprint(self):
        fp = Fingerprinter()
        f1 = fp.fingerprint("You are a helpful assistant.")
        f2 = fp.fingerprint("You are a helpful assistant.")
        assert f1 == f2

    def test_different_prompts_different_fingerprints(self):
        fp = Fingerprinter()
        f1 = fp.fingerprint("You are a helpful assistant.")
        f2 = fp.fingerprint("You are a coding expert.")
        assert f1 != f2

    def test_empty_prompt_returns_none(self):
        fp = Fingerprinter()
        assert fp.fingerprint(None) is None
        assert fp.fingerprint("") is None
        assert fp.fingerprint("   ") is None

    def test_fingerprint_is_16_chars(self):
        fp = Fingerprinter()
        f = fp.fingerprint("Some system prompt")
        assert f is not None
        assert len(f) == 16

    def test_whitespace_stripped(self):
        fp = Fingerprinter()
        f1 = fp.fingerprint("hello")
        f2 = fp.fingerprint("  hello  ")
        assert f1 == f2

    def test_user_prompt_ignored_in_fingerprint(self):
        fp = Fingerprinter()
        f1 = fp.fingerprint("System prompt", "user message 1")
        f2 = fp.fingerprint("System prompt", "user message 2")
        assert f1 == f2
