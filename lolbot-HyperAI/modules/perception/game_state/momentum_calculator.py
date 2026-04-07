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


# ═══════════════════════════════════════════════════════════════════════════
# Claude21: MomentumCalculatorV2 — multi-signal momentum with gold velocity,
# kill streaks, objective chains, and team fight momentum tracking
# ═══════════════════════════════════════════════════════════════════════════


@dataclass
class MomentumSignal:
    """A single momentum signal from one data source.

    Claude21: Momentum is computed from multiple signals (gold, kills,
    objectives, towers). Each signal has its own weight and decay rate.
    """
    source: str            # "gold", "kills", "objectives", "towers"
    value: float           # -1.0 (red momentum) to 1.0 (blue momentum)
    weight: float = 1.0
    decay_rate: float = 0.05  # Per-second decay toward neutral
    last_update: float = 0.0

    def decayed_value(self, game_time: float) -> float:
        """Get value after time decay."""
        if self.last_update <= 0:
            return 0.0
        elapsed = game_time - self.last_update
        decay = max(0.0, 1.0 - self.decay_rate * elapsed)
        return self.value * decay

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "value": round(self.value, 4),
            "weight": self.weight,
        }


@dataclass
class MomentumSnapshot:
    """Complete momentum state at a point in time.

    Claude21: Published on /lol/momentum_snapshot for planning and
    prediction to consume.
    """
    game_time: float = 0.0
    composite: float = 0.0       # -1 (red) to +1 (blue)
    signals: Dict[str, float] = field(default_factory=dict)
    trend: str = "neutral"       # "blue_surging", "red_surging", "shifting", "neutral"
    streak_team: str = ""        # Team on a kill/objective streak
    streak_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "time": round(self.game_time, 1),
            "composite": round(self.composite, 4),
            "signals": {k: round(v, 4) for k, v in self.signals.items()},
            "trend": self.trend,
            "streak": f"{self.streak_team}x{self.streak_count}" if self.streak_team else "",
        }


class MomentumCalculatorV2(MomentumCalculator):
    """Production-grade momentum calculator with multi-signal fusion,
    streak detection, trend analysis, and composite scoring.

    Claude21: Extends MomentumCalculator with:
    - Multi-signal momentum (gold velocity, kill rate, objectives, towers)
    - Per-signal weight and decay rate
    - Kill/objective streak detection
    - Trend classification (surging, shifting, neutral)
    - Time-decaying signals (recent events weighted more)

    Apollo reference: modules/prediction/evaluator/model_manager.cc
    combines multiple prediction signals with weighted fusion.

    Usage::
        calc = MomentumCalculatorV2()
        calc.record_kill("BLUE", 300.0)
        calc.record_gold_diff(1500.0, 300.0)
        snap = calc.compute(300.0)
    """

    _SIGNAL_WEIGHTS = {
        "gold": 0.30,
        "kills": 0.25,
        "objectives": 0.30,
        "towers": 0.15,
    }

    def __init__(self) -> None:
        super().__init__()
        self._signals: Dict[str, MomentumSignal] = {
            "gold": MomentumSignal(source="gold", weight=0.30, decay_rate=0.01),
            "kills": MomentumSignal(source="kills", weight=0.25, decay_rate=0.03),
            "objectives": MomentumSignal(source="objectives", weight=0.30, decay_rate=0.02),
            "towers": MomentumSignal(source="towers", weight=0.15, decay_rate=0.005),
        }
        self._kill_history: List[Tuple[str, float]] = []  # (team, game_time)
        self._objective_history: List[Tuple[str, float, str]] = []  # (team, time, type)
        self._streak_team: str = ""
        self._streak_count: int = 0
        self._prev_composite: float = 0.0
        self._history: Deque[MomentumSnapshot] = deque(maxlen=300)

    def record_kill(self, team: str, game_time: float) -> None:
        """Record a kill event."""
        self._kill_history.append((team, game_time))
        # Update kill signal
        direction = 1.0 if team == "BLUE" else -1.0
        sig = self._signals["kills"]
        sig.value = max(-1.0, min(1.0, sig.value + direction * 0.15))
        sig.last_update = game_time
        # Track streaks
        if team == self._streak_team:
            self._streak_count += 1
        else:
            self._streak_team = team
            self._streak_count = 1

    def record_objective(
        self, team: str, game_time: float, obj_type: str = "",
    ) -> None:
        """Record an objective taken (dragon, baron, herald, tower)."""
        self._objective_history.append((team, game_time, obj_type))
        direction = 1.0 if team == "BLUE" else -1.0

        # Objectives have varying impact
        impact = 0.20
        if "baron" in obj_type.lower():
            impact = 0.40
        elif "dragon" in obj_type.lower():
            impact = 0.25
        elif "tower" in obj_type.lower() or "turret" in obj_type.lower():
            impact = 0.15
            self._signals["towers"].value = max(
                -1.0, min(1.0, self._signals["towers"].value + direction * 0.2)
            )
            self._signals["towers"].last_update = game_time

        sig = self._signals["objectives"]
        sig.value = max(-1.0, min(1.0, sig.value + direction * impact))
        sig.last_update = game_time

    def record_gold_diff(self, gold_diff: float, game_time: float) -> None:
        """Record current gold difference (blue - red)."""
        # Normalize gold diff to [-1, 1] with soft scaling
        # ±10000 gold maps to ±1.0
        normalized = max(-1.0, min(1.0, gold_diff / 10000.0))
        self._signals["gold"].value = normalized
        self._signals["gold"].last_update = game_time

    def compute(self, game_time: float) -> MomentumSnapshot:
        """Compute composite momentum from all signals.

        Claude21: Weighted average of decayed signals with trend detection.
        """
        # Compute weighted composite
        total_weight = 0.0
        composite = 0.0
        signal_values: Dict[str, float] = {}

        for name, sig in self._signals.items():
            decayed = sig.decayed_value(game_time)
            signal_values[name] = decayed
            composite += decayed * sig.weight
            total_weight += sig.weight

        if total_weight > 0:
            composite /= total_weight

        # Classify trend
        trend = "neutral"
        delta = composite - self._prev_composite
        if composite > 0.3 and delta > 0.02:
            trend = "blue_surging"
        elif composite < -0.3 and delta < -0.02:
            trend = "red_surging"
        elif abs(delta) > 0.05:
            trend = "shifting"

        self._prev_composite = composite

        snapshot = MomentumSnapshot(
            game_time=game_time,
            composite=composite,
            signals=signal_values,
            trend=trend,
            streak_team=self._streak_team,
            streak_count=self._streak_count,
        )
        self._history.append(snapshot)
        return snapshot

    def recent_momentum_trend(self, count: int = 10) -> List[float]:
        """Get recent composite momentum values for charting."""
        n = min(count, len(self._history))
        return [s.composite for s in list(self._history)[-n:]]

    def is_team_on_streak(self, min_count: int = 3) -> Optional[str]:
        """Check if a team is on a significant streak.

        Returns team name or None.
        """
        if self._streak_count >= min_count:
            return self._streak_team
        return None

    def extended_stats(self) -> Dict[str, Any]:
        base = self.momentum_stats() if hasattr(self, "momentum_stats") else {}
        latest = self._history[-1] if self._history else None
        base.update({
            "latest": latest.to_dict() if latest else {},
            "history_size": len(self._history),
            "kill_events": len(self._kill_history),
            "objective_events": len(self._objective_history),
            "streak": f"{self._streak_team}x{self._streak_count}" if self._streak_team else "none",
        })
        return base

    def reset(self) -> None:
        if hasattr(super(), "reset"):
            super().reset()
        for sig in self._signals.values():
            sig.value = 0.0
            sig.last_update = 0.0
        self._kill_history.clear()
        self._objective_history.clear()
        self._streak_team = ""
        self._streak_count = 0
        self._prev_composite = 0.0
        self._history.clear()
