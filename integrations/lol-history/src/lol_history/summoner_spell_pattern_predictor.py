"""
SummonerSpellPatternPredictor — Predicts opponent summoner spell timing from history.

Architecture (拿来主义):
  Seraphine/app/lol/tools.py — participant spell1Id/spell2Id extraction
  Seraphine/app/lol/connector.py — JsonManager.getSummonerSpellList spell database

Location: integrations/lol-history/src/lol_history/summoner_spell_pattern_predictor.py

Design Notes (Knuth-level critique):
  User:
    - Predicts which summoner spells opponent will take based on history.
    - Tracks flash/ignite/teleport cooldown awareness from game timeline data.
  System:
    - Spell prediction is per-champion-per-role, not per-player globally.
    - Historical frequency is sufficient; no ML needed for binary spell choice.
"""
from __future__ import annotations
import logging, time
from collections import Counter, defaultdict
from typing import Any, Callable, Dict, List, Optional, Tuple
logger = logging.getLogger(__name__)
_EVOLUTION_KEY = "integrations.lol_history.summoner_spell_pattern_predictor.v1"

def _safe_div(a, b, d=0.0): return a / b if b else d

_SPELL_NAMES = {
    1: "Cleanse", 3: "Exhaust", 4: "Flash", 6: "Ghost", 7: "Heal",
    11: "Smite", 12: "Teleport", 13: "Clarity", 14: "Ignite",
    21: "Barrier", 32: "Mark",  # ARAM snowball
}
_SPELL_COOLDOWNS = {
    1: 210, 3: 210, 4: 300, 6: 210, 7: 240,
    11: 15, 12: 360, 13: 240, 14: 180, 21: 180, 32: 80,
}


class SummonerSpellPatternPredictor:
    """Predicts opponent summoner spell choices and tracks cooldowns.

    Public API: record_spell_choice, predict_spells, track_cooldown,
                get_cooldown_status, get_stats
    """
    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable] = None
        self._op_count = 0
        self._predict_count = 0
        # (champion_id, role) → Counter of (spell1, spell2) tuples
        self._spell_history: Dict[Tuple[int, str], Counter] = defaultdict(Counter)
        # Active cooldowns: (puuid, spell_id) → expiry timestamp
        self._cooldowns: Dict[Tuple[str, int], float] = {}

    def _fire(self, et, data):
        if self.evolution_callback:
            self.evolution_callback({"type": et, "key": _EVOLUTION_KEY, **data})

    def record_spell_choice(self, champion_id: int, role: str,
                             spell1_id: int, spell2_id: int) -> Dict[str, Any]:
        """Record a spell choice observation from match history."""
        self._op_count += 1
        key = (champion_id, role.upper())
        # Normalize spell order (lower id first)
        pair = tuple(sorted([spell1_id, spell2_id]))
        self._spell_history[key][pair] += 1
        return {"status": "ok", "champion_id": champion_id, "role": role,
                "spells": pair, "observations": self._spell_history[key][pair]}

    def record_from_matches(self, matches: List[Dict[str, Any]],
                             target_puuid: str = "") -> Dict[str, Any]:
        """Bulk record spell choices from match history."""
        self._op_count += 1
        recorded = 0
        for match in matches:
            info = match.get("info", match)
            for p in info.get("participants", []):
                if target_puuid and p.get("puuid", "") != target_puuid:
                    continue
                champ_id = p.get("championId", 0)
                role = p.get("teamPosition", p.get("lane", ""))
                s1 = p.get("summoner1Id", p.get("spell1Id", 0))
                s2 = p.get("summoner2Id", p.get("spell2Id", 0))
                if champ_id and s1 and s2:
                    self.record_spell_choice(champ_id, role, s1, s2)
                    recorded += 1
        return {"status": "ok", "recorded": recorded}

    def predict_spells(self, champion_id: int, role: str = "") -> Dict[str, Any]:
        """Predict most likely summoner spell combination."""
        self._op_count += 1
        self._predict_count += 1
        key = (champion_id, role.upper())
        counter = self._spell_history.get(key, Counter())
        if not counter:
            # Fallback: Flash + most common for role
            return {"status": "ok", "champion_id": champion_id,
                    "predicted_spells": (4, 14), "confidence": 0.0,
                    "spell_names": ["Flash", "Ignite"], "source": "default"}
        most_common = counter.most_common(3)
        top_pair, top_count = most_common[0]
        total = sum(counter.values())
        confidence = round(_safe_div(top_count, total), 3)
        predictions = []
        for pair, count in most_common:
            names = [_SPELL_NAMES.get(s, f"Spell_{s}") for s in pair]
            predictions.append({
                "spells": pair, "spell_names": names,
                "count": count, "probability": round(_safe_div(count, total), 3)
            })
        self._fire("predicted", {"champion_id": champion_id, "confidence": confidence})
        return {"status": "ok", "champion_id": champion_id,
                "predicted_spells": top_pair,
                "spell_names": [_SPELL_NAMES.get(s, f"Spell_{s}") for s in top_pair],
                "confidence": confidence, "alternatives": predictions,
                "source": "history", "observations": total}

    def track_cooldown(self, puuid: str, spell_id: int,
                        used_at: float = 0.0) -> Dict[str, Any]:
        """Track when a summoner spell was used to estimate cooldown."""
        self._op_count += 1
        if not used_at:
            used_at = time.time()
        cd = _SPELL_COOLDOWNS.get(spell_id, 300)
        expiry = used_at + cd
        self._cooldowns[(puuid, spell_id)] = expiry
        spell_name = _SPELL_NAMES.get(spell_id, f"Spell_{spell_id}")
        return {"status": "ok", "puuid": puuid[:8], "spell": spell_name,
                "cooldown": cd, "available_at": expiry}

    def get_cooldown_status(self, puuid: str) -> Dict[str, Any]:
        """Get current cooldown status for all tracked spells of a player."""
        self._op_count += 1
        now = time.time()
        statuses = []
        for (p, sid), expiry in self._cooldowns.items():
            if p == puuid:
                remaining = max(0, expiry - now)
                statuses.append({
                    "spell_id": sid,
                    "spell_name": _SPELL_NAMES.get(sid, f"Spell_{sid}"),
                    "available": remaining <= 0,
                    "remaining_seconds": round(remaining, 1),
                })
        return {"status": "ok", "puuid": puuid[:8], "spells": statuses}

    def get_stats(self) -> Dict[str, Any]:
        total_observations = sum(sum(c.values()) for c in self._spell_history.values())
        return {"predict_count": self._predict_count,
                "champion_role_combos": len(self._spell_history),
                "total_observations": total_observations,
                "active_cooldowns": len(self._cooldowns),
                "total_ops": self._op_count}
