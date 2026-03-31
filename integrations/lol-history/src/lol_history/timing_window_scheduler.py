"""
TimingWindowScheduler — Schedules time-sensitive action windows.

Architecture (拿来主义):
  ban_pick_realtime_advisor.py（M637）— realtime scheduling
  DI-star/distar/agent/default/agent.py — _get_time_factor

Location: integrations/lol-history/src/lol_history/timing_window_scheduler.py
"""
from __future__ import annotations
import logging, time
from typing import Any, Callable, Dict, List, Optional
logger = logging.getLogger(__name__)
_EVOLUTION_KEY = "integrations.lol_history.timing_window_scheduler.v1"

class TimingWindowScheduler:
    """Schedules time-sensitive windows (dragon spawn, ult CD, wave arrival).

    Public API: register_window, check_windows, get_upcoming, get_utilization, get_stats
    """
    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable] = None
        self._op_count = 0
        self._windows: List[Dict] = []
        self._utilized = 0
        self._missed = 0
        self._check_count = 0

    def _fire(self, et, data):
        if self.evolution_callback: self.evolution_callback({"type": et, "key": _EVOLUTION_KEY, **data})

    def register_window(self, name: str, opens_at: float, closes_at: float,
                        priority: str = "medium", meta: Dict = None) -> Dict[str, Any]:
        self._op_count += 1
        window = {"name": name, "opens_at": opens_at, "closes_at": closes_at,
                  "priority": priority, "meta": meta or {}, "status": "pending"}
        self._windows.append(window)
        return {"status": "ok", "window": name, "total_windows": len(self._windows)}

    def check_windows(self, game_time: float) -> Dict[str, Any]:
        self._op_count += 1
        self._check_count += 1
        active = []
        upcoming = []
        expired = []
        for w in self._windows:
            if w["status"] == "utilized": continue
            if game_time >= w["closes_at"]:
                if w["status"] != "expired":
                    w["status"] = "expired"
                    self._missed += 1
                expired.append(w)
            elif game_time >= w["opens_at"]:
                w["status"] = "active"
                active.append(w)
            else:
                eta = w["opens_at"] - game_time
                if eta < 30:
                    upcoming.append({**w, "eta_s": round(eta, 1)})
        self._fire("windows_checked", {"active": len(active), "upcoming": len(upcoming)})
        return {"status": "ok", "active": active, "upcoming": upcoming, "expired_count": len(expired)}

    def mark_utilized(self, name: str) -> Dict[str, Any]:
        self._op_count += 1
        for w in self._windows:
            if w["name"] == name and w["status"] == "active":
                w["status"] = "utilized"
                self._utilized += 1
                return {"status": "ok", "window": name}
        return {"status": "error", "reason": f"window '{name}' not active"}

    def get_upcoming(self, game_time: float, horizon_s: float = 60) -> List[Dict]:
        return [w for w in self._windows
                if w["status"] == "pending" and w["opens_at"] - game_time < horizon_s and w["opens_at"] > game_time]

    def get_utilization(self) -> Dict[str, Any]:
        total = self._utilized + self._missed
        return {"utilized": self._utilized, "missed": self._missed,
                "rate": round(self._utilized / max(total, 1), 3)}

    def get_stats(self) -> Dict[str, Any]:
        return {"total_windows": len(self._windows), "utilized": self._utilized,
                "missed": self._missed, "checks": self._check_count, "total_ops": self._op_count}

