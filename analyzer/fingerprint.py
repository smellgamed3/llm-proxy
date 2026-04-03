from __future__ import annotations

import hashlib


class Fingerprinter:
    """Generates stable fingerprints for prompt templates."""

    def fingerprint(
        self,
        system_prompt: str | None,
        user_prompt: str | None = None,
    ) -> str | None:
        """Generate a stable fingerprint for a prompt template.

        Uses the system prompt as the primary identifier. Returns None if no
        system prompt is present.
        """
        text = (system_prompt or "").strip()
        if not text:
            return None
        return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
