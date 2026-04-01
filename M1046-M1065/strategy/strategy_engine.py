#!/usr/bin/env python3
"""
M1051: Strategy Engine — Real-time Tactical Recommendation System
==================================================================
OperatorRL M1046-M1065 · 自部署 自环境反馈 自演化

Core strategy computation engine. Consumes ThreatAssessments (M1048),
GameContext (M1049), and TrendData (M1050) to produce actionable
tactical recommendations at each game phase.

Pattern: Read analysis/opponent_behavior_analyzer.py ThreatAssessment
→ understand threat scores → implement recommendation generator that
produces phase-appropriate advice. Then integrate with core/game_state_tracker.py
phase transitions to trigger recommendations at the right moment.

Log-driven: 101 strategy_engine + 40 reward events in test session.
Strategy acceptance rate = 40/101 ≈ 40%. Target: >60% acceptance
through better context-awareness and timing.

Output flows to Voice Engine (M1053) for TTS and to dashboard.
"""

import asyncio
import json
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, auto
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).parent.parent))
try:
    from evo_logging.evolution_logger import get_logger, LogCategory
    from analysis.opponent_behavior_analyzer import ThreatAssessment, ThreatLevel
    from core.game_state_tracker import GamePhase, GameContext
except ImportError:
    pass


class RecommendationType(Enum):
    BAN_SUGGESTION = "ban_suggestion"
    PICK_SUGGESTION = "pick_suggestion"
    LANE_WARNING = "lane_warning"
    OBJECTIVE_CALL = "objective_call"
    TEAMFIGHT_ADVICE = "teamfight_advice"
    ITEM_SUGGESTION = "item_suggestion"
    WARD_PLACEMENT = "ward_placement"
    ROAM_TIMING = "roam_timing"
    BACK_TIMING = "back_timing"
    DANGER_ALERT = "danger_alert"
    POWER_SPIKE = "power_spike"
    GENERAL_TIP = "general_tip"


class Priority(Enum):
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4


@dataclass
class Recommendation:
    """Single actionable recommendation."""
    rec_id: str
    rec_type: str
    priority: int
    title: str
    detail: str
    phase: str
    match_time_sec: Optional[float] = None
    confidence: float = 0.5
    expires_sec: float = 30.0      # How long this rec is valid
    created_at: float = field(default_factory=time.monotonic)
    accepted: Optional[bool] = None
    related_champion: Optional[str] = None
    related_opponent: Optional[str] = None
    voice_text: Optional[str] = None  # Compact text for TTS

    @property
    def is_expired(self) -> bool:
        return time.monotonic() - self.created_at > self.expires_sec

    def to_dict(self) -> Dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()
                if v is not None and k != 'created_at'}


@dataclass
class StrategyState:
    """Internal state of the strategy engine for current game."""
    recommendations_generated: int = 0
    recommendations_accepted: int = 0
    recommendations_rejected: int = 0
    active_recommendations: List[Recommendation] = field(default_factory=list)
    recommendation_history: List[Dict] = field(default_factory=list)
    current_phase: GamePhase = GamePhase.NONE
    threat_assessments: Dict[str, Any] = field(default_factory=dict)
    game_timer_sec: float = 0.0
    last_recommendation_time: float = 0.0

    @property
    def acceptance_rate(self) -> float:
        total = self.recommendations_accepted + self.recommendations_rejected
        if total == 0:
            return 0.0
        return round(self.recommendations_accepted / total * 100, 1)


class StrategyEngine:
    """
    Real-time tactical recommendation engine.

    Registered as a listener on GameStateTracker phase transitions.
    Generates phase-appropriate recommendations:

    ChampSelect → Ban/pick suggestions based on opponent profiles
    Loading → Matchup warnings, lane-specific advice
    InGame (0-15min) → Laning phase tips, gank warnings
    InGame (15-25min) → Objective calls, teamfight positioning
    InGame (25min+) → Win condition focus, baron/elder calls
    PostGame → Performance review, improvement suggestions
    """
    # Minimum interval between recommendations (avoid spam)
    MIN_REC_INTERVAL_SEC = 10.0
    # Maximum active (non-expired) recommendations
    MAX_ACTIVE_RECS = 5

    def __init__(self):
        self._state = StrategyState()
        self._logger = get_logger()
        self._listeners: List[Callable[[Recommendation], None]] = []
        self._rec_counter = 0

    def add_listener(self, listener: Callable[[Recommendation], None]) -> None:
        self._listeners.append(listener)

    def on_phase_change(
        self, old: GamePhase, new: GamePhase, context: GameContext
    ) -> List[Recommendation]:
        """Generate recommendations on phase transition."""
        self._state.current_phase = new
        recs = []
        if new == GamePhase.CHAMP_SELECT:
            recs = self._champ_select_strategy(context)
        elif new == GamePhase.GAME_START:
            recs = self._loading_strategy(context)
        elif new == GamePhase.IN_PROGRESS:
            recs = self._early_game_strategy(context)
        elif new == GamePhase.END_OF_GAME:
            recs = self._post_game_review(context)
        for r in recs:
            self._emit_recommendation(r)
        return recs

    def on_threat_update(
        self, assessments: Dict[str, Any]
    ) -> None:
        """Update threat data for strategy computation."""
        self._state.threat_assessments = assessments

    def on_game_timer_tick(
        self, match_time_sec: float, context: GameContext
    ) -> List[Recommendation]:
        """
        Periodic strategy check during InProgress phase.

        Called at regular intervals (e.g., every 30 seconds) to
        generate time-sensitive recommendations.
        """
        self._state.game_timer_sec = match_time_sec
        now = time.monotonic()
        if now - self._state.last_recommendation_time < self.MIN_REC_INTERVAL_SEC:
            return []
        # Prune expired
        self._state.active_recommendations = [
            r for r in self._state.active_recommendations if not r.is_expired]
        recs = []
        # Laning phase (0-15 min)
        if match_time_sec < 900:
            recs.extend(self._laning_phase_recs(match_time_sec, context))
        # Mid game (15-25 min)
        elif match_time_sec < 1500:
            recs.extend(self._mid_game_recs(match_time_sec, context))
        # Late game (25+ min)
        else:
            recs.extend(self._late_game_recs(match_time_sec, context))
        for r in recs:
            self._emit_recommendation(r)
        return recs

    def record_feedback(self, rec_id: str, accepted: bool) -> None:
        """Record user feedback on a recommendation."""
        if accepted:
            self._state.recommendations_accepted += 1
        else:
            self._state.recommendations_rejected += 1
        for r in self._state.active_recommendations:
            if r.rec_id == rec_id:
                r.accepted = accepted
                break
        reward = 1.0 if accepted else -0.5
        self._logger.reward(
            LogCategory.STRATEGY_ENGINE,
            f"Recommendation {'accepted' if accepted else 'rejected'}: {rec_id}",
            reward_signal=reward,
            data={'rec_id': rec_id, 'acceptance_rate': self._state.acceptance_rate})

    # ---- Phase-specific strategy generators ----

    def _champ_select_strategy(
        self, context: GameContext
    ) -> List[Recommendation]:
        recs = []
        threats = self._state.threat_assessments
        if not threats:
            return recs
        # Ban suggestions: highest ban_priority champions
        ban_candidates = []
        for puuid, assessment in threats.items():
            if isinstance(assessment, dict):
                for champ, threat in assessment.get('champion_threat', {}).items():
                    ban_candidates.append((champ, threat, assessment.get('summoner_name', '')))
        ban_candidates.sort(key=lambda x: -x[1])
        for i, (champ, threat, owner) in enumerate(ban_candidates[:3]):
            recs.append(self._make_rec(
                RecommendationType.BAN_SUGGESTION,
                Priority.HIGH if i == 0 else Priority.MEDIUM,
                f"Ban {champ}",
                f"{owner}'s {champ} has threat score {threat:.1f}/10. "
                f"{'Top priority ban.' if i == 0 else 'Alternative ban.'}",
                context,
                voice_text=f"Consider banning {champ}, {owner} is strong on it",
                related_champion=champ,
                related_opponent=owner,
                confidence=min(threat / 10, 1.0),
            ))
        return recs

    def _loading_strategy(
        self, context: GameContext
    ) -> List[Recommendation]:
        recs = []
        threats = self._state.threat_assessments
        # Lane matchup warnings
        for puuid, assessment in (threats or {}).items():
            if isinstance(assessment, dict):
                level = assessment.get('threat_level', 'moderate')
                name = assessment.get('summoner_name', 'Opponent')
                if level in ('high', 'critical'):
                    playstyle = assessment.get('primary_playstyle', 'balanced')
                    weaknesses = assessment.get('weaknesses', [])
                    detail = f"{name} ({assessment.get('rank_estimate_mmr', '?')} MMR) "
                    detail += f"plays {playstyle}."
                    if weaknesses:
                        detail += f" Weakness: {weaknesses[0]}"
                    recs.append(self._make_rec(
                        RecommendationType.LANE_WARNING,
                        Priority.HIGH,
                        f"Watch out for {name}",
                        detail, context,
                        voice_text=f"Careful, {name} is a high threat, plays {playstyle}",
                        related_opponent=name,
                        expires_sec=120.0,
                    ))
        return recs[:3]

    def _early_game_strategy(
        self, context: GameContext
    ) -> List[Recommendation]:
        return [self._make_rec(
            RecommendationType.GENERAL_TIP,
            Priority.MEDIUM,
            "Early game focus",
            "Focus on CS and wave management. Track enemy jungler position. "
            "Ward river at 2:30 for early gank protection.",
            context,
            voice_text="Focus on farming, ward river at two thirty",
            expires_sec=60.0,
        )]

    def _laning_phase_recs(
        self, time_sec: float, context: GameContext
    ) -> List[Recommendation]:
        recs = []
        # Objective timers
        if 270 < time_sec < 330:  # ~5 min: scuttle crab
            recs.append(self._make_rec(
                RecommendationType.OBJECTIVE_CALL,
                Priority.MEDIUM,
                "Scuttle crab spawning",
                "Scuttle crab spawns soon. Push wave and help jungler secure it.",
                context, match_time_sec=time_sec,
                voice_text="Scuttle crab soon, push your wave",
                expires_sec=60.0))
        if 810 < time_sec < 870:  # ~14 min: rift herald
            recs.append(self._make_rec(
                RecommendationType.OBJECTIVE_CALL,
                Priority.HIGH,
                "Rift Herald window",
                "Rift Herald despawns at 19:55. Coordinate with jungler to secure.",
                context, match_time_sec=time_sec,
                voice_text="Rift Herald available, coordinate with jungler",
                expires_sec=120.0))
        return recs

    def _mid_game_recs(
        self, time_sec: float, context: GameContext
    ) -> List[Recommendation]:
        recs = []
        if 1200 < time_sec < 1260:  # ~20 min: first baron window
            recs.append(self._make_rec(
                RecommendationType.OBJECTIVE_CALL,
                Priority.HIGH,
                "Baron Nashor spawned",
                "Baron is now available. Group for vision control. "
                "Don't face-check without wards.",
                context, match_time_sec=time_sec,
                voice_text="Baron is up, set up vision around pit",
                expires_sec=120.0))
        return recs

    def _late_game_recs(
        self, time_sec: float, context: GameContext
    ) -> List[Recommendation]:
        recs = []
        recs.append(self._make_rec(
            RecommendationType.TEAMFIGHT_ADVICE,
            Priority.HIGH,
            "Late game — one fight decides it",
            "Avoid getting caught alone. Group for objectives. "
            "Elder Dragon and Baron are win conditions.",
            context, match_time_sec=time_sec,
            voice_text="Late game. Stay grouped. One fight can end the game.",
            expires_sec=180.0))
        return recs

    def _post_game_review(
        self, context: GameContext
    ) -> List[Recommendation]:
        return [self._make_rec(
            RecommendationType.GENERAL_TIP,
            Priority.LOW,
            "Post-game review",
            f"Strategy acceptance rate: {self._state.acceptance_rate}%. "
            f"Total recommendations: {self._state.recommendations_generated}. "
            f"Review your replay for improvement opportunities.",
            context,
            expires_sec=300.0)]

    # ---- Helpers ----

    def _make_rec(
        self, rec_type: RecommendationType, priority: Priority,
        title: str, detail: str, context: GameContext, **kwargs
    ) -> Recommendation:
        self._rec_counter += 1
        return Recommendation(
            rec_id=f"rec_{self._rec_counter:06d}",
            rec_type=rec_type.value,
            priority=priority.value,
            title=title,
            detail=detail,
            phase=self._state.current_phase.value,
            **kwargs)

    def _emit_recommendation(self, rec: Recommendation) -> None:
        self._state.recommendations_generated += 1
        self._state.active_recommendations.append(rec)
        # Cap active recommendations
        if len(self._state.active_recommendations) > self.MAX_ACTIVE_RECS:
            self._state.active_recommendations = sorted(
                self._state.active_recommendations,
                key=lambda r: -r.priority)[:self.MAX_ACTIVE_RECS]
        self._state.last_recommendation_time = time.monotonic()
        self._state.recommendation_history.append(rec.to_dict())
        self._logger.info(
            LogCategory.STRATEGY_ENGINE,
            f"[{rec.rec_type}] {rec.title}",
            data={'priority': rec.priority, 'confidence': rec.confidence})
        for listener in self._listeners:
            try:
                listener(rec)
            except Exception as e:
                self._logger.error(
                    LogCategory.STRATEGY_ENGINE, f"Listener error: {e}")

    def get_active_recommendations(self) -> List[Dict]:
        self._state.active_recommendations = [
            r for r in self._state.active_recommendations if not r.is_expired]
        return [r.to_dict() for r in self._state.active_recommendations]

    def get_stats(self) -> Dict[str, Any]:
        return {
            'total_generated': self._state.recommendations_generated,
            'accepted': self._state.recommendations_accepted,
            'rejected': self._state.recommendations_rejected,
            'acceptance_rate': self._state.acceptance_rate,
            'active_count': len(self._state.active_recommendations),
        }


class MacroStrategyAdvisor:
    """
    Provides macro-level strategic advice based on game state.

    Macro strategy covers: when to push, when to group, when to
    split-push, objective prioritization, vision control zones.

    Production critique:
        1. User: Advice is specific and timed: "Push mid tower now"
           vs vague "play safe". Each recommendation includes a
           confidence score.
        2. System: Decision tree is explicit and debuggable. No
           neural network black-box — every recommendation can be
           traced to specific game state inputs.
    """
    def __init__(self):
        self._game_time: float = 0.0
        self._gold_diff: int = 0
        self._kill_diff: int = 0
        self._tower_diff: int = 0
        self._dragon_count: Dict[int, int] = {100: 0, 200: 0}
        self._baron_active: Dict[int, bool] = {100: False, 200: False}

    def update_state(
        self, game_time: float, gold_diff: int,
        kill_diff: int, tower_diff: int,
        dragon_count: Optional[Dict] = None,
        baron_active: Optional[Dict] = None
    ) -> None:
        self._game_time = game_time
        self._gold_diff = gold_diff
        self._kill_diff = kill_diff
        self._tower_diff = tower_diff
        if dragon_count:
            self._dragon_count = dragon_count
        if baron_active:
            self._baron_active = baron_active

    def get_macro_recommendation(self, my_team_id: int = 100) -> Dict:
        """Generate macro strategy recommendation."""
        is_ahead = (self._gold_diff > 0) == (my_team_id == 100)
        gold_lead = abs(self._gold_diff)
        phase = self._get_game_phase()

        if phase == 'early':
            return self._early_game_advice(is_ahead, gold_lead)
        elif phase == 'mid':
            return self._mid_game_advice(is_ahead, gold_lead)
        else:
            return self._late_game_advice(is_ahead, gold_lead)

    def _get_game_phase(self) -> str:
        if self._game_time < 900:
            return 'early'
        elif self._game_time < 1500:
            return 'mid'
        return 'late'

    def _early_game_advice(self, ahead: bool, lead: int) -> Dict:
        if ahead and lead > 1500:
            return {
                'strategy': 'aggressive_laning',
                'message': 'Strong early lead. Zone opponent from CS.',
                'priority': 'push_advantage',
                'confidence': 0.75,
            }
        return {
            'strategy': 'safe_farming',
            'message': 'Farm safely. Trade when abilities are up.',
            'priority': 'cs_focus',
            'confidence': 0.70,
        }

    def _mid_game_advice(self, ahead: bool, lead: int) -> Dict:
        if ahead and lead > 3000:
            return {
                'strategy': 'siege_objectives',
                'message': 'Group for objectives. Force fights with lead.',
                'priority': 'baron_dragon',
                'confidence': 0.80,
            }
        elif not ahead and lead > 3000:
            return {
                'strategy': 'turtle_scale',
                'message': 'Avoid 5v5. Split push and pick fights.',
                'priority': 'wave_management',
                'confidence': 0.65,
            }
        return {
            'strategy': 'contest_objectives',
            'message': 'Even game. Set up vision around Dragon.',
            'priority': 'vision_control',
            'confidence': 0.70,
        }

    def _late_game_advice(self, ahead: bool, lead: int) -> Dict:
        baron_up = not any(self._baron_active.values())
        if ahead:
            if baron_up:
                return {
                    'strategy': 'baron_force',
                    'message': 'Baron is key. Set up vision and force.',
                    'priority': 'baron',
                    'confidence': 0.85,
                }
            return {
                'strategy': 'close_out',
                'message': 'Push with numbers advantage. End the game.',
                'priority': 'inhibitor',
                'confidence': 0.80,
            }
        return {
            'strategy': 'defend_scale',
            'message': 'Defend base. Look for a pick to swing the game.',
            'priority': 'base_defense',
            'confidence': 0.70,
        }
