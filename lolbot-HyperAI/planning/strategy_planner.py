#!/usr/bin/env python3
"""
planning/strategy_planner.py — Tactical Strategy Recommendation Engine
========================================================================
lolbot-HyperAI · Planning Layer

In Apollo, the planning module takes the prediction output (where will
obstacles be) and plans the vehicle's trajectory (what to do). Our
planner takes win predictions + game state and generates tactical
recommendations (what should the player do now).

Recommendation types:
    - BAN/PICK: Champion select advice
    - LANE: Laning phase micro-advice (trade timings, wave management)
    - OBJECTIVE: Dragon/Baron/Herald calls
    - TEAMFIGHT: Engage/disengage decisions
    - MACRO: Rotations, split push, grouping
    - ITEM: Item build suggestions based on game state
    - WARD: Vision control priorities
    - DANGER: Gank warnings, power spike alerts

Each recommendation has:
    - priority: CRITICAL > HIGH > MEDIUM > LOW
    - expires_sec: how long it stays relevant
    - confidence: how sure we are this is good advice
    - voice_text: pre-formatted text for TTS

The planner respects a cooldown system: it won't spam the same advice
type within a short window (avoids annoying the player).

Evolution hook: The evolution controller can adjust:
    - Priority weights and thresholds
    - Cooldown intervals
    - Confidence thresholds for publishing

Subscribes to: CH_WIN_PROBABILITY, CH_LIVE_GAME_STATE
Publishes to: CH_STRATEGY_RECOMMENDATION, CH_OBJECTIVE_PRIORITY
"""

from __future__ import annotations

import hashlib
import sys
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from enum import Enum, IntEnum, auto
from pathlib import Path
from typing import Any, Callable, Deque, Dict, List, Optional, Set, Tuple

sys.path.insert(0, str(Path(__file__).parent.parent))
from canbus.channel_message import (
    CH_LIVE_GAME_STATE,
    CH_OBJECTIVE_PRIORITY,
    CH_STRATEGY_RECOMMENDATION,
    CH_WIN_PROBABILITY,
    ChannelMessage,
    MessageFactory,
)
from canbus.transport import Transport


# ---------------------------------------------------------------------------
# Recommendation types and priority
# ---------------------------------------------------------------------------
class RecType(Enum):
    BAN_SUGGESTION = "ban_suggestion"
    PICK_SUGGESTION = "pick_suggestion"
    LANE_TRADE = "lane_trade"
    LANE_WAVE = "lane_wave"
    BACK_TIMING = "back_timing"
    OBJECTIVE_CALL = "objective_call"
    TEAMFIGHT_ENGAGE = "teamfight_engage"
    TEAMFIGHT_DISENGAGE = "teamfight_disengage"
    MACRO_ROTATION = "macro_rotation"
    MACRO_SPLITPUSH = "macro_splitpush"
    ITEM_SUGGESTION = "item_suggestion"
    WARD_PRIORITY = "ward_priority"
    DANGER_GANK = "danger_gank"
    DANGER_POWER_SPIKE = "danger_power_spike"
    GENERAL_TIP = "general_tip"


class Priority(IntEnum):
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4


@dataclass
class Recommendation:
    """A single tactical recommendation."""
    rec_id: str
    rec_type: RecType
    priority: Priority
    title: str
    detail: str
    voice_text: str                     # Pre-formatted for TTS
    game_phase: str
    confidence: float = 0.5
    expires_sec: float = 30.0
    created_at: float = field(default_factory=time.monotonic)
    game_time_sec: float = 0.0
    related_champion: Optional[str] = None
    related_objective: Optional[str] = None

    def is_expired(self) -> bool:
        return (time.monotonic() - self.created_at) > self.expires_sec

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rec_id": self.rec_id,
            "rec_type": self.rec_type.value,
            "priority": self.priority.value,
            "title": self.title,
            "detail": self.detail,
            "voice_text": self.voice_text,
            "game_phase": self.game_phase,
            "confidence": round(self.confidence, 3),
            "expires_sec": self.expires_sec,
            "game_time_sec": self.game_time_sec,
            "related_champion": self.related_champion,
            "related_objective": self.related_objective,
        }


# ---------------------------------------------------------------------------
# Cooldown manager (prevents recommendation spam)
# ---------------------------------------------------------------------------
class CooldownManager:
    """
    Manages per-type recommendation cooldowns.

    Each RecType has a minimum interval between recommendations.
    CRITICAL priority ignores cooldowns (always delivered).
    """

    DEFAULT_COOLDOWNS: Dict[RecType, float] = {
        RecType.BAN_SUGGESTION: 10.0,
        RecType.PICK_SUGGESTION: 8.0,
        RecType.LANE_TRADE: 20.0,
        RecType.LANE_WAVE: 25.0,
        RecType.BACK_TIMING: 30.0,
        RecType.OBJECTIVE_CALL: 15.0,
        RecType.TEAMFIGHT_ENGAGE: 10.0,
        RecType.TEAMFIGHT_DISENGAGE: 10.0,
        RecType.MACRO_ROTATION: 20.0,
        RecType.MACRO_SPLITPUSH: 30.0,
        RecType.ITEM_SUGGESTION: 45.0,
        RecType.WARD_PRIORITY: 40.0,
        RecType.DANGER_GANK: 8.0,
        RecType.DANGER_POWER_SPIKE: 15.0,
        RecType.GENERAL_TIP: 60.0,
    }

    def __init__(
        self,
        cooldowns: Optional[Dict[RecType, float]] = None,
    ) -> None:
        self._cooldowns = dict(cooldowns or self.DEFAULT_COOLDOWNS)
        self._last_fire: Dict[RecType, float] = {}

    def can_fire(self, rec_type: RecType, priority: Priority) -> bool:
        """Check if this type is off cooldown."""
        if priority >= Priority.CRITICAL:
            return True
        last = self._last_fire.get(rec_type, 0)
        cd = self._cooldowns.get(rec_type, 15.0)
        return (time.monotonic() - last) >= cd

    def fire(self, rec_type: RecType) -> None:
        """Mark this type as just fired."""
        self._last_fire[rec_type] = time.monotonic()

    def adjust_cooldown(self, rec_type: RecType, new_cd: float) -> None:
        """Adjust a cooldown (used by evolution controller)."""
        self._cooldowns[rec_type] = max(1.0, new_cd)

    def export(self) -> Dict[str, float]:
        return {rt.value: cd for rt, cd in self._cooldowns.items()}


# ---------------------------------------------------------------------------
# Rule-based strategy generators
# ---------------------------------------------------------------------------
class ObjectiveAdvisor:
    """
    Generates objective-related recommendations.

    Tracks objective timers and recommends dragon/baron/herald
    prioritization based on game state.
    """

    # Objective spawn times (seconds)
    DRAGON_FIRST_SPAWN = 300        # 5:00
    DRAGON_RESPAWN = 300            # 5:00
    HERALD_FIRST_SPAWN = 480       # 8:00
    BARON_FIRST_SPAWN = 1200       # 20:00
    BARON_RESPAWN = 360            # 6:00
    ELDER_SPAWN_AFTER_SOUL = 360   # 6:00 after soul

    def evaluate(
        self,
        game_state: Dict[str, Any],
        win_prediction: Dict[str, Any],
    ) -> List[Recommendation]:
        """Generate objective recommendations."""
        recs = []
        gt = game_state.get("game_time_sec", 0)
        phase = game_state.get("phase", "none")
        objectives = game_state.get("objectives", {})
        kill_diff = game_state.get("kill_diff", 0)
        our_dead = sum(
            1 for p in game_state.get("our_team", {}).get("players", [])
            if p.get("is_dead", False)
        )
        enemy_dead = sum(
            1 for p in game_state.get("enemy_team", {}).get("players", [])
            if p.get("is_dead", False)
        )

        # Dragon advice
        dragon_ally = objectives.get("dragon_count_ally", 0)
        dragon_enemy = objectives.get("dragon_count_enemy", 0)

        if gt >= self.DRAGON_FIRST_SPAWN and phase in ("laning", "mid_game", "late_game"):
            if dragon_enemy >= 3 and dragon_ally < 3:
                recs.append(Recommendation(
                    rec_id=self._make_id("dragon_deny", gt),
                    rec_type=RecType.OBJECTIVE_CALL,
                    priority=Priority.CRITICAL,
                    title="DENY Dragon Soul!",
                    detail=(
                        f"Enemy has {dragon_enemy} dragons — soul point! "
                        "Contest this dragon at all costs."
                    ),
                    voice_text=(
                        f"Warning! Enemy is at {dragon_enemy} dragons. "
                        "We must contest the next dragon or they get soul."
                    ),
                    game_phase=phase,
                    confidence=0.9,
                    expires_sec=60,
                    game_time_sec=gt,
                    related_objective="dragon",
                ))
            elif dragon_ally >= 3:
                recs.append(Recommendation(
                    rec_id=self._make_id("dragon_soul", gt),
                    rec_type=RecType.OBJECTIVE_CALL,
                    priority=Priority.HIGH,
                    title="Dragon Soul Point!",
                    detail=(
                        f"We have {dragon_ally} dragons. "
                        "Prioritize the next dragon for soul."
                    ),
                    voice_text=(
                        f"We're at {dragon_ally} dragons! "
                        "Push for soul on the next dragon spawn."
                    ),
                    game_phase=phase,
                    confidence=0.85,
                    expires_sec=45,
                    game_time_sec=gt,
                    related_objective="dragon",
                ))

        # Baron advice
        if gt >= self.BARON_FIRST_SPAWN and phase in ("mid_game", "late_game"):
            if enemy_dead >= 2 and our_dead == 0:
                recs.append(Recommendation(
                    rec_id=self._make_id("baron_start", gt),
                    rec_type=RecType.OBJECTIVE_CALL,
                    priority=Priority.HIGH,
                    title="Baron Opportunity!",
                    detail=(
                        f"{enemy_dead} enemies dead. "
                        "This is a good time to start Baron."
                    ),
                    voice_text=(
                        f"{enemy_dead} enemies are dead. "
                        "Consider starting Baron now!"
                    ),
                    game_phase=phase,
                    confidence=0.75,
                    expires_sec=20,
                    game_time_sec=gt,
                    related_objective="baron",
                ))

        return recs

    @staticmethod
    def _make_id(prefix: str, game_time: float) -> str:
        raw = f"{prefix}_{int(game_time)}"
        return hashlib.md5(raw.encode()).hexdigest()[:12]


class TempoAdvisor:
    """
    Generates tempo-based recommendations.

    Reacts to momentum shifts, power spikes, and game flow.
    """

    # Known power spike levels
    _POWER_SPIKES = {
        "Kassadin": [6, 11, 16],
        "Kayle": [6, 11, 16],
        "Vayne": [6, 11, 13],
        "Lucian": [2, 6],
        "Renekton": [3, 6, 9],
        "Syndra": [6, 9],
        "Zed": [6, 11],
    }

    def evaluate(
        self,
        game_state: Dict[str, Any],
        win_prediction: Dict[str, Any],
    ) -> List[Recommendation]:
        recs = []
        gt = game_state.get("game_time_sec", 0)
        phase = game_state.get("phase", "none")
        win_pct = win_prediction.get("win_pct", 0.5)
        trend = win_prediction.get("trend", "stable")
        what_if = win_prediction.get("what_if", {})

        # Momentum shift warning
        trend_delta = win_prediction.get("trend_delta", 0)
        if trend_delta < -0.08:
            recs.append(Recommendation(
                rec_id=self._make_id("momentum_loss", gt),
                rec_type=RecType.DANGER_POWER_SPIKE,
                priority=Priority.HIGH,
                title="Momentum Shifting!",
                detail=(
                    f"Win probability dropping ({trend_delta:+.1%} in last 60s). "
                    "Play safe and avoid risky fights."
                ),
                voice_text=(
                    "We're losing momentum. Play safe and "
                    "wait for a better opportunity."
                ),
                game_phase=phase,
                confidence=0.7,
                expires_sec=30,
                game_time_sec=gt,
            ))
        elif trend_delta > 0.08:
            recs.append(Recommendation(
                rec_id=self._make_id("momentum_gain", gt),
                rec_type=RecType.MACRO_ROTATION,
                priority=Priority.MEDIUM,
                title="We Have Momentum!",
                detail=(
                    f"Win probability rising ({trend_delta:+.1%}). "
                    "Press the advantage — look for objectives."
                ),
                voice_text=(
                    "Good momentum! Press the advantage "
                    "and look for objectives."
                ),
                game_phase=phase,
                confidence=0.7,
                expires_sec=20,
                game_time_sec=gt,
            ))

        # Low win probability — need comeback plan
        if win_pct < 0.35 and phase in ("mid_game", "late_game"):
            # Check what-if to find best path
            best_scenario = max(what_if.items(), key=lambda x: x[1]) \
                if what_if else ("none", 0.5)
            if best_scenario[1] > win_pct + 0.05:
                recs.append(Recommendation(
                    rec_id=self._make_id("comeback_path", gt),
                    rec_type=RecType.MACRO_ROTATION,
                    priority=Priority.HIGH,
                    title="Comeback Plan",
                    detail=(
                        f"Current win chance: {win_pct:.0%}. "
                        f"Best path: {best_scenario[0].replace('_', ' ')} "
                        f"(would raise to {best_scenario[1]:.0%})."
                    ),
                    voice_text=(
                        f"We're behind at {win_pct:.0%} win chance. "
                        f"Focus on {best_scenario[0].replace('_', ' ')} "
                        "for the best comeback chance."
                    ),
                    game_phase=phase,
                    confidence=0.6,
                    expires_sec=45,
                    game_time_sec=gt,
                ))

        return recs

    @staticmethod
    def _make_id(prefix: str, game_time: float) -> str:
        raw = f"{prefix}_{int(game_time)}"
        return hashlib.md5(raw.encode()).hexdigest()[:12]


class LaneAdvisor:
    """Generates laning-phase specific advice."""

    def evaluate(
        self,
        game_state: Dict[str, Any],
        win_prediction: Dict[str, Any],
    ) -> List[Recommendation]:
        recs = []
        phase = game_state.get("phase", "none")
        if phase not in ("early_laning", "laning"):
            return recs

        gt = game_state.get("game_time_sec", 0)
        kill_diff = game_state.get("kill_diff", 0)
        cs_diff = game_state.get("cs_diff", 0)

        # CS behind warning
        if cs_diff < -20 and gt > 300:
            recs.append(Recommendation(
                rec_id=TempoAdvisor._make_id("cs_behind", gt),
                rec_type=RecType.LANE_WAVE,
                priority=Priority.MEDIUM,
                title="CS Deficit",
                detail=(
                    f"Team is {abs(cs_diff)} CS behind. "
                    "Focus on farming and catching side waves."
                ),
                voice_text=(
                    f"We're {abs(cs_diff)} CS behind. "
                    "Focus on farming safely."
                ),
                game_phase=phase,
                confidence=0.65,
                expires_sec=30,
                game_time_sec=gt,
            ))

        # Back timing after good trade
        if kill_diff >= 2 and gt > 180:
            recs.append(Recommendation(
                rec_id=TempoAdvisor._make_id("back_timing", gt),
                rec_type=RecType.BACK_TIMING,
                priority=Priority.LOW,
                title="Good Back Timing",
                detail=(
                    "We have a kill advantage. Consider backing "
                    "to convert gold into items."
                ),
                voice_text="Good time to back and spend your gold.",
                game_phase=phase,
                confidence=0.6,
                expires_sec=20,
                game_time_sec=gt,
            ))

        return recs


# ---------------------------------------------------------------------------
# Strategy Planner (main component)
# ---------------------------------------------------------------------------
class StrategyPlanner:
    """
    Central strategy recommendation engine.

    Combines multiple advisors (objective, tempo, lane) and manages
    recommendation flow, cooldowns, and publishing.

    Apollo equivalent: planning/planner.
    """

    PROC_INTERVAL_MS = 3000  # Generate recommendations every 3 seconds

    def __init__(self, transport: Transport) -> None:
        self._transport = transport
        self._factory = MessageFactory("planning.strategy_planner")
        self._cooldowns = CooldownManager()

        # Advisors
        self._objective_advisor = ObjectiveAdvisor()
        self._tempo_advisor = TempoAdvisor()
        self._lane_advisor = LaneAdvisor()

        # State
        self._last_proc_ms = 0
        self._total_generated = 0
        self._total_published = 0
        self._total_cooldown_blocked = 0
        self._published_history: Deque[Recommendation] = deque(maxlen=200)

        # Confidence threshold: only publish if above this
        self._min_confidence = 0.4

    def init(self) -> None:
        """No subscriptions needed — we pull from the bus on proc()."""
        pass

    async def proc(self) -> None:
        """
        Generate and publish recommendations.

        Pulls latest game state and win prediction from bus,
        runs all advisors, filters by cooldown and confidence,
        publishes survivors.
        """
        now_ms = int(time.monotonic() * 1000)
        if now_ms - self._last_proc_ms < self.PROC_INTERVAL_MS:
            return
        self._last_proc_ms = now_ms

        # Pull latest data from bus
        state_msg = self._transport.latest(CH_LIVE_GAME_STATE)
        pred_msg = self._transport.latest(CH_WIN_PROBABILITY)

        if state_msg is None:
            return

        game_state = state_msg.payload
        win_pred = pred_msg.payload if pred_msg else {
            "win_pct": 0.5, "trend": "stable", "trend_delta": 0,
            "what_if": {},
        }

        # Run all advisors
        candidates: List[Recommendation] = []
        candidates.extend(
            self._objective_advisor.evaluate(game_state, win_pred)
        )
        candidates.extend(
            self._tempo_advisor.evaluate(game_state, win_pred)
        )
        candidates.extend(
            self._lane_advisor.evaluate(game_state, win_pred)
        )

        self._total_generated += len(candidates)

        # Filter: confidence threshold
        candidates = [
            r for r in candidates if r.confidence >= self._min_confidence
        ]

        # Filter: cooldowns
        publishable = []
        for rec in candidates:
            if self._cooldowns.can_fire(rec.rec_type, rec.priority):
                publishable.append(rec)
                self._cooldowns.fire(rec.rec_type)
            else:
                self._total_cooldown_blocked += 1

        # Sort by priority (highest first)
        publishable.sort(key=lambda r: r.priority.value, reverse=True)

        # Publish (max 2 per tick to avoid overload)
        for rec in publishable[:2]:
            msg = self._factory.create(
                CH_STRATEGY_RECOMMENDATION,
                rec.to_dict(),
                priority=rec.priority.value,
                ttl_ms=int(rec.expires_sec * 1000),
            )
            self._transport.publish(msg)
            self._published_history.append(rec)
            self._total_published += 1

    def shutdown(self) -> Dict[str, Any]:
        return self.stats()

    # -- Evolution API --------------------------------------------------

    def set_min_confidence(self, threshold: float) -> None:
        """Adjust confidence threshold (evolution controller)."""
        self._min_confidence = max(0.1, min(0.9, threshold))

    def get_cooldowns(self) -> Dict[str, float]:
        """Export cooldown config for evolution."""
        return self._cooldowns.export()

    def set_cooldown(self, rec_type_str: str, value: float) -> None:
        """Adjust a specific cooldown (evolution controller)."""
        try:
            rt = RecType(rec_type_str)
            self._cooldowns.adjust_cooldown(rt, value)
        except ValueError:
            pass

    def acceptance_rate(self) -> float:
        """
        Placeholder for recommendation acceptance tracking.

        In production, the voice output engine would report back
        whether the player followed the recommendation (e.g. did
        they go to dragon after we said "dragon opportunity").
        """
        if self._total_published == 0:
            return 0.0
        # Placeholder: would need player action tracking
        return 0.0

    # -- Stats ----------------------------------------------------------

    def stats(self) -> Dict[str, Any]:
        return {
            "total_generated": self._total_generated,
            "total_published": self._total_published,
            "total_cooldown_blocked": self._total_cooldown_blocked,
            "min_confidence": self._min_confidence,
            "history_size": len(self._published_history),
        }
