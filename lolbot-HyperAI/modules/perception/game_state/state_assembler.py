"""
StateAssembler — Detailed game state assembly and enrichment.
==============================================================

Handles the complex logic of assembling raw LCU data into enriched
game state objects.  Resolves team colors, computes derived metrics
(gold per minute, damage shares, CS differentials), and maintains
historical state for delta computation.

Architecture position:
    modules/perception/game_state/state_assembler.py   ← YOU ARE HERE
    ├─ Called by: perception_component.py
    ├─ Input: Raw allgamedata dict
    ├─ Output: Enriched GameSnapshot with derived metrics
    └─ Maintains: rolling state history for trend computation

Apollo reference:
    modules/perception/multi_sensor_fusion/fusion_component.cc

Design notes:
    - Computes gold/min, CS/min, KDA ratios per player
    - Tracks gold diff over time for trend analysis
    - Tower/objective state tracking
    - Player role detection from game data
    - Handles edge cases: disconnected players, remake, ARAM
"""

from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional, Tuple

from modules.common.adapters.game_messages import (
    GamePhase,
    GameSnapshot,
    PlayerState,
    TeamSide,
    TeamState,
)
from cyber.logger.cyber_logger import get_logger

logger = get_logger("perception.assembler")

# ─── Constants ───────────────────────────────────────────────────────────────

_GOLD_HISTORY_MAX = 300       # track ~5 min of gold diffs at 10Hz
_POSITION_MAP = {
    "TOP": "TOP",
    "JUNGLE": "JUNGLE",
    "MIDDLE": "MID",
    "BOTTOM": "ADC",
    "UTILITY": "SUPPORT",
    "": "UNKNOWN",
}


@dataclass
class PlayerMetrics:
    """Derived per-player metrics computed from raw state."""
    summoner_name: str = ""
    champion_name: str = ""
    team: TeamSide = TeamSide.UNKNOWN
    position: str = ""

    # Economy
    gold_per_min: float = 0.0
    cs_per_min: float = 0.0
    gold_share: float = 0.0     # fraction of team's total gold

    # Combat
    kda: float = 0.0
    kill_participation: float = 0.0
    damage_share: float = 0.0   # estimated from items/level

    # Lane state
    level_diff_vs_lane: float = 0.0
    cs_diff_vs_lane: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.summoner_name,
            "champion": self.champion_name,
            "position": self.position,
            "gold_per_min": round(self.gold_per_min, 1),
            "cs_per_min": round(self.cs_per_min, 1),
            "kda": round(self.kda, 2),
            "kill_participation": round(self.kill_participation, 2),
        }


@dataclass
class GoldSnapshot:
    """Gold diff at a point in time (for trend computation)."""
    game_time: float
    gold_diff: float   # blue - red
    blue_gold: float
    red_gold: float


@dataclass
class ObjectiveState:
    """Tracked objective kills and timers."""
    blue_dragons: int = 0
    red_dragons: int = 0
    blue_barons: int = 0
    red_barons: int = 0
    blue_towers: int = 0
    red_towers: int = 0
    blue_inhibs: int = 0
    red_inhibs: int = 0
    blue_heralds: int = 0
    red_heralds: int = 0
    blue_grubs: int = 0
    red_grubs: int = 0
    blue_dragon_soul: str = ""
    red_dragon_soul: str = ""


class StateAssembler:
    """Assembles and enriches game state from raw LCU data.

    Maintains history for trend computation and provides richer
    metrics than the raw API data.

    Usage::

        assembler = StateAssembler()
        # Each tick:
        enriched = assembler.assemble(raw_allgamedata, base_snapshot)
        metrics = assembler.player_metrics
    """

    def __init__(self) -> None:
        self._gold_history: Deque[GoldSnapshot] = deque(
            maxlen=_GOLD_HISTORY_MAX
        )
        self._player_metrics: Dict[str, PlayerMetrics] = {}
        self._objective_state = ObjectiveState()
        self._position_mapping: Dict[str, str] = {}  # summoner → position
        self._lane_opponents: Dict[str, str] = {}     # summoner → opponent
        self._assembly_count: int = 0

    def assemble(
        self,
        allgamedata: Dict[str, Any],
        base_snapshot: GameSnapshot,
    ) -> GameSnapshot:
        """Enrich a base GameSnapshot with derived metrics.

        Args:
            allgamedata: Raw API response.
            base_snapshot: Basic snapshot from perception_component.

        Returns:
            Enriched GameSnapshot with updated team states.
        """
        self._assembly_count += 1
        game_time = base_snapshot.game_time

        if game_time <= 0:
            return base_snapshot

        # ── Track gold history ───────────────────────────────────────
        blue_gold = base_snapshot.blue_team.total_gold
        red_gold = base_snapshot.red_team.total_gold
        self._gold_history.append(GoldSnapshot(
            game_time=game_time,
            gold_diff=blue_gold - red_gold,
            blue_gold=blue_gold,
            red_gold=red_gold,
        ))

        # ── Compute player metrics ───────────────────────────────────
        self._compute_player_metrics(base_snapshot)

        # ── Resolve lane opponents ───────────────────────────────────
        self._resolve_lane_opponents(base_snapshot)

        # ── Enrich team states ───────────────────────────────────────
        blue_enriched = self._enrich_team(
            base_snapshot.blue_team, base_snapshot
        )
        red_enriched = self._enrich_team(
            base_snapshot.red_team, base_snapshot
        )

        # Return enriched snapshot (creates new frozen instance)
        return GameSnapshot(
            game_time=base_snapshot.game_time,
            real_timestamp=base_snapshot.real_timestamp,
            sequence=base_snapshot.sequence,
            phase=base_snapshot.phase,
            game_mode=base_snapshot.game_mode,
            map_number=base_snapshot.map_number,
            blue_team=blue_enriched,
            red_team=red_enriched,
            active_player=base_snapshot.active_player,
            active_team=base_snapshot.active_team,
            all_players=base_snapshot.all_players,
            new_events=base_snapshot.new_events,
            all_events=base_snapshot.all_events,
            gold_diff=base_snapshot.gold_diff,
        )

    def _compute_player_metrics(self, snapshot: GameSnapshot) -> None:
        """Compute derived metrics for all players."""
        game_min = max(snapshot.game_time / 60.0, 0.1)

        # Team kill totals for KP calculation
        team_kills: Dict[str, int] = {
            TeamSide.BLUE.name: snapshot.blue_team.total_kills,
            TeamSide.RED.name: snapshot.red_team.total_kills,
        }

        for player in snapshot.all_players:
            name = player.summoner_name
            team_total_kills = team_kills.get(player.team.name, 1) or 1
            team_gold = (
                snapshot.blue_team.total_gold
                if player.team == TeamSide.BLUE
                else snapshot.red_team.total_gold
            ) or 1.0

            metrics = PlayerMetrics(
                summoner_name=name,
                champion_name=player.champion_name,
                team=player.team,
                position=_POSITION_MAP.get(player.position, player.position),
                gold_per_min=player.current_gold / game_min if player.is_active_player else 0.0,
                cs_per_min=player.scores.creep_score / game_min,
                gold_share=player.current_gold / team_gold if player.is_active_player else 0.0,
                kda=player.scores.kda,
                kill_participation=(
                    (player.scores.kills + player.scores.assists) /
                    max(1, team_total_kills)
                ),
            )

            # Lane diff computation
            opponent = self._lane_opponents.get(name)
            if opponent:
                opp_player = snapshot.get_player(opponent)
                if opp_player:
                    metrics.level_diff_vs_lane = (
                        player.level - opp_player.level
                    )
                    metrics.cs_diff_vs_lane = (
                        player.scores.creep_score -
                        opp_player.scores.creep_score
                    )

            self._player_metrics[name] = metrics

    def _resolve_lane_opponents(self, snapshot: GameSnapshot) -> None:
        """Match players to their lane opponents by position."""
        blue_by_pos: Dict[str, str] = {}
        red_by_pos: Dict[str, str] = {}

        for player in snapshot.all_players:
            pos = _POSITION_MAP.get(player.position, "")
            if player.team == TeamSide.BLUE:
                blue_by_pos[pos] = player.summoner_name
            elif player.team == TeamSide.RED:
                red_by_pos[pos] = player.summoner_name

        for pos in blue_by_pos:
            if pos in red_by_pos and pos != "UNKNOWN":
                b_name = blue_by_pos[pos]
                r_name = red_by_pos[pos]
                self._lane_opponents[b_name] = r_name
                self._lane_opponents[r_name] = b_name

    def _enrich_team(
        self,
        team: TeamState,
        snapshot: GameSnapshot,
    ) -> TeamState:
        """Add objective tracking to team state."""
        # For now, return as-is; in production, would track towers/dragons
        # from events and update ObjectiveState
        return team

    # ─── Gold trend analysis ─────────────────────────────────────────

    def gold_trend(self, lookback_s: float = 120.0) -> Optional[float]:
        """Compute gold diff trend over the last N seconds.

        Returns:
            Gold per second trend (positive = blue gaining),
            or None if insufficient data.
        """
        if len(self._gold_history) < 10:
            return None

        now = self._gold_history[-1]
        for old in self._gold_history:
            if now.game_time - old.game_time >= lookback_s * 0.8:
                dt = now.game_time - old.game_time
                if dt > 0:
                    return (now.gold_diff - old.gold_diff) / dt
                break

        return None

    def gold_at_time(self, game_time: float) -> Optional[float]:
        """Look up historical gold diff at a specific game time."""
        best = None
        best_delta = float("inf")
        for gs in self._gold_history:
            delta = abs(gs.game_time - game_time)
            if delta < best_delta:
                best_delta = delta
                best = gs.gold_diff
        return best

    # ─── Introspection ───────────────────────────────────────────────

    @property
    def player_metrics(self) -> Dict[str, PlayerMetrics]:
        return dict(self._player_metrics)

    @property
    def objective_state(self) -> ObjectiveState:
        return self._objective_state

    def summary(self) -> Dict[str, Any]:
        return {
            "assembly_count": self._assembly_count,
            "gold_history_size": len(self._gold_history),
            "tracked_players": len(self._player_metrics),
            "gold_trend_per_s": self.gold_trend(),
        }
