"""
BackTimingAdvisor — Optimal recall timing based on gold and wave state.
=========================================================================
lolbot-HyperAI · Planning Layer

Calculates the best time to recall (B) based on current gold, key item
breakpoints, enemy positions, and wave state.

Claude15 设计规范:
    查看 Apollo modules/planning/planner/lattice_planner.cc 上现有 multi-cost
    path evaluation 的实现方式, 理解其模式, 特别是 各维度 cost function 是如何
    独立计算再聚合的。从 Apollo lattice_planner 的 cost 分层 这个好例子开始。
    然后, 遵循该模式实现一个新的 multi-factor BackTimingAdvisor, 让
    PlanningComponent 可以 基于 gold/health/wave/threat/objective 五维评估做出
    回城建议, 并能 通过 confidence gate 过滤低信心推荐。接着 引入 WaveStateModel,
    使 advisor 能够 从CS率和事件流推断波浪位置, 同时 ThreatAssessor 优化
    敌方威胁评估。随后 ObjectiveWindowGuard 整合 目标计时器, 令 advisor 支持
    目标窗口抑制, 进而 增强 推荐时机精准度。最终 完善 BackRecommendation 输出,
    确保 score breakdown 兼容 监控面板, 全面 升级 回城建议质量。

Architecture position:
    modules/planning/strategy/back_timing_advisor.py   ← YOU ARE HERE
    ├─ Called by: PlanningComponent.Proc() during EARLY/MID phase
    ├─ Input: GameSnapshot (gold, items, positions)
    ├─ Output: Optional VoiceCommand ("Good time to back for X")
    └─ Publishes: via PlanningComponent voice_writer

Design notes:
    - Item breakpoint table: common first-buy thresholds
    - Gold buffer: recommend back when gold >= breakpoint + 75 (control ward)
    - Safety check: don't recommend back during teamfight/objective contest
    - Cooldown: 60s between back recommendations
    - Claude15: wave state inference, threat assessment, objective window guard
    - Claude15: multi-cost scoring with configurable weights
"""

from __future__ import annotations

import math
import time
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, FrozenSet, List, Optional, Set, Tuple

from modules.common.adapters.game_messages import (
    EventType, GameEvent, GamePhase, GameSnapshot, PlayerState,
    TeamSide, VoiceCommand,
)
from cyber.logger.cyber_logger import get_logger

logger = get_logger("planning.back_timing")

_COOLDOWN_S = 60.0
_GOLD_BUFFER = 75  # extra gold for control ward
_SAME_ITEM_COOLDOWN_S = 120.0       # don't repeat same item within 2 min
_CONFIDENCE_GATE = 0.50             # only announce high-confidence backs
_EARLY_GAME_MIN_TIME = 120.0        # don't recommend before 2 min
_OBJECTIVE_SUPPRESS_WINDOW_S = 45.0 # suppress near objective spawns

# Score weights (Apollo-style cost dimensions — see lattice_planner.cc)
_W_GOLD: float = 0.35      # can we buy a meaningful item?
_W_HEALTH: float = 0.25    # are we low on resources?
_W_WAVE: float = 0.20      # is the wave in a safe position?
_W_THREAT: float = 0.15    # is it safe to back?
_W_OBJECTIVE: float = 0.05 # is an objective about to spawn?

# Common first-buy gold breakpoints
_ITEM_BREAKPOINTS: List[Tuple[int, str, str]] = [
    (300,  "Boots", "Boots"),
    (350,  "Long Sword", "AD Component"),
    (435,  "Amplifying Tome", "AP Component"),
    (800, "Boots Tier 2 Upgrade", "Mobility"),
    (875, "Pickaxe", "AD"),
    (900, "Blasting Wand", "AP"),
    (1100, "Serrated Dirk", "Lethality"),
    (1100, "Noonquiver", "ADC"),
    (1300, "B.F. Sword", "AD"),
    (1300, "Lost Chapter", "AP Mana"),
    (1600, "Ironspike Whip", "Fighter"),
    (2600, "Mythic Component", "Core"),
    (3200, "Full Mythic", "Powerspike"),
]


# ─── Wave Position Enum ─────────────────────────────────────────────────────

class WavePosition(Enum):
    """Estimated minion wave position relative to our tower."""
    AT_OUR_TOWER = auto()       # bad to back — lose CS under tower
    PUSHING_TO_US = auto()      # ok — wave will crash then reset
    FROZEN_CENTER = auto()      # ok-ish, but lose freeze
    PUSHING_TO_ENEMY = auto()   # good — wave pushes out
    AT_ENEMY_TOWER = auto()     # best — wave resets, enemy loses CS
    UNKNOWN = auto()


# ─── Sub-modules (Apollo-style separated concerns) ──────────────────────────

class WaveStateModel:
    """Infers wave position from CS rate and game events.

    Since we can't directly see minion positions via the LCU API,
    we estimate wave state from CS rate trends, recent death events,
    and game time vs expected CS.

    Apollo analogy: Localization module fusing IMU + GPS when GPS
    is intermittent — we fuse CS + events when wave is unobservable.
    """

    _EXPECTED_CS_PER_MIN: Dict[str, float] = {
        "EARLY": 7.5,
        "MID": 7.0,
        "LATE": 6.0,
    }

    def estimate_position(
        self,
        player: PlayerState,
        game_time: float,
        recent_events: Tuple[GameEvent, ...],
        phase: GamePhase,
    ) -> WavePosition:
        """Estimate wave position from indirect signals."""
        if game_time < 90:
            return WavePosition.AT_OUR_TOWER  # first wave still arriving

        # Check if player recently died → wave likely pushing to us
        for event in reversed(recent_events):
            if event.game_time < game_time - 30:
                break
            if (event.event_type == EventType.CHAMPION_KILL
                    and event.victim == player.summoner_name):
                return WavePosition.PUSHING_TO_US

        # CS efficiency check
        minutes = game_time / 60.0
        if minutes <= 0:
            return WavePosition.UNKNOWN

        cs_per_min = player.scores.creep_score / minutes
        expected = self._EXPECTED_CS_PER_MIN.get(phase.name, 7.0)

        if cs_per_min > expected * 1.1:
            return WavePosition.PUSHING_TO_ENEMY
        elif cs_per_min < expected * 0.7:
            return WavePosition.FROZEN_CENTER
        else:
            return WavePosition.PUSHING_TO_ENEMY

    def back_safety_score(self, position: WavePosition) -> float:
        """Score how safe it is to back given wave position. 0..1."""
        scores = {
            WavePosition.AT_ENEMY_TOWER: 1.0,
            WavePosition.PUSHING_TO_ENEMY: 0.85,
            WavePosition.FROZEN_CENTER: 0.5,
            WavePosition.PUSHING_TO_US: 0.3,
            WavePosition.AT_OUR_TOWER: 0.1,
            WavePosition.UNKNOWN: 0.5,
        }
        return scores.get(position, 0.5)


class ThreatAssessor:
    """Evaluates safety of backing based on enemy team state.

    Checks dead enemy count, alive count, and number advantage.

    Apollo analogy: Obstacle predictor assessing collision risk
    before committing to a lane-change maneuver.
    """

    def evaluate(
        self,
        snapshot: GameSnapshot,
        player: PlayerState,
    ) -> float:
        """Return safety score 0..1 (1.0 = very safe to back)."""
        if snapshot.active_team == TeamSide.UNKNOWN:
            return 0.5

        enemy = snapshot.enemy_team
        dead_count = sum(1 for p in enemy.players if p.is_dead)
        alive_count = len(enemy.players) - dead_count

        safety = 0.5
        safety += dead_count * 0.10
        safety -= alive_count * 0.02

        if dead_count >= 3:
            safety += 0.15

        if player.is_low_health and alive_count >= 3:
            safety -= 0.10

        my_alive = snapshot.my_team.alive_count
        if my_alive > alive_count:
            safety += 0.05 * (my_alive - alive_count)

        return max(0.0, min(1.0, safety))


class ObjectiveWindowGuard:
    """Suppresses back recommendations near objective spawn windows.

    Extends the original _is_objective_window with respawn cycle tracking
    and post-objective push windows.

    Apollo analogy: Traffic light stop decision — don't commit to
    a turn when the light is about to change.
    """

    _OBJECTIVES = [
        # (first_spawn_s, respawn_s, end_time_s, name)
        (300,  300,  1200, "Dragon"),
        (300,  240,  840,  "Void Grubs"),
        (840,  0,    1185, "Rift Herald"),
        (1200, 360,  9999, "Baron"),
    ]

    def is_objective_window(
        self,
        game_time: float,
        recent_events: Tuple[GameEvent, ...] = (),
    ) -> Tuple[bool, str]:
        """Check if an objective spawn is imminent. Returns (blocked, name)."""
        for first, respawn, end_time, name in self._OBJECTIVES:
            if game_time > end_time:
                continue
            if abs(game_time - first) < _OBJECTIVE_SUPPRESS_WINDOW_S:
                return True, name
            if respawn > 0 and game_time > first:
                time_in_cycle = (game_time - first) % respawn
                if (time_in_cycle > (respawn - _OBJECTIVE_SUPPRESS_WINDOW_S)
                        or time_in_cycle < 15):
                    return True, name

        # Suppress right after objective kill — push advantage instead
        for event in reversed(recent_events):
            if event.game_time < game_time - 20:
                break
            if event.event_type in (
                EventType.DRAGON_KILL, EventType.BARON_KILL,
                EventType.HERALD_KILL,
            ):
                return True, "Post-objective push"

        return False, ""

    def objective_penalty(
        self,
        game_time: float,
        recent_events: Tuple[GameEvent, ...] = (),
    ) -> float:
        """Return penalty 0..1 for backing during objective window."""
        blocked, _ = self.is_objective_window(game_time, recent_events)
        return 0.8 if blocked else 0.0


@dataclass
class BackRecommendation:
    """A recall recommendation with multi-factor reasoning."""
    # ── Original fields (preserved) ──────────────────────────────────
    should_back: bool = False
    item_name: str = ""
    gold_needed: int = 0
    current_gold: float = 0.0
    confidence: float = 0.0
    reasoning: str = ""
    # ── Claude15: score breakdown (Apollo multi-cost dimensions) ─────
    gold_score: float = 0.0
    health_score: float = 0.0
    wave_score: float = 0.0
    threat_score: float = 0.0
    objective_penalty: float = 0.0
    wave_position: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "should_back": self.should_back,
            "item": self.item_name,
            "gold": int(self.current_gold),
            "confidence": round(self.confidence, 2),
            "scores": {
                "gold": round(self.gold_score, 3),
                "health": round(self.health_score, 3),
                "wave": round(self.wave_score, 3),
                "threat": round(self.threat_score, 3),
                "obj_penalty": round(self.objective_penalty, 3),
            },
            "wave_position": self.wave_position,
        }


class BackTimingAdvisor:
    """Advises when to recall based on gold, health, wave, and threat context.

    Not a TimerComponent — called by PlanningComponent as a sub-module.

    Claude15: Extended with Apollo-style multi-cost-dimension scoring:
        total = W_gold * gold_score
              + W_health * health_score
              + W_wave * wave_score
              + W_threat * threat_score
              - W_objective * objective_penalty

    Original evaluate() signature preserved for backward compatibility.

    Usage::
        advisor = BackTimingAdvisor()
        rec = advisor.evaluate(snapshot)
        if rec.should_back:
            voice_writer.Write(VoiceCommand(text=rec.reasoning, ...))
    """

    def __init__(self) -> None:
        self._last_recommendation_time: float = 0.0
        self._recommendation_count: int = 0
        self._breakpoints = sorted(_ITEM_BREAKPOINTS, key=lambda x: x[0])
        # Claude15: sub-modules for multi-factor evaluation
        self._wave_model = WaveStateModel()
        self._threat_assessor = ThreatAssessor()
        self._objective_guard = ObjectiveWindowGuard()
        self._last_item_recommended: str = ""
        self._last_item_time: float = 0.0
        self._suppressed_count: int = 0
        self._evaluation_count: int = 0

    def evaluate(self, snapshot: GameSnapshot) -> Optional[BackRecommendation]:
        """Evaluate whether the active player should recall now.

        Claude15: Now uses multi-factor scoring with sub-modules instead
        of single gold threshold. Original flow preserved as the gold
        dimension; wave, threat, objective added as new dimensions.
        """
        self._evaluation_count += 1
        now = time.time()

        # Cooldown check (original)
        if now - self._last_recommendation_time < _COOLDOWN_S:
            return None

        # Only during laning / mid game (original)
        if snapshot.phase not in (GamePhase.EARLY, GamePhase.MID):
            return None

        # Claude15: minimum game time guard
        if snapshot.game_time < _EARLY_GAME_MIN_TIME:
            return None

        player = snapshot.active_player
        if player is None:
            return None

        # Don't suggest back if player is dead (original)
        if player.is_dead:
            return None

        gold = player.current_gold

        # ── Dimension 1: Gold / Item analysis (original logic enhanced) ──
        best_item: Optional[Tuple[int, str, str]] = None
        for cost, name, category in reversed(self._breakpoints):
            if gold >= cost + _GOLD_BUFFER:
                best_item = (cost, name, category)
                break

        if best_item is not None:
            cost, name, category = best_item
            # Gold efficiency: how much of our gold we'd spend
            gold_score = max(0.0, 1.0 - (max(0, gold - cost) / gold) * 0.5) \
                if gold > 0 else 0.0
        else:
            cost, name, category = 0, "", ""
            gold_score = 0.0

        # Claude15: same-item cooldown
        if (name == self._last_item_recommended
                and now - self._last_item_time < _SAME_ITEM_COOLDOWN_S):
            self._suppressed_count += 1
            return None

        # ── Dimension 2: Health / Mana resources (original + mana) ───
        health_ratio = 1.0
        if player.max_health > 0:
            health_ratio = player.current_health / player.max_health

        health_score = 0.0
        if health_ratio < 0.25:
            health_score = 1.0
        elif health_ratio < 0.40:
            health_score = 0.8
        elif health_ratio < 0.60:
            health_score = 0.4
        elif health_ratio < 0.80:
            health_score = 0.1

        # Mana check (Claude15 addition)
        mana_pct = player.mana_pct
        if player.max_mana > 100 and mana_pct < 0.20:
            health_score = max(health_score, 0.6)

        # ── Dimension 3: Wave state (Claude15 new) ───────────────────
        wave_pos = self._wave_model.estimate_position(
            player, snapshot.game_time, snapshot.all_events, snapshot.phase,
        )
        wave_score = self._wave_model.back_safety_score(wave_pos)

        # ── Dimension 4: Threat assessment (Claude15 new) ────────────
        threat_score = self._threat_assessor.evaluate(snapshot, player)

        # ── Dimension 5: Objective window (original enhanced) ────────
        obj_penalty = self._objective_guard.objective_penalty(
            snapshot.game_time, snapshot.all_events,
        )

        # ── Aggregate score (Apollo multi-cost pattern) ──────────────
        raw_score = (
            _W_GOLD * gold_score
            + _W_HEALTH * health_score
            + _W_WAVE * wave_score
            + _W_THREAT * threat_score
            - _W_OBJECTIVE * obj_penalty
        )

        # Need at least a meaningful item purchase or critical health
        if gold_score < 0.1 and health_score < 0.6:
            return None

        confidence = max(0.0, min(1.0, raw_score))

        # Claude15: confidence gate
        if confidence < _CONFIDENCE_GATE:
            self._suppressed_count += 1
            return None

        # ── Build reasoning (original logic + wave/threat context) ───
        reasoning_parts = [f"You have {int(gold)}g"]

        if health_ratio < 0.4:
            reasoning_parts.append("low health")
        if mana_pct < 0.20 and player.max_mana > 100:
            reasoning_parts.append("almost out of mana")
        if name:
            if gold >= cost + 300:
                reasoning_parts.append(f"enough for {name} plus extras")
            else:
                reasoning_parts.append(f"enough for {name}")
        if wave_pos in (WavePosition.AT_ENEMY_TOWER,
                        WavePosition.PUSHING_TO_ENEMY):
            reasoning_parts.append("wave is pushing out")
        if threat_score > 0.7:
            reasoning_parts.append("enemies are away or dead")

        text = f"Good time to back. {'. '.join(reasoning_parts)}."

        self._last_recommendation_time = now
        self._last_item_recommended = name
        self._last_item_time = now
        self._recommendation_count += 1

        return BackRecommendation(
            should_back=True,
            item_name=name,
            gold_needed=cost,
            current_gold=gold,
            confidence=confidence,
            reasoning=text,
            gold_score=gold_score,
            health_score=health_score,
            wave_score=wave_score,
            threat_score=threat_score,
            objective_penalty=obj_penalty,
            wave_position=wave_pos.name,
        )

    @staticmethod
    def _is_objective_window(game_time: float) -> bool:
        """Check if an objective spawn is imminent.

        Original method preserved for backward compatibility.
        Claude15: Internal logic now delegates to ObjectiveWindowGuard,
        but this static method stays in case external code calls it.
        """
        # Dragon spawns at 5:00 and every 5:00 after
        # Baron spawns at 20:00 and every 6:00 after
        drake_windows = [300, 600, 900, 1200, 1500, 1800, 2100]
        baron_windows = [1200, 1560, 1920, 2280]
        for w in drake_windows + baron_windows:
            if abs(game_time - w) < 45:
                return True
        return False

    def reset_cooldowns(self) -> None:
        """Reset all cooldown state (useful for new game)."""
        self._last_recommendation_time = 0.0
        self._last_item_recommended = ""
        self._last_item_time = 0.0

    def stats(self) -> Dict[str, Any]:
        return {
            "recommendations": self._recommendation_count,
            "evaluations": self._evaluation_count,
            "suppressed": self._suppressed_count,
            "last_item": self._last_item_recommended,
        }
