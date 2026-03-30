"""
LiveMatchHistoryCorrelator — Correlates live game events with historical match
patterns to produce real-time strategic intelligence.

This is the key bridge between Seraphine's historical data (match history,
opponent stats, champion performance) and the live game decision loop.
During an active game, this module takes snapshots of live state and cross-
references them against the player's (or opponent's) match history to surface
actionable recommendations: "this opponent historically feeds early on this
champion", "your matchup winrate is 70% — play aggressive", etc.

Architecture (拿来主义 from Seraphine):
  - Seraphine/app/lol/tools.py: parseGames, parseGameData, getRecentChampions
  - Seraphine/app/lol/connector.py: getGameDetailByGameId response schema
  - integrations/lol-history/src/lol_history/player_profiler.py: profiling pattern
  - integrations/lol/src/lol_agent/opponent_history_merger.py: merge pattern

Location: integrations/lol-history/src/lol_history/live_match_history_correlator.py
"""

from __future__ import annotations

import logging
import math
import time
from collections import defaultdict
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "integrations.lol_history.live_match_history_correlator.v1"

# ---------------------------------------------------------------------------
# Phase boundaries (seconds) — mirrors Seraphine/tools.py game phase logic
# ---------------------------------------------------------------------------
EARLY_PHASE_END: int = 900       # 0-15 min
MID_PHASE_END: int = 1800        # 15-30 min
# > 30 min → late

# ---------------------------------------------------------------------------
# Confidence scaling — how many games are needed for high confidence
# ---------------------------------------------------------------------------
MIN_GAMES_FOR_CONFIDENCE: int = 5
MAX_GAMES_FOR_CONFIDENCE: int = 20


def _safe_div(numerator: float, denominator: float, default: float = 0.0) -> float:
    """Division that returns *default* when denominator is zero."""
    if denominator == 0:
        return default
    return numerator / denominator


def _kda(kills: int, deaths: int, assists: int) -> float:
    """Compute KDA ratio with floor-1 deaths to avoid division by zero."""
    return (kills + assists) / max(deaths, 1)


def _game_phase(seconds: int) -> str:
    """Classify game time into a phase string."""
    if seconds < EARLY_PHASE_END:
        return "early"
    if seconds < MID_PHASE_END:
        return "mid"
    return "late"


def _confidence_from_games(games: int) -> float:
    """Map game count → [0, 1] confidence using a log curve."""
    if games <= 0:
        return 0.0
    raw = math.log1p(games) / math.log1p(MAX_GAMES_FOR_CONFIDENCE)
    return max(0.0, min(1.0, raw))


# ===================================================================== #
#                    LiveMatchHistoryCorrelator                          #
# ===================================================================== #

class LiveMatchHistoryCorrelator:
    """Correlates live game events with historical match patterns.

    This class is the *analytical engine* that sits between Seraphine's
    historical data layer and the real-time LoL assistant.  It does **not**
    fetch data itself — callers supply live state dicts and match history
    lists, and the correlator returns structured intelligence.

    Public API
    ----------
    correlate_champion_performance(live_champion, history)
    correlate_matchup_history(my_champ, their_champ, history)
    correlate_time_phase(current_game_time, history)
    detect_streak_pattern(history)
    correlate_by_role(history)
    correlate_item_builds(history, champion_id)
    run_full_correlation(live_state, match_history)

    Evolution
    ---------
    Set ``evolution_callback`` to receive structured events whenever a
    correlation step completes.  Events carry ``_EVOLUTION_KEY`` as source
    so the governance layer can route them.

    Attributes
    ----------
    max_history_window : int
        Maximum number of recent matches to consider (default 20).
    evolution_callback : Optional[Callable]
        If set, called with an event dict after each correlation step.
    """

    # ------------------------------------------------------------------ #
    #  Construction
    # ------------------------------------------------------------------ #

    def __init__(
        self,
        max_history_window: int = 20,
    ) -> None:
        self.max_history_window: int = max_history_window
        self.evolution_callback: Optional[Callable[[Dict[str, Any]], None]] = None

        # internal counters
        self._correlation_count: int = 0
        self._pattern_cache: Dict[str, Any] = {}
        self._last_correlation_ts: float = 0.0

    # ------------------------------------------------------------------ #
    #  1. Champion Performance Correlation                                #
    # ------------------------------------------------------------------ #

    def correlate_champion_performance(
        self,
        live_champion: Dict[str, Any],
        history: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Cross-reference a live champion pick with historical performance.

        Mirrors Seraphine ``getRecentChampions`` logic: filter history by
        champion_id, aggregate wins/losses/KDA/CS.

        Parameters
        ----------
        live_champion : dict
            Must contain ``champion_id`` (int) and ``champion_name`` (str).
        history : list[dict]
            Each entry must have at minimum:
            ``champion_id``, ``win``, ``kills``, ``deaths``, ``assists``,
            ``cs``, ``game_duration``.

        Returns
        -------
        dict with keys:
            champion_id, champion_name, games_played, winrate, avg_kda,
            avg_cs_per_min, avg_kills, avg_deaths, avg_assists, confidence.
        """
        champ_id: int = live_champion.get("champion_id", 0)
        champ_name: str = live_champion.get("champion_name", "Unknown")

        filtered = [
            m for m in (history or [])
            if m.get("champion_id") == champ_id
        ][:self.max_history_window]

        games: int = len(filtered)
        if games == 0:
            result = {
                "champion_id": champ_id,
                "champion_name": champ_name,
                "games_played": 0,
                "winrate": 0.0,
                "avg_kda": 0.0,
                "avg_cs_per_min": 0.0,
                "avg_kills": 0.0,
                "avg_deaths": 0.0,
                "avg_assists": 0.0,
                "confidence": 0.0,
            }
            self._fire("champion_performance", result)
            return result

        wins = sum(1 for m in filtered if m.get("win"))
        total_kills = sum(m.get("kills", 0) for m in filtered)
        total_deaths = sum(m.get("deaths", 0) for m in filtered)
        total_assists = sum(m.get("assists", 0) for m in filtered)
        total_cs = sum(m.get("cs", 0) for m in filtered)
        total_duration_min = sum(m.get("game_duration", 1) for m in filtered) / 60.0

        result = {
            "champion_id": champ_id,
            "champion_name": champ_name,
            "games_played": games,
            "winrate": _safe_div(wins, games),
            "avg_kda": _kda(total_kills, total_deaths, total_assists),
            "avg_cs_per_min": _safe_div(total_cs, total_duration_min),
            "avg_kills": _safe_div(total_kills, games),
            "avg_deaths": _safe_div(total_deaths, games),
            "avg_assists": _safe_div(total_assists, games),
            "confidence": _confidence_from_games(games),
        }

        self._fire("champion_performance", {
            "champion": champ_name,
            "games": games,
            "winrate": result["winrate"],
        })
        return result

    # ------------------------------------------------------------------ #
    #  2. Matchup History Correlation                                     #
    # ------------------------------------------------------------------ #

    def correlate_matchup_history(
        self,
        my_champ: int,
        their_champ: int,
        history: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Analyse a specific champion-vs-champion matchup from history.

        Mirrors the matchup analysis that Seraphine's ``parseGameDetailData``
        enables: when we have ``opponent_champion_id`` per match, we can
        compute matchup-specific winrate and kill differentials.

        Parameters
        ----------
        my_champ, their_champ : int
            Champion IDs for our pick and the opponent's pick.
        history : list[dict]
            Must contain ``champion_id``, ``opponent_champion_id``, ``win``,
            ``kills``, ``deaths``.

        Returns
        -------
        dict with matchup_games, matchup_winrate, avg_kill_diff,
        matchup_confidence, recommendation.
        """
        filtered = [
            m for m in (history or [])
            if m.get("champion_id") == my_champ
            and m.get("opponent_champion_id") == their_champ
        ][:self.max_history_window]

        games = len(filtered)
        if games == 0:
            result = {
                "matchup_games": 0,
                "matchup_winrate": 0.0,
                "avg_kill_diff": 0.0,
                "matchup_confidence": 0.0,
                "recommendation": "No matchup data available — play standard and adapt.",
            }
            self._fire("matchup_history", result)
            return result

        wins = sum(1 for m in filtered if m.get("win"))
        winrate = _safe_div(wins, games)
        kill_diffs = [
            m.get("kills", 0) - m.get("deaths", 0) for m in filtered
        ]
        avg_kill_diff = _safe_div(sum(kill_diffs), games)
        confidence = _confidence_from_games(games)

        # Generate recommendation text
        if winrate >= 0.65:
            rec = (
                f"Strong matchup (WR {winrate:.0%} over {games} games). "
                "Play aggressive and look for early advantages."
            )
        elif winrate >= 0.45:
            rec = (
                f"Neutral matchup (WR {winrate:.0%} over {games} games). "
                "Play standard and focus on CS leads."
            )
        else:
            rec = (
                f"Difficult matchup (WR {winrate:.0%} over {games} games). "
                "Consider playing safe and farming under tower."
            )

        result = {
            "matchup_games": games,
            "matchup_winrate": winrate,
            "avg_kill_diff": avg_kill_diff,
            "matchup_confidence": confidence,
            "recommendation": rec,
        }
        self._fire("matchup_history", {
            "my_champ": my_champ,
            "their_champ": their_champ,
            "winrate": winrate,
            "games": games,
        })
        return result

    # ------------------------------------------------------------------ #
    #  3. Time Phase Correlation                                          #
    # ------------------------------------------------------------------ #

    def correlate_time_phase(
        self,
        current_game_time: int,
        history: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Correlate historical per-phase performance with current game time.

        Each history entry should carry ``early_kills``, ``early_deaths``,
        ``early_cs``, ``mid_*``, ``late_*`` breakdowns plus ``win`` and
        ``game_duration``.

        Parameters
        ----------
        current_game_time : int
            Seconds elapsed in the current game.
        history : list[dict]
            Phase-annotated match records.

        Returns
        -------
        dict with current_phase, phase_winrate, phase_kda, phase_cs_per_min,
        strongest_phase.
        """
        phase = _game_phase(current_game_time)
        history = (history or [])[:self.max_history_window]

        if not history:
            return {
                "current_phase": phase,
                "phase_winrate": 0.0,
                "phase_kda": 0.0,
                "phase_cs_per_min": 0.0,
                "strongest_phase": phase,
            }

        # --- aggregate per phase ---
        phase_stats: Dict[str, Dict[str, float]] = {
            "early": {"kills": 0, "deaths": 0, "cs": 0, "wins": 0, "games": 0},
            "mid":   {"kills": 0, "deaths": 0, "cs": 0, "wins": 0, "games": 0},
            "late":  {"kills": 0, "deaths": 0, "cs": 0, "wins": 0, "games": 0},
        }

        for m in history:
            dur = m.get("game_duration", 0)
            won = 1 if m.get("win") else 0

            # Early phase is always present in a game
            phase_stats["early"]["kills"] += m.get("early_kills", 0)
            phase_stats["early"]["deaths"] += m.get("early_deaths", 0)
            phase_stats["early"]["cs"] += m.get("early_cs", 0)
            phase_stats["early"]["wins"] += won
            phase_stats["early"]["games"] += 1

            # Mid phase: games longer than 15 min
            if dur >= EARLY_PHASE_END:
                phase_stats["mid"]["kills"] += m.get("mid_kills", 0)
                phase_stats["mid"]["deaths"] += m.get("mid_deaths", 0)
                phase_stats["mid"]["cs"] += m.get("mid_cs", 0)
                phase_stats["mid"]["wins"] += won
                phase_stats["mid"]["games"] += 1

            # Late phase: games longer than 30 min
            if dur >= MID_PHASE_END:
                phase_stats["late"]["kills"] += m.get("late_kills", 0)
                phase_stats["late"]["deaths"] += m.get("late_deaths", 0)
                phase_stats["late"]["cs"] += m.get("late_cs", 0)
                phase_stats["late"]["wins"] += won
                phase_stats["late"]["games"] += 1

        # --- current phase stats ---
        cur = phase_stats[phase]
        cur_wr = _safe_div(cur["wins"], cur["games"])
        cur_kda = _kda(int(cur["kills"]), int(cur["deaths"]), 0)
        phase_minutes = {
            "early": EARLY_PHASE_END / 60.0,
            "mid": (MID_PHASE_END - EARLY_PHASE_END) / 60.0,
            "late": 15.0,  # assume ~15 min late on average
        }
        cur_cs_min = _safe_div(cur["cs"], cur["games"] * phase_minutes.get(phase, 15))

        # --- strongest phase (highest combined KDA + winrate) ---
        best_phase = phase
        best_score = -1.0
        for p_name, p_data in phase_stats.items():
            if p_data["games"] == 0:
                continue
            p_wr = _safe_div(p_data["wins"], p_data["games"])
            p_kda = _kda(int(p_data["kills"]), int(p_data["deaths"]), 0)
            score = p_wr * 0.6 + min(p_kda / 10.0, 1.0) * 0.4
            if score > best_score:
                best_score = score
                best_phase = p_name

        result = {
            "current_phase": phase,
            "phase_winrate": cur_wr,
            "phase_kda": cur_kda,
            "phase_cs_per_min": cur_cs_min,
            "strongest_phase": best_phase,
        }
        self._fire("time_phase", {"phase": phase, "winrate": cur_wr})
        return result

    # ------------------------------------------------------------------ #
    #  4. Streak Pattern Detection                                        #
    # ------------------------------------------------------------------ #

    def detect_streak_pattern(
        self,
        history: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Detect win/loss streaks and compute tilt/momentum metrics.

        History should be sorted *most recent first* (latest game at index 0).

        Parameters
        ----------
        history : list[dict]
            Each entry needs ``win`` (bool) and optionally ``game_timestamp``.

        Returns
        -------
        dict with current_streak_type, current_streak_length,
        longest_win_streak, longest_loss_streak, tilt_probability,
        momentum_score.
        """
        history = (history or [])[:self.max_history_window]

        if not history:
            return {
                "current_streak_type": "none",
                "current_streak_length": 0,
                "longest_win_streak": 0,
                "longest_loss_streak": 0,
                "tilt_probability": 0.0,
                "momentum_score": 0.0,
            }

        # --- current streak (from most-recent end of list) ---
        # History may be sorted newest-first OR oldest-first; we detect by
        # looking at timestamps.  If no timestamps, assume index-0 is oldest.
        sorted_history = list(history)
        if len(sorted_history) >= 2:
            ts0 = sorted_history[0].get("game_timestamp", 0)
            ts1 = sorted_history[-1].get("game_timestamp", 0)
            if ts0 > ts1:
                # newest first → reverse so oldest first for streak algo
                sorted_history = list(reversed(sorted_history))

        # Walk from the end (most recent) backwards
        cur_type: str = "win" if sorted_history[-1].get("win") else "loss"
        cur_length: int = 0
        for m in reversed(sorted_history):
            is_win = m.get("win", False)
            if (cur_type == "win" and is_win) or (cur_type == "loss" and not is_win):
                cur_length += 1
            else:
                break

        # --- longest streaks ---
        longest_win: int = 0
        longest_loss: int = 0
        streak: int = 0
        streak_type: Optional[str] = None
        for m in sorted_history:
            is_win = m.get("win", False)
            t = "win" if is_win else "loss"
            if t == streak_type:
                streak += 1
            else:
                streak = 1
                streak_type = t
            if t == "win":
                longest_win = max(longest_win, streak)
            else:
                longest_loss = max(longest_loss, streak)

        # --- tilt probability ---
        # Recent losses weigh more.  A 3-loss streak = ~0.6 tilt, 5 = ~0.9.
        recent_losses = sum(
            1 for m in sorted_history[-5:] if not m.get("win")
        )
        tilt_prob = min(1.0, recent_losses / 5.0 * 0.9)
        if cur_type == "loss":
            tilt_prob = min(1.0, tilt_prob + 0.1 * cur_length)

        # --- momentum score: [-1, 1], positive = winning momentum ---
        wins_last_5 = sum(1 for m in sorted_history[-5:] if m.get("win"))
        momentum = (wins_last_5 - (5 - wins_last_5)) / 5.0  # [-1, 1]

        result = {
            "current_streak_type": cur_type,
            "current_streak_length": cur_length,
            "longest_win_streak": longest_win,
            "longest_loss_streak": longest_loss,
            "tilt_probability": round(tilt_prob, 4),
            "momentum_score": round(momentum, 4),
        }
        self._fire("streak_pattern", result)
        return result

    # ------------------------------------------------------------------ #
    #  5. Role-Specific Correlation                                       #
    # ------------------------------------------------------------------ #

    def correlate_by_role(
        self,
        history: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Aggregate performance per role/position.

        Parameters
        ----------
        history : list[dict]
            Entries need ``role`` (str), ``win``, ``kills``, ``deaths``,
            ``assists``, ``cs``.

        Returns
        -------
        dict with role_stats (per-role sub-dicts), best_role, worst_role.
        """
        history = (history or [])[:self.max_history_window]
        buckets: Dict[str, Dict[str, Any]] = defaultdict(
            lambda: {"games": 0, "wins": 0, "kills": 0, "deaths": 0, "assists": 0, "cs": 0}
        )

        for m in history:
            role = m.get("role", "UNKNOWN")
            b = buckets[role]
            b["games"] += 1
            b["wins"] += 1 if m.get("win") else 0
            b["kills"] += m.get("kills", 0)
            b["deaths"] += m.get("deaths", 0)
            b["assists"] += m.get("assists", 0)
            b["cs"] += m.get("cs", 0)

        role_stats: Dict[str, Dict[str, Any]] = {}
        for role, b in buckets.items():
            g = b["games"]
            role_stats[role] = {
                "games": g,
                "winrate": _safe_div(b["wins"], g),
                "avg_kda": _kda(b["kills"], b["deaths"], b["assists"]),
                "avg_cs": _safe_div(b["cs"], g),
            }

        best_role = max(role_stats, key=lambda r: role_stats[r]["winrate"]) if role_stats else ""
        worst_role = min(role_stats, key=lambda r: role_stats[r]["winrate"]) if role_stats else ""

        result = {
            "role_stats": dict(role_stats),
            "best_role": best_role,
            "worst_role": worst_role,
        }
        self._fire("role_correlation", {
            "roles": list(role_stats.keys()),
            "best": best_role,
        })
        return result

    # ------------------------------------------------------------------ #
    #  6. Item Build Path Correlation                                     #
    # ------------------------------------------------------------------ #

    def correlate_item_builds(
        self,
        history: List[Dict[str, Any]],
        champion_id: int = 0,
    ) -> Dict[str, Any]:
        """Analyse item build paths and their correlation with wins.

        Groups games by first item (items[0]) to find the highest-winrate
        opening, then ranks full build paths.

        Parameters
        ----------
        history : list[dict]
            Entries need ``items`` (list[int]), ``win``, ``champion_id``.
        champion_id : int
            If non-zero, filter to this champion.

        Returns
        -------
        dict with build_paths (sorted list), recommended_first_item.
        """
        filtered = history or []
        if champion_id:
            filtered = [m for m in filtered if m.get("champion_id") == champion_id]
        filtered = filtered[:self.max_history_window]

        if not filtered:
            return {"build_paths": [], "recommended_first_item": 0}

        # --- group by item tuple ---
        path_stats: Dict[Tuple[int, ...], Dict[str, Any]] = defaultdict(
            lambda: {"wins": 0, "games": 0}
        )
        first_item_stats: Dict[int, Dict[str, int]] = defaultdict(
            lambda: {"wins": 0, "games": 0}
        )

        for m in filtered:
            items = tuple(m.get("items", []))
            if not items:
                continue
            path_stats[items]["games"] += 1
            path_stats[items]["wins"] += 1 if m.get("win") else 0

            first = items[0]
            first_item_stats[first]["games"] += 1
            first_item_stats[first]["wins"] += 1 if m.get("win") else 0

        build_paths = []
        for items, stats in path_stats.items():
            build_paths.append({
                "items": list(items),
                "winrate": _safe_div(stats["wins"], stats["games"]),
                "games": stats["games"],
            })
        build_paths.sort(key=lambda b: (-b["winrate"], -b["games"]))

        # Recommended first item: highest winrate among first items with >=1 game
        best_first = 0
        best_wr = -1.0
        for item_id, stats in first_item_stats.items():
            wr = _safe_div(stats["wins"], stats["games"])
            if wr > best_wr or (wr == best_wr and stats["games"] > first_item_stats.get(best_first, {}).get("games", 0)):
                best_wr = wr
                best_first = item_id

        result = {
            "build_paths": build_paths,
            "recommended_first_item": best_first,
        }
        self._fire("item_builds", {
            "paths_analyzed": len(build_paths),
            "recommended": best_first,
        })
        return result

    # ------------------------------------------------------------------ #
    #  7. Full Correlation Pipeline                                       #
    # ------------------------------------------------------------------ #

    def run_full_correlation(
        self,
        live_state: Dict[str, Any],
        match_history: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Run the complete correlation pipeline and return a unified report.

        Parameters
        ----------
        live_state : dict
            Keys: game_time, my_champion, opponent_champion, my_team,
            all_players.
        match_history : list[dict]
            Raw match history records.

        Returns
        -------
        dict with champion_correlation, matchup_correlation,
        time_phase_correlation, streak_pattern, role_correlation,
        item_correlation, overall_confidence, strategic_summary.
        """
        self._correlation_count += 1
        self._last_correlation_ts = time.time()

        game_time = live_state.get("game_time", 0)
        my_champ = live_state.get("my_champion", {})
        opp_champ = live_state.get("opponent_champion", {})

        # 1 — champion performance
        champ_corr = self.correlate_champion_performance(my_champ, match_history)

        # 2 — matchup
        matchup_corr = self.correlate_matchup_history(
            my_champ.get("champion_id", 0),
            opp_champ.get("champion_id", 0),
            match_history,
        )

        # 3 — time phase
        time_corr = self.correlate_time_phase(game_time, match_history)

        # 4 — streak
        streak = self.detect_streak_pattern(match_history)

        # 5 — role
        role_corr = self.correlate_by_role(match_history)

        # 6 — item builds
        item_corr = self.correlate_item_builds(
            match_history,
            champion_id=my_champ.get("champion_id", 0),
        )

        # --- overall confidence ---
        confidences = [
            champ_corr.get("confidence", 0),
            matchup_corr.get("matchup_confidence", 0),
            _confidence_from_games(len(match_history)),
        ]
        overall_conf = sum(confidences) / max(len(confidences), 1)

        # --- strategic summary ---
        summary_parts: List[str] = []

        wr = champ_corr.get("winrate", 0)
        if wr >= 0.6:
            summary_parts.append(
                f"You have a strong {wr:.0%} winrate on {my_champ.get('champion_name', 'this champion')}."
            )
        elif wr > 0:
            summary_parts.append(
                f"Your winrate on {my_champ.get('champion_name', 'this champion')} is {wr:.0%}."
            )

        mu_wr = matchup_corr.get("matchup_winrate", 0)
        mu_games = matchup_corr.get("matchup_games", 0)
        if mu_games > 0:
            summary_parts.append(
                f"Matchup history: {mu_wr:.0%} WR over {mu_games} games vs {opp_champ.get('champion_name', 'opponent')}."
            )

        strongest = time_corr.get("strongest_phase", "")
        if strongest:
            summary_parts.append(f"Historically strongest in {strongest} game.")

        tilt = streak.get("tilt_probability", 0)
        if tilt > 0.5:
            summary_parts.append("Warning: recent loss streak detected — stay focused.")

        strategic_summary = " ".join(summary_parts) if summary_parts else "Insufficient data for strategic summary."

        result = {
            "champion_correlation": champ_corr,
            "matchup_correlation": matchup_corr,
            "time_phase_correlation": time_corr,
            "streak_pattern": streak,
            "role_correlation": role_corr,
            "item_correlation": item_corr,
            "overall_confidence": round(overall_conf, 4),
            "strategic_summary": strategic_summary,
        }

        self._fire("full_correlation", {
            "overall_confidence": overall_conf,
            "summary_length": len(strategic_summary),
            "correlation_id": self._correlation_count,
        })
        return result

    # ------------------------------------------------------------------ #
    #  Internal helpers                                                   #
    # ------------------------------------------------------------------ #

    def _fire(self, event_type: str, payload: Dict[str, Any]) -> None:
        """Dispatch an evolution event if a callback is registered."""
        if self.evolution_callback is not None:
            self.evolution_callback({
                "source": _EVOLUTION_KEY,
                "type": event_type,
                "timestamp": time.time(),
                "payload": payload,
            })

    # ------------------------------------------------------------------ #
    #  Cache Management                                                   #
    # ------------------------------------------------------------------ #

    def invalidate_cache(self) -> None:
        """Clear the internal pattern cache."""
        self._pattern_cache.clear()

    def cache_put(self, key: str, value: Any) -> None:
        """Store a value in the pattern cache."""
        self._pattern_cache[key] = {
            "value": value,
            "ts": time.time(),
        }

    def cache_get(self, key: str, max_age: float = 300.0) -> Optional[Any]:
        """Retrieve a cached value if fresh enough."""
        entry = self._pattern_cache.get(key)
        if entry is None:
            return None
        if time.time() - entry["ts"] > max_age:
            del self._pattern_cache[key]
            return None
        return entry["value"]

    # ------------------------------------------------------------------ #
    #  Diagnostics                                                        #
    # ------------------------------------------------------------------ #

    def get_diagnostics(self) -> Dict[str, Any]:
        """Return internal counters for observability."""
        return {
            "correlation_count": self._correlation_count,
            "cache_size": len(self._pattern_cache),
            "last_correlation_ts": self._last_correlation_ts,
            "max_history_window": self.max_history_window,
            "evolution_key": _EVOLUTION_KEY,
        }
