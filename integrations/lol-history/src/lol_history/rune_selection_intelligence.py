"""
RuneSelectionIntelligence — Predicts and advises rune pages from historical data.

Architecture (拿来主义):
  Seraphine/app/lol/connector.py — JsonManager.getRuneIconPath, getRuneName, getPerkStyles
  Seraphine/app/lol/tools.py — participant perkPrimaryStyle/perkSubStyle extraction

Location: integrations/lol-history/src/lol_history/rune_selection_intelligence.py

Design Notes (Knuth-level critique):
  User:
    - Predicts opponent rune pages before game: "enemy likely running Conqueror + Resolve."
    - Suggests rune pages based on matchup history win rates.
  System:
    - Rune page is (primary_style, sub_style) pair; individual rune choices are secondary.
    - Per-champion-per-matchup rune tracking for counter-rune suggestions.
"""
from __future__ import annotations
import logging, time
from collections import Counter, defaultdict
from typing import Any, Callable, Dict, List, Optional, Tuple
logger = logging.getLogger(__name__)
_EVOLUTION_KEY = "integrations.lol_history.rune_selection_intelligence.v1"

def _safe_div(a, b, d=0.0): return a / b if b else d

_STYLE_NAMES = {
    8000: "Precision", 8100: "Domination", 8200: "Sorcery",
    8300: "Inspiration", 8400: "Resolve",
}


class RuneSelectionIntelligence:
    """Predicts and advises rune pages from historical match data.

    Public API: record_rune_choice, predict_runes, suggest_counter_runes,
                get_champion_rune_stats, get_stats
    """
    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable] = None
        self._op_count = 0
        self._predict_count = 0
        # (champion_id, role) → Counter of (primary_style, sub_style)
        self._rune_history: Dict[Tuple[int, str], List[Dict[str, Any]]] = defaultdict(list)

    def _fire(self, et, data):
        if self.evolution_callback:
            self.evolution_callback({"type": et, "key": _EVOLUTION_KEY, **data})

    def record_rune_choice(self, champion_id: int, role: str,
                            primary_style: int, sub_style: int,
                            win: bool = False) -> Dict[str, Any]:
        """Record a rune page observation."""
        self._op_count += 1
        key = (champion_id, role.upper())
        record = {"primary": primary_style, "sub": sub_style,
                  "win": win, "timestamp": time.time()}
        self._rune_history[key].append(record)
        # Trim to 500 per champion-role
        if len(self._rune_history[key]) > 500:
            self._rune_history[key] = self._rune_history[key][-500:]
        return {"status": "ok", "champion_id": champion_id, "role": role,
                "runes": (primary_style, sub_style)}

    def record_from_matches(self, matches: List[Dict[str, Any]],
                             target_puuid: str = "") -> Dict[str, Any]:
        """Bulk record rune choices from match history."""
        self._op_count += 1
        recorded = 0
        for match in matches:
            info = match.get("info", match)
            for p in info.get("participants", []):
                if target_puuid and p.get("puuid", "") != target_puuid:
                    continue
                champ_id = p.get("championId", 0)
                role = p.get("teamPosition", p.get("lane", ""))
                perks = p.get("perks", {})
                styles = perks.get("styles", [])
                primary = sub = 0
                if len(styles) >= 2:
                    primary = styles[0].get("style", 0)
                    sub = styles[1].get("style", 0)
                else:
                    primary = p.get("perkPrimaryStyle",
                                    p.get("stats", {}).get("perkPrimaryStyle", 0))
                    sub = p.get("perkSubStyle",
                                p.get("stats", {}).get("perkSubStyle", 0))
                win = p.get("win", p.get("stats", {}).get("win", False))
                if champ_id and primary:
                    self.record_rune_choice(champ_id, role, primary, sub, win)
                    recorded += 1
        return {"status": "ok", "recorded": recorded}

    def predict_runes(self, champion_id: int, role: str = "") -> Dict[str, Any]:
        """Predict most likely rune page for a champion."""
        self._op_count += 1
        self._predict_count += 1
        key = (champion_id, role.upper())
        history = self._rune_history.get(key, [])
        if not history:
            return {"status": "ok", "champion_id": champion_id,
                    "predictions": [], "confidence": 0.0}
        counter: Counter = Counter()
        win_counter: Dict[Tuple[int, int], int] = defaultdict(int)
        for r in history:
            pair = (r["primary"], r["sub"])
            counter[pair] += 1
            if r.get("win"):
                win_counter[pair] += 1
        total = sum(counter.values())
        predictions = []
        for pair, count in counter.most_common(5):
            wr = round(_safe_div(win_counter[pair], count) * 100, 1)
            predictions.append({
                "primary_style": pair[0],
                "sub_style": pair[1],
                "primary_name": _STYLE_NAMES.get(pair[0], f"Style_{pair[0]}"),
                "sub_name": _STYLE_NAMES.get(pair[1], f"Style_{pair[1]}"),
                "probability": round(_safe_div(count, total), 3),
                "winrate": wr,
                "observations": count,
            })
        confidence = predictions[0]["probability"] if predictions else 0.0
        self._fire("predicted", {"champion_id": champion_id, "confidence": confidence})
        return {"status": "ok", "champion_id": champion_id,
                "predictions": predictions, "confidence": confidence,
                "total_observations": total}

    def suggest_counter_runes(self, my_champion_id: int, enemy_champion_id: int,
                               role: str = "") -> Dict[str, Any]:
        """Suggest rune page based on matchup win rates."""
        self._op_count += 1
        key = (my_champion_id, role.upper())
        history = self._rune_history.get(key, [])
        if not history:
            return {"status": "ok", "suggestions": [], "reason": "no_data"}
        # Find highest win rate rune combos
        combo_stats: Dict[Tuple[int, int], Dict] = defaultdict(
            lambda: {"wins": 0, "games": 0})
        for r in history:
            pair = (r["primary"], r["sub"])
            combo_stats[pair]["games"] += 1
            if r.get("win"):
                combo_stats[pair]["wins"] += 1
        suggestions = []
        for pair, stats in combo_stats.items():
            if stats["games"] >= 3:
                wr = round(_safe_div(stats["wins"], stats["games"]) * 100, 1)
                suggestions.append({
                    "primary_style": pair[0], "sub_style": pair[1],
                    "primary_name": _STYLE_NAMES.get(pair[0], ""),
                    "sub_name": _STYLE_NAMES.get(pair[1], ""),
                    "winrate": wr, "games": stats["games"],
                })
        suggestions.sort(key=lambda s: s["winrate"], reverse=True)
        return {"status": "ok", "my_champion": my_champion_id,
                "enemy_champion": enemy_champion_id,
                "suggestions": suggestions[:3]}

    def get_champion_rune_stats(self, champion_id: int,
                                 role: str = "") -> Dict[str, Any]:
        """Get rune usage statistics for a champion."""
        self._op_count += 1
        key = (champion_id, role.upper())
        history = self._rune_history.get(key, [])
        if not history:
            return {"status": "ok", "champion_id": champion_id, "stats": {}}
        primary_counter: Counter = Counter()
        for r in history:
            primary_counter[r["primary"]] += 1
        total = len(history)
        stats = {
            "total_games": total,
            "primary_distribution": {
                _STYLE_NAMES.get(k, str(k)): round(_safe_div(v, total) * 100, 1)
                for k, v in primary_counter.most_common()
            },
        }
        return {"status": "ok", "champion_id": champion_id, "stats": stats}

    def get_stats(self) -> Dict[str, Any]:
        total_records = sum(len(v) for v in self._rune_history.values())
        return {"predict_count": self._predict_count,
                "champion_role_combos": len(self._rune_history),
                "total_records": total_records,
                "total_ops": self._op_count}
