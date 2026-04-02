"""
MomentumCalculator — Event-weighted momentum scoring [-1, +1].
================================================================
lolbot-HyperAI · Perception Layer

Computes per-team momentum based on recent events (kills, objectives,
towers) with exponential decay.  Used by GameStateParser to enrich
GameSnapshot with a momentum_score field.

Architecture position:
    modules/perception/game_state/momentum_calculator.py   ← YOU ARE HERE
    ├─ Called by: PerceptionComponent (during snapshot assembly)
    ├─ Input: List[GameEvent], game_time
    ├─ Output: MomentumScore dataclass
    └─ Consumed by: prediction feature extraction

Apollo reference:
    modules/perception/camera/tracker/omt/track_object.cc
    — temporal smoothing of tracked entity attributes

Design notes:
    - Half-life 30s: events 30s ago contribute half weight
    - Window 60s: ignore events older than 60s
    - Normalization: raw scores capped at [-1, +1]
    - Thread-safe: stateless computation, all state passed in
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from modules.common.adapters.game_messages import (
    EventType,
    GameEvent,
    TeamSide,
)

# ─── Constants ───────────────────────────────────────────────────────────────

_HALF_LIFE_S = 30.0
_WINDOW_S = 60.0
_DECAY_LAMBDA = math.log(2) / _HALF_LIFE_S

# Event weight table
_EVENT_WEIGHTS: Dict[str, float] = {
    "ChampionKill": 0.20,
    "DragonKill": 0.30,
    "BaronKill": 0.45,
    "HeraldKill": 0.25,
    "TurretKilled": 0.15,
    "InhibKilled": 0.35,
    "FirstBlood": 0.25,
}

# Maximum raw score before normalization
_MAX_RAW_SCORE = 2.0


@dataclass(frozen=True)
class MomentumScore:
    """Computed momentum for both teams."""
    blue: float = 0.0       # [-1, +1]
    red: float = 0.0        # [-1, +1]
    net: float = 0.0        # blue - red, [-2, +2] but typically [-1, +1]
    trend: str = "stable"   # "blue_surging", "red_surging", "stable"
    dominant_factor: str = ""  # what's driving the momentum
    game_time: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "blue": round(self.blue, 3),
            "red": round(self.red, 3),
            "net": round(self.net, 3),
            "trend": self.trend,
            "dominant_factor": self.dominant_factor,
        }


class MomentumCalculator:
    """Stateless momentum calculator.

    Usage::
        calc = MomentumCalculator()
        score = calc.compute(events, game_time, player_teams)
    """

    def compute(
        self,
        events: List[GameEvent],
        game_time: float,
        player_teams: Optional[Dict[str, TeamSide]] = None,
    ) -> MomentumScore:
        """Compute momentum from event history.

        Args:
            events: All game events (filtered internally by window).
            game_time: Current game time in seconds.
            player_teams: Optional mapping of player_name → TeamSide.

        Returns:
            MomentumScore with per-team values.
        """
        if game_time <= 0 or not events:
            return MomentumScore(game_time=game_time)

        cutoff = game_time - _WINDOW_S
        blue_raw = 0.0
        red_raw = 0.0
        blue_factors: Dict[str, float] = {}
        red_factors: Dict[str, float] = {}

        for event in events:
            if event.game_time < cutoff:
                continue
            if event.game_time > game_time:
                continue

            evt_name = event.event_type.value if hasattr(event.event_type, 'value') else str(event.event_type)
            weight = _EVENT_WEIGHTS.get(evt_name, 0.0)
            if weight <= 0:
                continue

            age = game_time - event.game_time
            decayed = weight * math.exp(-_DECAY_LAMBDA * age)

            # Determine team from killer name
            team = self._resolve_team(event.killer, player_teams)

            if team == TeamSide.BLUE:
                blue_raw += decayed
                blue_factors[evt_name] = blue_factors.get(evt_name, 0.0) + decayed
            elif team == TeamSide.RED:
                red_raw += decayed
                red_factors[evt_name] = red_factors.get(evt_name, 0.0) + decayed

        # Normalize to [-1, +1]
        blue_norm = max(-1.0, min(1.0, blue_raw / _MAX_RAW_SCORE))
        red_norm = max(-1.0, min(1.0, red_raw / _MAX_RAW_SCORE))
        net = blue_norm - red_norm

        # Determine trend
        if net > 0.3:
            trend = "blue_surging"
        elif net < -0.3:
            trend = "red_surging"
        else:
            trend = "stable"

        # Dominant factor
        all_factors = {}
        for k, v in blue_factors.items():
            all_factors[f"blue_{k}"] = v
        for k, v in red_factors.items():
            all_factors[f"red_{k}"] = v

        dominant = ""
        if all_factors:
            dominant = max(all_factors, key=all_factors.get)

        return MomentumScore(
            blue=blue_norm,
            red=red_norm,
            net=round(net, 3),
            trend=trend,
            dominant_factor=dominant,
            game_time=game_time,
        )

    @staticmethod
    def _resolve_team(
        killer_name: str,
        player_teams: Optional[Dict[str, TeamSide]],
    ) -> TeamSide:
        if player_teams and killer_name in player_teams:
            return player_teams[killer_name]
        return TeamSide.UNKNOWN
