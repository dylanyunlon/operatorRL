"""
LaneAdvisor — Early-game laning phase strategy advisor.
=========================================================
lolbot-HyperAI · Planning Layer

Provides lane-specific advice during the early game (0-14 minutes):
    - Trade timing: when to harass vs farm
    - Wave management: freeze, slow push, fast push
    - Back timing: optimal recall windows
    - Power spikes: level/item breakpoints
    - Gank vulnerability: based on wave position and vision

Architecture position:
    modules/planning/strategy/lane_advisor.py   ← YOU ARE HERE
    ├─ Input: GameSnapshot (player states, game time, events)
    ├─ Output: LaneAdvice objects for active player's lane
    ├─ Consumed by: PlanningComponent (early-game decisions)
    └─ Disengages after 14 minutes (hands off to MacroPlanner)

Apollo reference:
    modules/planning/tasks/deciders/lane_decider.cc — conceptual analog
    modules/planning/scenarios/lane_follow/ — lane-following logic

Design notes:
    - Active only during GamePhase.EARLY (0-14 min)
    - Champion matchup database: static win rates by lane pairing
    - CS difference tracking for laning performance assessment
    - Cooldown on advice to avoid spamming lane partner
"""

from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Deque, Dict, List, Optional, Tuple

from cyber.logger.cyber_logger import get_logger
from modules.common.adapters.game_messages import (
    GamePhase,
    GameSnapshot,
    PlayerState,
    TeamSide,
)

logger = get_logger("planning.lane")

# ─── Constants ───────────────────────────────────────────────────────────────

_EARLY_GAME_END_S = 840.0       # 14 minutes
_ADVICE_COOLDOWN_S = 20.0       # Min gap between similar advice
_POWER_SPIKE_LEVELS = [2, 3, 6, 9, 11, 16]  # Major level spikes
_LOW_HP_THRESHOLD = 0.35
_BACK_GOLD_THRESHOLDS = [1300, 900, 875]  # BF Sword, Pickaxe, Long Sword combos
_CS_PER_MIN_TARGET = 7.0
_MAX_ADVICE_HISTORY = 50


# ─── Matchup Database (Static) ──────────────────────────────────────────────

# Simplified matchup data: (champion_a, champion_b) → advantage score
# Positive = champion_a favored, range [-1.0, 1.0]
# In production, this would be loaded from a data file or M-series module
_MATCHUP_DB: Dict[Tuple[str, str], float] = {
    # Example entries — would be populated from actual data
    ("Darius", "Garen"): 0.15,
    ("Garen", "Darius"): -0.15,
    ("Yasuo", "Zed"): -0.1,
    ("Zed", "Yasuo"): 0.1,
    ("Jinx", "Caitlyn"): -0.2,
    ("Caitlyn", "Jinx"): 0.2,
}


# ─── Data Types ──────────────────────────────────────────────────────────────

class LaneAdviceType(Enum):
    """Types of lane advice."""
    TRADE = "trade"                 # Engage a trade
    FARM_SAFE = "farm_safe"         # Play safe, focus CS
    FREEZE = "freeze"               # Freeze the wave
    SLOW_PUSH = "slow_push"         # Build a slow push
    FAST_PUSH = "fast_push"         # Shove and roam/back
    BACK_NOW = "back_now"           # Recall timing
    POWER_SPIKE = "power_spike"     # Level/item spike alert
    GANK_WARNING = "gank_warning"   # Vulnerable to gank
    ALL_IN = "all_in"               # Go for the kill
    ZONE = "zone"                   # Zone enemy off CS
    ROAM = "roam"                   # Leave lane to help team


class AggressivenessLevel(Enum):
    """How aggressive lane play should be."""
    PASSIVE = "passive"       # Under tower, survive
    DEFENSIVE = "defensive"   # Trade only when favorable
    NEUTRAL = "neutral"       # Standard laning
    AGGRESSIVE = "aggressive" # Look for trades and pressure
    ALL_IN = "all_in"         # Go for kills


@dataclass
class LaneAdvice:
    """A piece of lane-phase advice."""
    advice_type: LaneAdviceType
    text: str
    aggressiveness: AggressivenessLevel = AggressivenessLevel.NEUTRAL
    confidence: float = 0.5
    game_time: float = 0.0
    dedup_key: str = ""
    timestamp: float = field(default_factory=time.monotonic)

    def __post_init__(self):
        if not self.dedup_key:
            self.dedup_key = self.advice_type.value

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.advice_type.value,
            "text": self.text,
            "aggressiveness": self.aggressiveness.value,
            "confidence": round(self.confidence, 2),
            "game_time": round(self.game_time, 1),
        }


@dataclass
class LaneMatchupAssessment:
    """Assessment of the lane matchup."""
    our_champion: str
    their_champion: str
    advantage_score: float        # [-1, 1]: positive = we're favored
    recommended_aggro: AggressivenessLevel
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "our_champ": self.our_champion,
            "their_champ": self.their_champion,
            "advantage": round(self.advantage_score, 2),
            "aggro": self.recommended_aggro.value,
            "notes": self.notes,
        }


# ─── Lane State Tracker ─────────────────────────────────────────────────────

class _LaneStateTracker:
    """Tracks lane state over time for trend detection."""

    def __init__(self) -> None:
        self.hp_history: Deque[float] = deque(maxlen=30)
        self.cs_history: Deque[Tuple[float, int]] = deque(maxlen=30)
        self.gold_history: Deque[float] = deque(maxlen=30)
        self.last_back_time: float = 0.0
        self.deaths_in_lane: int = 0

    def update(self, player: PlayerState, game_time: float) -> None:
        hp_ratio = player.current_health / max(player.max_health, 1)
        self.hp_history.append(hp_ratio)
        self.cs_history.append((game_time, player.scores.creep_score))
        self.gold_history.append(player.current_gold)

    @property
    def current_hp_ratio(self) -> float:
        return self.hp_history[-1] if self.hp_history else 1.0

    @property
    def cs_per_min(self) -> float:
        if len(self.cs_history) < 2:
            return 0.0
        first_time, first_cs = self.cs_history[0]
        last_time, last_cs = self.cs_history[-1]
        duration_min = (last_time - first_time) / 60.0
        if duration_min < 0.5:
            return 0.0
        return (last_cs - first_cs) / duration_min

    @property
    def hp_trend(self) -> float:
        """HP trend: positive = recovering, negative = getting poked."""
        if len(self.hp_history) < 5:
            return 0.0
        recent = list(self.hp_history)[-5:]
        return recent[-1] - recent[0]


# ─── LaneAdvisor ─────────────────────────────────────────────────────────────

class LaneAdvisor:
    """Generates lane-phase strategy advice.

    Active during GamePhase.EARLY only.  Analyzes the active player's
    lane state (HP, CS, gold, matchup) and produces context-aware advice.

    Usage::

        advisor = LaneAdvisor()
        advice_list = advisor.advise(snapshot)
        for advice in advice_list:
            print(f"[{advice.advice_type.value}] {advice.text}")
    """

    def __init__(self) -> None:
        self._tracker = _LaneStateTracker()
        self._matchup: Optional[LaneMatchupAssessment] = None
        self._advice_cooldowns: Dict[str, float] = {}
        self._advice_history: Deque[LaneAdvice] = deque(maxlen=_MAX_ADVICE_HISTORY)
        self._advice_count: int = 0
        self._active: bool = True

    def advise(self, snapshot: GameSnapshot) -> List[LaneAdvice]:
        """Generate lane advice for the current state.

        Args:
            snapshot: Current game state.

        Returns:
            List of applicable lane advice items (may be empty).
        """
        # Only active during early game
        if snapshot.phase != GamePhase.EARLY or snapshot.game_time > _EARLY_GAME_END_S:
            self._active = False
            return []

        self._active = True
        advices: List[LaneAdvice] = []

        # Find active player
        active_player = self._find_active_player(snapshot)
        if active_player is None:
            return []

        # Update tracker
        self._tracker.update(active_player, snapshot.game_time)

        # Check each advice type
        self._check_hp_advice(active_player, snapshot, advices)
        self._check_cs_advice(active_player, snapshot, advices)
        self._check_power_spike(active_player, snapshot, advices)
        self._check_back_timing(active_player, snapshot, advices)
        self._check_wave_management(active_player, snapshot, advices)

        # Apply cooldowns
        now = time.monotonic()
        filtered = []
        for advice in advices:
            last = self._advice_cooldowns.get(advice.dedup_key, 0.0)
            if now - last >= _ADVICE_COOLDOWN_S:
                filtered.append(advice)
                self._advice_cooldowns[advice.dedup_key] = now
                self._advice_history.append(advice)
                self._advice_count += 1

        return filtered

    def _find_active_player(self, snapshot: GameSnapshot) -> Optional[PlayerState]:
        """Find the active (local) player from the snapshot."""
        for team in (snapshot.blue_team, snapshot.red_team):
            for player in team.players:
                if getattr(player, "is_local_player", False):
                    return player
        # Fallback: first alive player on active team
        our_side = snapshot.active_team
        team = snapshot.blue_team if our_side == TeamSide.BLUE else snapshot.red_team
        for player in team.players:
            if not player.is_dead:
                return player
        return None

    def _check_hp_advice(
        self,
        player: PlayerState,
        snapshot: GameSnapshot,
        advices: List[LaneAdvice],
    ) -> None:
        """Check if HP warrants caution or aggression."""
        hp_ratio = self._tracker.current_hp_ratio

        if hp_ratio < _LOW_HP_THRESHOLD:
            advices.append(LaneAdvice(
                advice_type=LaneAdviceType.FARM_SAFE,
                text=f"HP low ({hp_ratio:.0%}) — farm under tower, wait for back timing",
                aggressiveness=AggressivenessLevel.PASSIVE,
                confidence=0.85,
                game_time=snapshot.game_time,
                dedup_key="low_hp",
            ))
        elif hp_ratio > 0.8 and self._tracker.hp_trend > 0.1:
            advices.append(LaneAdvice(
                advice_type=LaneAdviceType.TRADE,
                text="HP healthy and recovering — look for a trade",
                aggressiveness=AggressivenessLevel.AGGRESSIVE,
                confidence=0.6,
                game_time=snapshot.game_time,
                dedup_key="healthy_trade",
            ))

    def _check_cs_advice(
        self,
        player: PlayerState,
        snapshot: GameSnapshot,
        advices: List[LaneAdvice],
    ) -> None:
        """Check CS rate and provide farming advice."""
        cspm = self._tracker.cs_per_min
        if snapshot.game_time < 180:  # Too early for CS tracking
            return

        if cspm < _CS_PER_MIN_TARGET * 0.7:
            advices.append(LaneAdvice(
                advice_type=LaneAdviceType.FARM_SAFE,
                text=f"CS/min low ({cspm:.1f}) — focus on last-hitting",
                aggressiveness=AggressivenessLevel.DEFENSIVE,
                confidence=0.7,
                game_time=snapshot.game_time,
                dedup_key="low_cs",
            ))

    def _check_power_spike(
        self,
        player: PlayerState,
        snapshot: GameSnapshot,
        advices: List[LaneAdvice],
    ) -> None:
        """Detect level power spikes."""
        if player.level in _POWER_SPIKE_LEVELS:
            advices.append(LaneAdvice(
                advice_type=LaneAdviceType.POWER_SPIKE,
                text=f"Level {player.level} power spike — consider engaging",
                aggressiveness=AggressivenessLevel.AGGRESSIVE,
                confidence=0.75,
                game_time=snapshot.game_time,
                dedup_key=f"spike_l{player.level}",
            ))

    def _check_back_timing(
        self,
        player: PlayerState,
        snapshot: GameSnapshot,
        advices: List[LaneAdvice],
    ) -> None:
        """Check if it's a good time to recall."""
        gold = player.current_gold
        hp_ratio = self._tracker.current_hp_ratio

        for threshold in _BACK_GOLD_THRESHOLDS:
            if gold >= threshold and hp_ratio < 0.6:
                advices.append(LaneAdvice(
                    advice_type=LaneAdviceType.BACK_NOW,
                    text=f"Push wave and back — {gold:.0f}g available for item buy",
                    aggressiveness=AggressivenessLevel.NEUTRAL,
                    confidence=0.7,
                    game_time=snapshot.game_time,
                    dedup_key="back_timing",
                ))
                break

    def _check_wave_management(
        self,
        player: PlayerState,
        snapshot: GameSnapshot,
        advices: List[LaneAdvice],
    ) -> None:
        """Basic wave management advice based on game time windows."""
        game_min = snapshot.game_time / 60.0

        if 2.0 < game_min < 5.0:
            advices.append(LaneAdvice(
                advice_type=LaneAdviceType.FREEZE,
                text="Early game — freeze near tower for safe farming",
                aggressiveness=AggressivenessLevel.DEFENSIVE,
                confidence=0.5,
                game_time=snapshot.game_time,
                dedup_key="early_freeze",
            ))
        elif 5.0 <= game_min < 10.0 and self._tracker.current_hp_ratio > 0.7:
            advices.append(LaneAdvice(
                advice_type=LaneAdviceType.SLOW_PUSH,
                text="Build a slow push for dive or plate threat",
                aggressiveness=AggressivenessLevel.AGGRESSIVE,
                confidence=0.55,
                game_time=snapshot.game_time,
                dedup_key="slow_push",
            ))

    # ── Configuration ────────────────────────────────────────────────────

    def set_matchup(
        self,
        our_champ: str,
        their_champ: str,
    ) -> LaneMatchupAssessment:
        """Set the lane matchup for context-aware advice."""
        key = (our_champ, their_champ)
        advantage = _MATCHUP_DB.get(key, 0.0)

        if advantage > 0.15:
            aggro = AggressivenessLevel.AGGRESSIVE
            notes = "Favorable matchup — look for trades"
        elif advantage < -0.15:
            aggro = AggressivenessLevel.DEFENSIVE
            notes = "Unfavorable matchup — play safe"
        else:
            aggro = AggressivenessLevel.NEUTRAL
            notes = "Even matchup — skill dependent"

        self._matchup = LaneMatchupAssessment(
            our_champion=our_champ,
            their_champion=their_champ,
            advantage_score=advantage,
            recommended_aggro=aggro,
            notes=notes,
        )
        return self._matchup

    # ── Stats ────────────────────────────────────────────────────────────

    def stats(self) -> Dict[str, Any]:
        type_counts: Dict[str, int] = {}
        for a in self._advice_history:
            key = a.advice_type.value
            type_counts[key] = type_counts.get(key, 0) + 1
        return {
            "active": self._active,
            "advice_count": self._advice_count,
            "matchup": self._matchup.to_dict() if self._matchup else None,
            "cs_per_min": round(self._tracker.cs_per_min, 1),
            "hp_ratio": round(self._tracker.current_hp_ratio, 2),
            "advice_types": type_counts,
        }

    def reset(self) -> None:
        self._tracker = _LaneStateTracker()
        self._matchup = None
        self._advice_cooldowns.clear()
        self._advice_history.clear()
        self._advice_count = 0
        self._active = True
