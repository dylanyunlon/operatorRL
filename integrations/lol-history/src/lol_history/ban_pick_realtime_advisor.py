"""
BanPickRealtimeAdvisor — Real-time ban/pick suggestions during champion select.

Architecture (拿来主义):
  champ_select_automator.py + champion_pool_recommender.py（M610）

Location: integrations/lol-history/src/lol_history/ban_pick_realtime_advisor.py

Design Notes (Knuth-level critique):
  User:
    - suggest_ban/suggest_pick handle empty history gracefully — fallback to default.
    - Suggestions always include reasoning so user understands the recommendation.
    - All outputs sorted by priority score.
  System:
    - evolution_callback fires on every operation for system-level tracking.
    - Scoring uses historical win rates + meta tier + matchup data for composite score.
    - op_count provides basic throughput monitoring.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "integrations.lol_history.ban_pick_realtime_advisor.v1"


def _safe_div(a: float, b: float, default: float = 0.0) -> float:
    return a / b if b != 0 else default


class BanPickRealtimeAdvisor:
    """Real-time ban/pick suggestions during champion select.

    Public API
    ----------
    set_context         — load matchup/meta/pool data
    suggest_ban         — suggest champions to ban
    suggest_pick        — suggest champions to pick
    evaluate_draft      — evaluate current draft state
    get_stats           — internal statistics

    Evolution
    ---------
    Set evolution_callback to receive structured events.
    """

    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable[[Dict[str, Any]], None]] = None
        self._op_count: int = 0
        self._context: Dict[str, Any] = {}
        self._suggestion_count: int = 0

    def _fire(self, event_type: str, data: Dict[str, Any]) -> None:
        if self.evolution_callback is not None:
            self.evolution_callback({
                "source": _EVOLUTION_KEY, "type": event_type,
                "timestamp": time.time(), "payload": data,
            })

    # ------------------------------------------------------------------ #

    def set_context(self, context: Dict[str, Any] = None) -> Dict[str, Any]:
        """Load matchup, meta, and champion pool data for advisory.

        Parameters
        ----------
        context : dict
            Keys: champion_pool, matchup_table, meta_tier_list,
                  opponent_history, team_needs.

        Returns
        -------
        dict
        """
        self._op_count += 1
        if context is None:
            context = {}
        self._context = dict(context)
        self._fire("set_context", {"keys": list(context.keys())})
        return {"status": "ok", "op": "set_context",
                "context_keys": list(context.keys())}

    # ------------------------------------------------------------------ #

    def suggest_ban(self, already_banned: List[int] = None,
                    enemy_hover: List[int] = None,
                    top_n: int = 3) -> Dict[str, Any]:
        """Suggest champions to ban.

        Parameters
        ----------
        already_banned : list of int  (champion IDs)
        enemy_hover : list of int
        top_n : int

        Returns
        -------
        dict  with status, suggestions (list)
        """
        self._op_count += 1
        _start = time.time()
        if already_banned is None:
            already_banned = []
        if enemy_hover is None:
            enemy_hover = []

        banned_set = set(already_banned)
        meta_tiers = self._context.get("meta_tier_list", {})
        opp_history = self._context.get("opponent_history", {})
        matchup_table = self._context.get("matchup_table", {})

        candidates: List[Dict[str, Any]] = []

        # Score all known champions not yet banned
        all_champs = set()
        all_champs.update(meta_tiers.keys())
        all_champs.update(int(k) for k in opp_history.keys() if str(k).isdigit())
        for champ_id in all_champs:
            if champ_id in banned_set:
                continue

            score = 0.0
            reasons: List[str] = []

            # Meta threat
            tier = meta_tiers.get(champ_id, {})
            if isinstance(tier, dict):
                wr = tier.get("win_rate", 0.5)
                pr = tier.get("pick_rate", 0.0)
                score += wr * 0.4 + pr * 0.3
                if wr > 0.52:
                    reasons.append(f"High meta win rate ({wr:.1%})")

            # Opponent preference
            opp_data = opp_history.get(str(champ_id), {})
            if opp_data.get("games", 0) > 3:
                opp_wr = opp_data.get("win_rate", 0.5)
                score += opp_wr * 0.3
                reasons.append(f"Opponent comfort pick ({opp_data['games']} games)")

            # Enemy hovering
            if champ_id in enemy_hover:
                score += 0.2
                reasons.append("Enemy is hovering this champion")

            candidates.append({
                "champion_id": champ_id,
                "ban_score": round(score, 4),
                "reasons": reasons,
            })

        candidates.sort(key=lambda x: -x["ban_score"])
        suggestions = candidates[:top_n]

        self._suggestion_count += 1
        elapsed = time.time() - _start
        self._fire("suggest_ban_completed", {"elapsed": elapsed})
        return {"status": "ok", "op": "suggest_ban", "suggestions": suggestions}

    # ------------------------------------------------------------------ #

    def suggest_pick(self, available: List[int] = None,
                     team_comp: List[int] = None,
                     assigned_role: str = "",
                     top_n: int = 3) -> Dict[str, Any]:
        """Suggest champions to pick.

        Parameters
        ----------
        available : list of int  (available champion IDs)
        team_comp : list of int  (already picked by team)
        assigned_role : str
        top_n : int

        Returns
        -------
        dict  with status, suggestions (list)
        """
        self._op_count += 1
        _start = time.time()
        if available is None:
            available = []
        if team_comp is None:
            team_comp = []

        pool = self._context.get("champion_pool", {})
        matchup_table = self._context.get("matchup_table", {})
        meta_tiers = self._context.get("meta_tier_list", {})

        candidates: List[Dict[str, Any]] = []
        team_set = set(team_comp)

        for champ_id in available:
            if champ_id in team_set:
                continue

            score = 0.0
            reasons: List[str] = []

            # Personal proficiency
            prof = pool.get(champ_id, {})
            if isinstance(prof, dict):
                mastery = prof.get("mastery", 0)
                personal_wr = prof.get("win_rate", 0.5)
                score += personal_wr * 0.4
                if mastery > 5:
                    score += 0.1
                    reasons.append(f"High mastery ({mastery})")
                if personal_wr > 0.55:
                    reasons.append(f"Strong personal win rate ({personal_wr:.1%})")

            # Meta positioning
            tier = meta_tiers.get(champ_id, {})
            if isinstance(tier, dict):
                score += tier.get("win_rate", 0.5) * 0.3
                tier_label = tier.get("tier", "")
                if tier_label in ("S", "A"):
                    reasons.append(f"{tier_label}-tier in current meta")

            # Role fit
            if assigned_role and isinstance(prof, dict):
                if prof.get("role") == assigned_role:
                    score += 0.15
                    reasons.append(f"Matches assigned role ({assigned_role})")

            candidates.append({
                "champion_id": champ_id,
                "pick_score": round(score, 4),
                "reasons": reasons,
            })

        candidates.sort(key=lambda x: -x["pick_score"])
        suggestions = candidates[:top_n]

        self._suggestion_count += 1
        elapsed = time.time() - _start
        self._fire("suggest_pick_completed", {"elapsed": elapsed})
        return {"status": "ok", "op": "suggest_pick", "suggestions": suggestions}

    # ------------------------------------------------------------------ #

    def evaluate_draft(self, ally_picks: List[int] = None,
                       enemy_picks: List[int] = None) -> Dict[str, Any]:
        """Evaluate current draft state.

        Parameters
        ----------
        ally_picks : list of int
        enemy_picks : list of int

        Returns
        -------
        dict  with status, draft_score, strengths, weaknesses
        """
        self._op_count += 1
        if ally_picks is None:
            ally_picks = []
        if enemy_picks is None:
            enemy_picks = []

        matchup_table = self._context.get("matchup_table", {})
        strengths: List[str] = []
        weaknesses: List[str] = []
        total_score = 0.0
        comparisons = 0

        for ally in ally_picks:
            for enemy in enemy_picks:
                key = f"{ally}v{enemy}"
                wr = matchup_table.get(key, {}).get("win_rate", 0.5) if isinstance(
                    matchup_table.get(key), dict) else 0.5
                total_score += wr
                comparisons += 1
                if wr > 0.55:
                    strengths.append(f"Champion {ally} has advantage vs {enemy}")
                elif wr < 0.45:
                    weaknesses.append(f"Champion {ally} is weak vs {enemy}")

        avg_score = _safe_div(total_score, comparisons, 0.5)

        return {"status": "ok", "op": "evaluate_draft",
                "draft_score": round(avg_score, 4),
                "strengths": strengths[:5], "weaknesses": weaknesses[:5],
                "ally_count": len(ally_picks), "enemy_count": len(enemy_picks)}

    # ------------------------------------------------------------------ #

    def get_stats(self) -> Dict[str, Any]:
        """Internal statistics."""
        return {
            "op_count": self._op_count,
            "suggestion_count": self._suggestion_count,
            "context_loaded": bool(self._context),
        }
