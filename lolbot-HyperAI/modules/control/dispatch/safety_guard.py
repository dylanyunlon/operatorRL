"""
modules/control/dispatch/safety_guard.py
Contradiction suppression. Verbatim from Claude25 control_component.py (Claude11).
"""
from __future__ import annotations
import time
from typing import Any, Dict, List, Tuple


class SafetyGuard:
    """Suppress contradictory advice within a time window."""

    _CONTRARY_PAIRS = {
        "aggressive": "defensive",
        "push": "retreat",
        "engage": "disengage",
        "fight": "avoid",
        "all_in": "back_off",
    }

    def __init__(self, window_s: float = 5.0):
        self._window_s = window_s
        self._recent: List[Dict[str, Any]] = []

    def check(self, action: Any) -> Tuple[bool, str]:
        """Return (allowed, reason). False means suppress."""
        now = time.monotonic()
        direction = self._classify_direction(action.text)
        if not direction:
            return True, ""

        full_contrary: Dict[str, str] = {}
        for k, v in self._CONTRARY_PAIRS.items():
            full_contrary[k] = v
            full_contrary[v] = k

        for recent in self._recent:
            if now - recent["ts"] > self._window_s:
                continue
            if not recent.get("dir"):
                continue
            contrary = full_contrary.get(direction, "")
            if contrary and contrary == recent["dir"]:
                return False, (
                    f"contradicts '{recent['text'][:40]}' from "
                    f"{now - recent['ts']:.1f}s ago"
                )
        return True, ""

    def record(self, action: Any) -> None:
        direction = self._classify_direction(action.text)
        self._recent.append({
            "dir": direction, "ts": time.monotonic(), "text": action.text,
        })
        if len(self._recent) > 50:
            self._recent = self._recent[-50:]

    def _classify_direction(self, text: str) -> str:
        lower = text.lower()
        for d in self._CONTRARY_PAIRS:
            if d.replace("_", " ") in lower:
                return d
        for d, c in self._CONTRARY_PAIRS.items():
            if c.replace("_", " ") in lower:
                return c
        return ""
