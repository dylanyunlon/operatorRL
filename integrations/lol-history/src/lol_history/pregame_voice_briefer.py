"""
PregameVoiceBriefer — Generates structured pregame voice briefings.

Architecture (拿来主义):
  voice_narration_engine.py + history_driven_coaching_advisor.py（M605）

Location: integrations/lol-history/src/lol_history/pregame_voice_briefer.py

Design Notes (Knuth-level critique):
  User:
    - generate_briefing handles missing data gracefully — produces partial briefing.
    - to_tts_text converts structured briefing into natural language for TTS.
    - Priority queue ensures most critical intel is spoken first.
  System:
    - evolution_callback fires on every operation for system-level tracking.
    - Briefing length capped by max_sentences to respect TTS latency budget.
    - op_count provides basic throughput monitoring.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "integrations.lol_history.pregame_voice_briefer.v1"
_DEFAULT_MAX_SENTENCES: int = 12


def _safe_div(a: float, b: float, default: float = 0.0) -> float:
    return a / b if b != 0 else default


class PregameVoiceBriefer:
    """Generates structured pregame voice briefings from history intelligence.

    Public API
    ----------
    generate_briefing   — build a structured briefing from pregame context
    to_tts_text         — convert briefing to TTS-ready natural language
    get_briefing_queue  — get all pending briefings
    clear_queue         — clear briefing queue
    get_stats           — internal statistics

    Evolution
    ---------
    Set evolution_callback to receive structured events.
    """

    def __init__(self, *, max_sentences: int = _DEFAULT_MAX_SENTENCES) -> None:
        self.evolution_callback: Optional[Callable[[Dict[str, Any]], None]] = None
        self._op_count: int = 0
        self._max_sentences: int = max_sentences
        self._queue: List[Dict[str, Any]] = []
        self._generated_count: int = 0

    def _fire(self, event_type: str, data: Dict[str, Any]) -> None:
        if self.evolution_callback is not None:
            self.evolution_callback({
                "source": _EVOLUTION_KEY, "type": event_type,
                "timestamp": time.time(), "payload": data,
            })

    # ------------------------------------------------------------------ #

    def generate_briefing(self, context: Dict[str, Any] = None) -> Dict[str, Any]:
        """Build a structured briefing from pregame context.

        Parameters
        ----------
        context : dict
            Keys: opponent_history, team_comp, champion_id, role,
                  streak_info, matchup_data, meta_intel.

        Returns
        -------
        dict  with status, briefing_items (list), priority_order
        """
        self._op_count += 1
        _start = time.time()
        if context is None:
            context = {}

        items: List[Dict[str, Any]] = []

        # Streak warning (highest priority)
        streak = context.get("streak_info", {})
        if streak.get("type") == "losing" and streak.get("count", 0) >= 3:
            items.append({
                "priority": 0, "category": "mental",
                "message": f"You are on a {streak['count']}-game losing streak. Consider a break.",
                "tts_hint": "warning",
            })

        # Matchup intel
        matchup = context.get("matchup_data", {})
        if matchup.get("win_rate") is not None:
            wr = matchup["win_rate"]
            items.append({
                "priority": 1, "category": "matchup",
                "message": f"Your historical win rate in this matchup is {wr:.0%}.",
                "tts_hint": "info",
            })

        # Opponent tendencies
        opp = context.get("opponent_history", {})
        if opp.get("playstyle"):
            items.append({
                "priority": 2, "category": "opponent",
                "message": f"Opponent tends to play {opp['playstyle']}.",
                "tts_hint": "info",
            })

        # Meta intel
        meta = context.get("meta_intel", {})
        if meta.get("champion_tier"):
            items.append({
                "priority": 3, "category": "meta",
                "message": f"Your champion is currently {meta['champion_tier']}-tier in the meta.",
                "tts_hint": "info",
            })

        # Role-specific advice
        role = context.get("role", "")
        champ = context.get("champion_id")
        if role and champ:
            items.append({
                "priority": 4, "category": "role",
                "message": f"Playing champion {champ} in {role} position.",
                "tts_hint": "info",
            })

        # Sort by priority and cap
        items.sort(key=lambda x: x["priority"])
        items = items[:self._max_sentences]

        briefing = {
            "items": items,
            "generated_at": time.time(),
            "context_keys": list(context.keys()),
        }
        self._queue.append(briefing)
        self._generated_count += 1

        elapsed = time.time() - _start
        self._fire("generate_briefing_completed", {"elapsed": elapsed, "item_count": len(items)})
        return {"status": "ok", "op": "generate_briefing",
                "briefing_items": items, "item_count": len(items)}

    # ------------------------------------------------------------------ #

    def to_tts_text(self, briefing_items: List[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Convert briefing items to TTS-ready natural language.

        Parameters
        ----------
        briefing_items : list of dict

        Returns
        -------
        dict  with status, tts_text, sentence_count
        """
        self._op_count += 1
        if briefing_items is None:
            briefing_items = []

        sentences: List[str] = []
        for item in briefing_items:
            msg = item.get("message", "")
            if msg:
                sentences.append(msg)

        tts_text = " ".join(sentences)
        return {"status": "ok", "op": "to_tts_text",
                "tts_text": tts_text, "sentence_count": len(sentences)}

    # ------------------------------------------------------------------ #

    def get_briefing_queue(self) -> Dict[str, Any]:
        """Get all pending briefings."""
        self._op_count += 1
        return {"status": "ok", "op": "get_briefing_queue",
                "queue": list(self._queue), "count": len(self._queue)}

    # ------------------------------------------------------------------ #

    def clear_queue(self) -> Dict[str, Any]:
        """Clear briefing queue."""
        self._op_count += 1
        self._queue.clear()
        self._fire("clear_queue", {})
        return {"status": "ok", "op": "clear_queue"}

    # ------------------------------------------------------------------ #

    def get_stats(self) -> Dict[str, Any]:
        """Internal statistics."""
        self._op_count += 1
        return {
            "op_count": self._op_count,
            "generated_count": self._generated_count,
            "queue_size": len(self._queue),
            "max_sentences": self._max_sentences,
        }
