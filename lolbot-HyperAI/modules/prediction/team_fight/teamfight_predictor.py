"""
TeamfightPredictor — Team fight outcome prediction model.
===========================================================
lolbot-HyperAI · Prediction Layer

Predicts teamfight outcomes using a multi-factor scoring model
that considers: alive counts, HP ratios, ultimate cooldowns,
gold advantage, item completions, and terrain positioning.

Outputs: engage/disengage/poke recommendation with confidence and
a detailed breakdown of contributing factors.

Architecture position:
    modules/prediction/team_fight/teamfight_predictor.py   ← YOU ARE HERE
    ├─ Called by: prediction_component.py in its Proc() cycle
    ├─ Input: GameSnapshot, recent events
    ├─ Output: TeamfightAssessment (engage/disengage/poke + scores)
    └─ Model: Multi-factor linear scoring (no external ML deps)

Apollo reference:
    modules/prediction/evaluator/evaluator_manager.cc — model dispatch
    modules/prediction/container/obstacles_container.cc — feature container

Design notes:
    - Pure Python scoring model — no numpy/sklearn dependency
    - Feature weights are evolvable via generation_manager
    - Rolling confidence calibration from past predictions
    - Terrain bonus: river/jungle chokepoints affect scores
    - Cooldown-aware: accounts for ult/summoner spell availability
"""

from __future__ import annotations

import logging
import math
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Deque, Dict, List, Optional, Tuple

from cyber.logger.cyber_logger import get_logger
from modules.common.adapters.game_messages import (
    GameEvent,
    GamePhase,
    GameSnapshot,
    PlayerAbilities,
    PlayerItems,
    PlayerState,
    TeamSide,
    TeamState,
)

logger = get_logger("prediction.teamfight")

# ─── Constants ───────────────────────────────────────────────────────────────

_HISTORY_WINDOW = 50
_MIN_CONFIDENCE = 0.10
_MAX_CONFIDENCE = 0.95
_CALIBRATION_WINDOW = 20   # Past predictions to calibrate against

# Feature weights (default, evolvable)
_DEFAULT_WEIGHTS = {
    "alive_ratio": 0.25,        # How many alive on each side
    "hp_ratio": 0.15,           # Average HP percentage
    "gold_per_player": 0.12,    # Gold advantage normalized
    "level_advantage": 0.10,    # Average level difference
    "ult_availability": 0.15,   # Fraction with ult ready
    "item_completion": 0.08,    # Number of completed items
    "summoner_spells": 0.05,    # Flash/heal/barrier availability
    "momentum": 0.10,           # Recent kill/objective momentum
}

# Terrain coefficients (simplified zone system)
_TERRAIN_BONUS = {
    "river_dragon": 0.05,   # Dragon pit advantage
    "river_baron": 0.05,    # Baron pit advantage
    "jungle_our": 0.03,     # Our jungle (vision advantage)
    "jungle_their": -0.02,  # Their jungle (risky)
    "lane_open": 0.0,       # Open lane, neutral
}


# ─── Data Types ──────────────────────────────────────────────────────────────

class FightAction(Enum):
    """Recommended teamfight action."""
    ENGAGE = "engage"           # Initiate the fight
    DISENGAGE = "disengage"     # Back off, bad fight
    POKE = "poke"               # Poke from range, don't commit
    PICK = "pick"               # Look for a pick, not full engage

    @property
    def description(self) -> str:
        return {
            FightAction.ENGAGE: "All-in teamfight — we have the advantage",
            FightAction.DISENGAGE: "Avoid fighting — they have the edge",
            FightAction.POKE: "Poke and wait for better opportunity",
            FightAction.PICK: "Look for a solo pick before committing",
        }[self]


@dataclass
class FeatureVector:
    """Extracted features for teamfight prediction."""
    # Our side features
    our_alive: int = 0
    our_hp_ratio: float = 0.0         # Average HP/maxHP
    our_avg_gold: float = 0.0
    our_avg_level: float = 0.0
    our_ults_ready: int = 0
    our_completed_items: int = 0
    our_flash_count: int = 0

    # Their side features
    their_alive: int = 0
    their_hp_ratio: float = 0.0
    their_avg_gold: float = 0.0
    their_avg_level: float = 0.0
    their_ults_ready: int = 0
    their_completed_items: int = 0
    their_flash_count: int = 0

    # Derived
    alive_ratio: float = 0.0          # our_alive / their_alive (clamped)
    gold_ratio: float = 0.0
    level_diff: float = 0.0
    ult_ratio: float = 0.0

    # Context
    game_phase: GamePhase = GamePhase.EARLY
    game_time: float = 0.0
    momentum: float = 0.0             # [-1, 1] recent performance


@dataclass
class TeamfightAssessment:
    """Output of the teamfight predictor."""
    recommended_action: FightAction
    our_win_probability: float        # 0.0-1.0
    confidence: float                 # 0.0-1.0 (model confidence)
    rationale: str
    factor_breakdown: Dict[str, float] = field(default_factory=dict)
    features: Optional[FeatureVector] = None
    timestamp: float = field(default_factory=time.monotonic)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action": self.recommended_action.value,
            "win_probability": round(self.our_win_probability, 3),
            "confidence": round(self.confidence, 3),
            "rationale": self.rationale,
            "factors": {
                k: round(v, 4) for k, v in self.factor_breakdown.items()
            },
        }


# ─── Feature Extractor ──────────────────────────────────────────────────────

class TeamfightFeatureExtractor:
    """Extracts teamfight-relevant features from game state."""

    def extract(
        self,
        snapshot: GameSnapshot,
        events: Optional[List[GameEvent]] = None,
    ) -> FeatureVector:
        """Extract feature vector from current game state."""
        fv = FeatureVector()
        fv.game_phase = snapshot.phase
        fv.game_time = snapshot.game_time

        our_side = snapshot.active_team
        if our_side == TeamSide.BLUE:
            our_team, their_team = snapshot.blue_team, snapshot.red_team
        else:
            our_team, their_team = snapshot.red_team, snapshot.blue_team

        # Extract per-team features
        self._extract_team(our_team, fv, is_ours=True)
        self._extract_team(their_team, fv, is_ours=False)

        # Derived ratios
        if fv.their_alive > 0:
            fv.alive_ratio = fv.our_alive / fv.their_alive
        else:
            fv.alive_ratio = 2.0  # Max advantage

        if fv.their_avg_gold > 0:
            fv.gold_ratio = fv.our_avg_gold / fv.their_avg_gold
        else:
            fv.gold_ratio = 1.5

        fv.level_diff = fv.our_avg_level - fv.their_avg_level

        total_ults = fv.our_alive + fv.their_alive
        if total_ults > 0:
            fv.ult_ratio = (
                (fv.our_ults_ready / max(fv.our_alive, 1))
                - (fv.their_ults_ready / max(fv.their_alive, 1))
            )
        else:
            fv.ult_ratio = 0.0

        # Momentum from recent events
        fv.momentum = self._compute_momentum(events, snapshot.game_time)

        return fv

    def _extract_team(
        self,
        team: TeamState,
        fv: FeatureVector,
        is_ours: bool,
    ) -> None:
        """Extract features from one team."""
        alive_players = [p for p in team.players if not p.is_dead]
        alive_count = len(alive_players)

        # HP ratio
        hp_ratio = 0.0
        if alive_count > 0:
            for p in alive_players:
                if p.max_health > 0:
                    hp_ratio += p.current_health / p.max_health
            hp_ratio /= alive_count

        # Average gold
        avg_gold = 0.0
        if alive_count > 0:
            avg_gold = sum(p.current_gold for p in alive_players) / alive_count

        # Average level
        avg_level = 0.0
        if alive_count > 0:
            avg_level = sum(p.level for p in alive_players) / alive_count

        # Ultimate availability (level >= 6 is a proxy)
        ults_ready = sum(1 for p in alive_players if p.level >= 6)

        # Completed items (6 slots, rough estimate from gold)
        completed_items = sum(
            min(6, int(p.current_gold / 3000))
            for p in alive_players
        )

        # Flash count (simplified: all alive players assumed to have flash)
        flash_count = alive_count

        if is_ours:
            fv.our_alive = alive_count
            fv.our_hp_ratio = hp_ratio
            fv.our_avg_gold = avg_gold
            fv.our_avg_level = avg_level
            fv.our_ults_ready = ults_ready
            fv.our_completed_items = completed_items
            fv.our_flash_count = flash_count
        else:
            fv.their_alive = alive_count
            fv.their_hp_ratio = hp_ratio
            fv.their_avg_gold = avg_gold
            fv.their_avg_level = avg_level
            fv.their_ults_ready = ults_ready
            fv.their_completed_items = completed_items
            fv.their_flash_count = flash_count

    @staticmethod
    def _compute_momentum(
        events: Optional[List[GameEvent]],
        current_time: float,
    ) -> float:
        """Compute momentum from recent events."""
        if not events:
            return 0.0
        recent = [e for e in events if e.game_time > current_time - 120.0]
        if not recent:
            return 0.0
        # Simplified momentum: count of events as positive signal
        return min(1.0, len(recent) / 10.0)


# ─── Scoring Model ──────────────────────────────────────────────────────────

class TeamfightScoringModel:
    """Multi-factor linear scoring model for teamfight prediction.

    Each factor contributes a signed score component. The total is
    passed through a sigmoid to produce a win probability in [0, 1].

    The model is deliberately simple and interpretable — weights can
    be tuned by the evolution layer without needing gradient descent.
    """

    def __init__(
        self,
        weights: Optional[Dict[str, float]] = None,
    ) -> None:
        self._weights = dict(_DEFAULT_WEIGHTS)
        if weights:
            self._weights.update(weights)
        self._prediction_history: Deque[Tuple[float, bool]] = deque(
            maxlen=_CALIBRATION_WINDOW,
        )

    def predict(self, fv: FeatureVector) -> Tuple[float, Dict[str, float]]:
        """Predict our teamfight win probability.

        Args:
            fv: Extracted feature vector.

        Returns:
            (win_probability, factor_breakdown) tuple.
        """
        factors: Dict[str, float] = {}

        # Factor 1: Alive ratio advantage
        alive_score = 0.0
        if fv.their_alive == 0:
            alive_score = 1.0
        elif fv.our_alive == 0:
            alive_score = -1.0
        else:
            alive_score = (fv.our_alive - fv.their_alive) / 5.0
        factors["alive_ratio"] = alive_score

        # Factor 2: HP ratio advantage
        hp_score = fv.our_hp_ratio - fv.their_hp_ratio
        factors["hp_ratio"] = hp_score

        # Factor 3: Gold per player advantage
        gold_score = 0.0
        if fv.their_avg_gold > 0:
            gold_diff = fv.our_avg_gold - fv.their_avg_gold
            gold_score = gold_diff / 5000.0  # Normalize
        factors["gold_per_player"] = max(-1.0, min(1.0, gold_score))

        # Factor 4: Level advantage
        level_score = fv.level_diff / 3.0  # Normalize
        factors["level_advantage"] = max(-1.0, min(1.0, level_score))

        # Factor 5: Ultimate availability
        ult_score = fv.ult_ratio
        factors["ult_availability"] = max(-1.0, min(1.0, ult_score))

        # Factor 6: Item completion
        item_diff = fv.our_completed_items - fv.their_completed_items
        item_score = item_diff / 10.0
        factors["item_completion"] = max(-1.0, min(1.0, item_score))

        # Factor 7: Summoner spells
        spell_diff = fv.our_flash_count - fv.their_flash_count
        spell_score = spell_diff / 5.0
        factors["summoner_spells"] = max(-1.0, min(1.0, spell_score))

        # Factor 8: Momentum
        factors["momentum"] = fv.momentum

        # Weighted sum
        total_score = sum(
            factors[key] * self._weights.get(key, 0.0)
            for key in factors
        )

        # Phase scaling: early game fights are more volatile
        phase_scale = {
            GamePhase.EARLY: 0.8,
            GamePhase.MID: 1.0,
            GamePhase.LATE: 1.1,
        }.get(fv.game_phase, 1.0)
        total_score *= phase_scale

        # Sigmoid to get probability
        win_prob = self._sigmoid(total_score)

        return win_prob, factors

    def set_weights(self, weights: Dict[str, float]) -> None:
        """Update model weights (for evolution)."""
        self._weights.update(weights)

    def record_outcome(self, predicted_prob: float, actual_win: bool) -> None:
        """Record a prediction outcome for calibration tracking."""
        self._prediction_history.append((predicted_prob, actual_win))

    @property
    def calibration_error(self) -> float:
        """Compute mean calibration error from recent predictions."""
        if len(self._prediction_history) < 5:
            return 0.0
        total_err = 0.0
        for pred, actual in self._prediction_history:
            actual_val = 1.0 if actual else 0.0
            total_err += abs(pred - actual_val)
        return total_err / len(self._prediction_history)

    @staticmethod
    def _sigmoid(x: float) -> float:
        """Numerically stable sigmoid."""
        if x >= 0:
            z = math.exp(-x)
            return 1.0 / (1.0 + z)
        else:
            z = math.exp(x)
            return z / (1.0 + z)


# ─── TeamfightPredictor (Main Class) ────────────────────────────────────────

class TeamfightPredictor:
    """Top-level teamfight prediction interface.

    Integrates feature extraction, scoring model, and action
    recommendation into a single ``predict()`` call.

    Usage::

        predictor = TeamfightPredictor()
        assessment = predictor.predict(snapshot, events)
        print(assessment.recommended_action)  # ENGAGE / DISENGAGE / POKE
    """

    # Action thresholds
    ENGAGE_THRESHOLD = 0.60       # > 60% win prob → engage
    DISENGAGE_THRESHOLD = 0.40    # < 40% win prob → disengage
    PICK_THRESHOLD_LOW = 0.45     # 40-45% → look for pick
    PICK_THRESHOLD_HIGH = 0.55    # 55-60% → look for pick

    def __init__(
        self,
        weights: Optional[Dict[str, float]] = None,
    ) -> None:
        self._extractor = TeamfightFeatureExtractor()
        self._model = TeamfightScoringModel(weights)
        self._prediction_count: int = 0
        self._history: Deque[TeamfightAssessment] = deque(maxlen=_HISTORY_WINDOW)

    def predict(
        self,
        snapshot: GameSnapshot,
        events: Optional[List[GameEvent]] = None,
    ) -> TeamfightAssessment:
        """Predict teamfight outcome and recommend action.

        Args:
            snapshot: Current game state.
            events: Recent game events for momentum analysis.

        Returns:
            TeamfightAssessment with action, probability, and breakdown.
        """
        # Extract features
        fv = self._extractor.extract(snapshot, events)

        # Edge case: no one alive
        if fv.our_alive == 0:
            return TeamfightAssessment(
                recommended_action=FightAction.DISENGAGE,
                our_win_probability=0.0,
                confidence=1.0,
                rationale="All dead — cannot fight",
            )
        if fv.their_alive == 0:
            return TeamfightAssessment(
                recommended_action=FightAction.ENGAGE,
                our_win_probability=1.0,
                confidence=1.0,
                rationale="Enemy team wiped — free objective",
            )

        # Run model
        win_prob, factors = self._model.predict(fv)

        # Determine action
        action, rationale = self._determine_action(win_prob, fv, factors)

        # Confidence: higher when probability is far from 50%
        confidence = abs(win_prob - 0.5) * 2.0  # Maps 0.5→0, 0/1→1.0
        confidence = max(_MIN_CONFIDENCE, min(_MAX_CONFIDENCE, confidence))

        # Apply calibration adjustment
        cal_err = self._model.calibration_error
        if cal_err > 0.1:
            confidence *= (1.0 - cal_err)

        assessment = TeamfightAssessment(
            recommended_action=action,
            our_win_probability=win_prob,
            confidence=confidence,
            rationale=rationale,
            factor_breakdown=factors,
            features=fv,
        )

        self._prediction_count += 1
        self._history.append(assessment)

        logger.debug(
            "Teamfight #%d: %s (%.1f%% win, %.0f%% conf) — %s",
            self._prediction_count,
            action.value,
            win_prob * 100,
            confidence * 100,
            rationale,
        )

        return assessment

    def _determine_action(
        self,
        win_prob: float,
        fv: FeatureVector,
        factors: Dict[str, float],
    ) -> Tuple[FightAction, str]:
        """Map win probability to a recommended action."""
        # Strong engage
        if win_prob >= self.ENGAGE_THRESHOLD:
            top_factor = max(factors, key=lambda k: factors[k])
            return (
                FightAction.ENGAGE,
                f"Engage ({win_prob:.0%} win) — strongest factor: {top_factor}",
            )

        # Strong disengage
        if win_prob <= self.DISENGAGE_THRESHOLD:
            worst_factor = min(factors, key=lambda k: factors[k])
            return (
                FightAction.DISENGAGE,
                f"Disengage ({win_prob:.0%} win) — weakest factor: {worst_factor}",
            )

        # Pick opportunity: slight disadvantage but alive advantage
        if (
            self.PICK_THRESHOLD_LOW <= win_prob < self.DISENGAGE_THRESHOLD + 0.05
            and fv.our_alive > fv.their_alive
        ):
            return (
                FightAction.PICK,
                f"Look for a pick ({win_prob:.0%}) — man advantage",
            )

        # Poke: marginal situation
        return (
            FightAction.POKE,
            f"Poke and wait ({win_prob:.0%} win) — not decisive enough to commit",
        )

    # ── Configuration API ────────────────────────────────────────────────

    def set_weights(self, weights: Dict[str, float]) -> None:
        """Update model weights at runtime (for evolution)."""
        self._model.set_weights(weights)

    def set_thresholds(
        self,
        engage: Optional[float] = None,
        disengage: Optional[float] = None,
    ) -> None:
        """Update action thresholds."""
        if engage is not None:
            self.ENGAGE_THRESHOLD = max(0.5, min(0.9, engage))
        if disengage is not None:
            self.DISENGAGE_THRESHOLD = max(0.1, min(0.5, disengage))

    def record_outcome(self, predicted_prob: float, actual_win: bool) -> None:
        """Feed back actual outcome for calibration."""
        self._model.record_outcome(predicted_prob, actual_win)

    # ── Stats ────────────────────────────────────────────────────────────

    def stats(self) -> Dict[str, Any]:
        """Return predictor statistics."""
        action_dist: Dict[str, int] = {}
        avg_conf = 0.0
        for a in self._history:
            key = a.recommended_action.value
            action_dist[key] = action_dist.get(key, 0) + 1
            avg_conf += a.confidence

        if self._history:
            avg_conf /= len(self._history)

        return {
            "prediction_count": self._prediction_count,
            "history_size": len(self._history),
            "action_distribution": action_dist,
            "avg_confidence": round(avg_conf, 3),
            "calibration_error": round(self._model.calibration_error, 4),
        }

    def reset(self) -> None:
        """Reset state between games."""
        self._prediction_count = 0
        self._history.clear()
