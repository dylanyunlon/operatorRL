"""
modules/control/dispatch/rate_limiter.py
Per-category rate limiting. Verbatim from Claude25 (Claude17).
"""
from __future__ import annotations
import time
from typing import Any, Dict, Optional


class DispatchRateLimiter:
    """Rate-limits dispatch actions by category to prevent spam."""

    DEFAULT_COOLDOWNS = {
        "voice_announcement": 10.0,
        "overlay_alert": 5.0,
        "log_entry": 1.0,
        "strategy_update": 15.0,
        "win_probability": 20.0,
    }

    def __init__(self, cooldowns: Optional[Dict[str, float]] = None) -> None:
        self._cooldowns = dict(self.DEFAULT_COOLDOWNS)
        if cooldowns:
            self._cooldowns.update(cooldowns)
        self._last_dispatch: Dict[str, float] = {}
        self._suppressed_count: int = 0
        self._total_checked: int = 0

    def should_dispatch(self, category: str) -> bool:
        self._total_checked += 1
        now = time.time()
        cooldown = self._cooldowns.get(category, 5.0)
        last = self._last_dispatch.get(category, 0.0)
        if now - last < cooldown:
            self._suppressed_count += 1
            return False
        self._last_dispatch[category] = now
        return True

    def set_cooldown(self, category: str, cooldown_s: float) -> None:
        self._cooldowns[category] = max(0.1, cooldown_s)

    def stats(self) -> Dict[str, Any]:
        return {
            "total_checked": self._total_checked,
            "suppressed_count": self._suppressed_count,
            "suppression_rate": round(self._suppressed_count / max(self._total_checked, 1), 4),
            "cooldowns": dict(self._cooldowns),
        }
