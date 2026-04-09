"""
modules/control/dispatch/cooldown_tracker.py
Per-category cooldown enforcement. Verbatim from Claude25 (Claude11).
"""
from __future__ import annotations
import time
from typing import Any, Dict


class CooldownTracker:
    """Per-category cooldown. Prevents spamming 'back now!' every tick."""

    def __init__(self, default_s: float = 10.0) -> None:
        self._default_s = default_s
        self._overrides: Dict[str, float] = {}
        self._last_fire: Dict[str, float] = {}

    def set_cooldown(self, category: str, seconds: float) -> None:
        self._overrides[category] = seconds

    def is_ready(self, category: str) -> bool:
        cooldown = self._overrides.get(category, self._default_s)
        last = self._last_fire.get(category, 0.0)
        return (time.monotonic() - last) >= cooldown

    def fire(self, category: str) -> None:
        self._last_fire[category] = time.monotonic()

    def time_remaining(self, category: str) -> float:
        cooldown = self._overrides.get(category, self._default_s)
        last = self._last_fire.get(category, 0.0)
        return max(0.0, cooldown - (time.monotonic() - last))

    def reset(self) -> None:
        self._last_fire.clear()

    def snapshot(self) -> Dict[str, Any]:
        now = time.monotonic()
        return {
            cat: {"cooldown_s": self._overrides.get(cat, self._default_s),
                  "remaining_s": round(self.time_remaining(cat), 2)}
            for cat, ts in self._last_fire.items()
        }
