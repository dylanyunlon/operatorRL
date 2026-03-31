"""
HistoricalPowerSpikeAdvisor — Advises on champion power spikes from historical timing data.

Architecture (拿来主义):
  game_phase_strategy_mapper.py（M642）— phase-based strategy mapping
  timing_window_scheduler.py（M699）— timing window management

Location: integrations/lol-history/src/lol_history/historical_power_spike_advisor.py

Design Notes (Knuth-level critique):
  User:
    - get_spikes() returns when a champion historically hits power spikes (level/item based).
    - advise_current() tells you if you/opponent are currently in a spike window.
  System:
    - Spike detection from win rate jumps at specific game times/item completions.
    - Both level-based and item-based spike tracking.
"""
from __future__ import annotations
import logging, time
from collections import defaultdict
from typing import Any, Callable, Dict, List, Optional, Tuple
logger = logging.getLogger(__name__)
_EVOLUTION_KEY = "integrations.lol_history.historical_power_spike_advisor.v1"

def _safe_div(a, b, d=0.0): return a / b if b else d


class HistoricalPowerSpikeAdvisor:
    """Advises on power spikes from historical game timing data.

    Public API: ingest_spike_data, get_spikes, advise_current, get_upcoming_spikes, get_stats
    """
    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable] = None
        self._op_count = 0
        # key: champion_id -> list of spike definitions
        self._spike_db: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
        self._advise_count = 0

    def _fire(self, et, data):
        if self.evolution_callback: self.evolution_callback({"type": et, "key": _EVOLUTION_KEY, **data})

    def ingest_spike_data(self, records: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Ingest historical power spike data.

        Each record: {champion_id, spike_type: "level"|"item"|"time",
                      trigger: (level number or item_id or game_time_s),
                      win_rate_before, win_rate_after, avg_game_time_s, samples}
        """
        self._op_count += 1
        ingested = 0
        for rec in records:
            cid = rec.get("champion_id", 0)
            if not cid or not rec.get("spike_type"):
                continue
            wr_before = rec.get("win_rate_before", 0.5)
            wr_after = rec.get("win_rate_after", 0.5)
            magnitude = wr_after - wr_before
            spike = {
                "spike_type": rec["spike_type"],
                "trigger": rec.get("trigger"),
                "win_rate_before": round(wr_before, 4),
                "win_rate_after": round(wr_after, 4),
                "magnitude": round(magnitude, 4),
                "avg_game_time": rec.get("avg_game_time_s", 0),
                "samples": rec.get("samples", 0),
            }
            self._spike_db[cid].append(spike)
            ingested += 1

        # Sort spikes by magnitude for each champion
        for cid in self._spike_db:
            self._spike_db[cid].sort(key=lambda s: s["magnitude"], reverse=True)

        return {"status": "ok", "ingested": ingested, "champions": len(self._spike_db)}

    def get_spikes(self, champion_id: int) -> Dict[str, Any]:
        """Get all known power spikes for a champion."""
        self._op_count += 1
        spikes = self._spike_db.get(champion_id, [])
        return {"status": "ok", "champion_id": champion_id,
                "spikes": spikes, "total_spikes": len(spikes)}

    def advise_current(self, champion_id: int, current_level: int = 0,
                       current_items: List[int] = None,
                       game_time_s: float = 0) -> Dict[str, Any]:
        """Check if champion is currently at a power spike.

        Args:
            current_level: Champion's current level.
            current_items: List of completed item IDs.
            game_time_s: Current game time in seconds.

        Returns:
            Dict with active_spikes, is_spiking, spike_strength.
        """
        self._op_count += 1
        self._advise_count += 1
        current_items = set(current_items or [])
        spikes = self._spike_db.get(champion_id, [])

        active_spikes = []
        for spike in spikes:
            st = spike["spike_type"]
            trigger = spike["trigger"]
            is_active = False

            if st == "level" and isinstance(trigger, (int, float)):
                # Level spike: active when at or just past the trigger level
                if current_level >= trigger and current_level <= trigger + 1:
                    is_active = True
            elif st == "item" and trigger in current_items:
                is_active = True
            elif st == "time" and isinstance(trigger, (int, float)):
                # Time spike: active within ±60s of the trigger time
                if abs(game_time_s - trigger) <= 60:
                    is_active = True

            if is_active:
                active_spikes.append(spike)

        is_spiking = len(active_spikes) > 0
        spike_strength = max((s["magnitude"] for s in active_spikes), default=0.0)

        result = {
            "status": "ok", "champion_id": champion_id,
            "is_spiking": is_spiking,
            "spike_strength": round(spike_strength, 4),
            "active_spikes": active_spikes,
            "current_level": current_level,
            "game_time": game_time_s,
        }
        if is_spiking:
            self._fire("spike_active", {"champion_id": champion_id,
                                         "strength": spike_strength})
        return result

    def get_upcoming_spikes(self, champion_id: int, current_level: int = 0,
                            current_items: List[int] = None,
                            game_time_s: float = 0) -> Dict[str, Any]:
        """Get upcoming power spikes that haven't been reached yet."""
        self._op_count += 1
        current_items = set(current_items or [])
        spikes = self._spike_db.get(champion_id, [])

        upcoming = []
        for spike in spikes:
            st = spike["spike_type"]
            trigger = spike["trigger"]

            if st == "level" and isinstance(trigger, (int, float)) and trigger > current_level:
                upcoming.append({**spike, "levels_away": trigger - current_level})
            elif st == "item" and trigger not in current_items:
                upcoming.append({**spike, "item_needed": trigger})
            elif st == "time" and isinstance(trigger, (int, float)) and trigger > game_time_s:
                upcoming.append({**spike, "seconds_away": round(trigger - game_time_s)})

        upcoming.sort(key=lambda s: s.get("magnitude", 0), reverse=True)
        return {"status": "ok", "champion_id": champion_id,
                "upcoming_spikes": upcoming[:5]}

    def get_stats(self) -> Dict[str, Any]:
        self._op_count += 1
        return {"op_count": self._op_count, "advise_count": self._advise_count,
                "champions": len(self._spike_db),
                "total_spikes": sum(len(v) for v in self._spike_db.values())}
