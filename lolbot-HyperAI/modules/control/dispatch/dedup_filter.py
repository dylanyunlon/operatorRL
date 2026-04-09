"""
modules/control/dispatch/dedup_filter.py
Content-hash deduplication. Verbatim from Claude25 (Claude11).
"""
from __future__ import annotations
import hashlib
import time
from typing import Any, Dict


class DedupFilter:
    """Content-hash based deduplication within a sliding window."""

    def __init__(self, window_s: float = 3.0) -> None:
        self._window_s = window_s
        self._seen: Dict[str, float] = {}

    def is_duplicate(self, action: Any) -> bool:
        h = hashlib.md5(
            f"{action.dedup_key}:{action.text}".encode()
        ).hexdigest()[:12]
        now = time.monotonic()
        if len(self._seen) > 500:
            cutoff = now - self._window_s
            self._seen = {k: v for k, v in self._seen.items() if v > cutoff}
        last = self._seen.get(h, 0.0)
        if now - last < self._window_s:
            return True
        self._seen[h] = now
        return False
