"""
IngameDecisionSuggestionEngine — Generates tactical suggestions during live games.

Architecture (拿来主义):
  decision_engine.py — core decision logic patterns
  macro_decision_engine.py — macro-level game decisions (objectives, rotations)

Location: integrations/lol-history/src/lol_history/ingame_decision_suggestion_engine.py

Design Notes (Knuth-level critique):
  User:
    - Suggestions are phase-aware: laning (0-14min), mid-game (14-25min), late-game (25min+).
    - Each suggestion has a priority (critical/high/medium/low) and a confidence score.
    - Suggestions include reasoning text for the voice narrator downstream.
  System:
    - Rule-based engine with weighted scoring; no ML inference required at runtime.
    - Rules are composable: each rule produces a candidate, scoring selects top-N.
    - History integration: past match patterns influence suggestion weights.
    - Stateless per-call: all context passed in, no hidden game state.
"""
from __future__ import annotations

import logging
import math
import time
from collections import defaultdict, deque
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)
_EVOLUTION_KEY = "integrations.lol_history.ingame_decision_suggestion_engine.v1"


def _safe_div(a: float, b: float, d: float = 0.0) -> float:
    return a / b if b else d


# ─── Game Phase Classifier ───────────────────────────────────────────────────

class _GamePhaseClassifier:
    """Classifies current game phase based on game time and state."""

    PHASE_THRESHOLDS = {
        "early_laning": (0, 360),
        "mid_laning": (360, 840),
        "mid_game": (840, 1500),
        "late_game": (1500, 2400),
        "ultra_late": (2400, float("inf")),
    }

    @classmethod
    def classify(cls, game_time: float, state: Dict[str, Any] = None) -> str:
        for phase, (start, end) in cls.PHASE_THRESHOLDS.items():
            if start <= game_time < end:
                return phase
        return "ultra_late"

    @classmethod
    def get_phase_priorities(cls, phase: str) -> Dict[str, float]:
        """Get suggestion type weights for each game phase."""
        weights = {
            "early_laning": {"farm": 1.5, "trade": 1.2, "ward": 1.0, "objective": 0.3, "teamfight": 0.1, "split": 0.0, "recall": 0.8},
            "mid_laning": {"farm": 1.0, "trade": 1.0, "ward": 1.2, "objective": 0.8, "teamfight": 0.5, "split": 0.3, "recall": 0.7},
            "mid_game": {"farm": 0.5, "trade": 0.3, "ward": 1.0, "objective": 1.5, "teamfight": 1.2, "split": 1.0, "recall": 0.6},
            "late_game": {"farm": 0.2, "trade": 0.1, "ward": 1.2, "objective": 1.8, "teamfight": 1.5, "split": 0.8, "recall": 0.4},
            "ultra_late": {"farm": 0.1, "trade": 0.0, "ward": 1.0, "objective": 2.0, "teamfight": 2.0, "split": 0.5, "recall": 0.3},
        }
        return weights.get(phase, weights["mid_game"])


# ─── Suggestion Rules ────────────────────────────────────────────────────────

class _SuggestionCandidate:
    """A single suggestion candidate with scoring metadata."""

    def __init__(self, suggestion_type: str, text: str, reasoning: str,
                 base_score: float, priority: str = "medium") -> None:
        self.suggestion_type = suggestion_type
        self.text = text
        self.reasoning = reasoning
        self.base_score = base_score
        self.priority = priority
        self.final_score = base_score
        self.confidence = 0.5
        self.phase_weight = 1.0
        self.history_weight = 1.0

    def apply_weights(self, phase_weight: float, history_weight: float) -> None:
        self.phase_weight = phase_weight
        self.history_weight = history_weight
        self.final_score = self.base_score * phase_weight * history_weight

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.suggestion_type,
            "text": self.text,
            "reasoning": self.reasoning,
            "priority": self.priority,
            "score": round(self.final_score, 3),
            "confidence": round(self.confidence, 3),
            "base_score": self.base_score,
            "phase_weight": self.phase_weight,
            "history_weight": self.history_weight,
        }


class _ObjectiveRule:
    """Generates objective-related suggestions (dragon, baron, herald, tower)."""

    OBJECTIVE_RESPAWNS = {
        "dragon": 300, "baron": 360, "herald": 480, "elder": 360,
    }

    def evaluate(self, state: Dict[str, Any], phase: str) -> List[_SuggestionCandidate]:
        candidates = []
        game_time = state.get("game_time", 0)
        events = state.get("events", [])

        dragon_kills = sum(1 for e in events if e.get("EventName") == "DragonKill")
        baron_available = game_time >= 1200
        elder_possible = dragon_kills >= 4

        if baron_available and phase in ("mid_game", "late_game", "ultra_late"):
            candidates.append(_SuggestionCandidate(
                "objective", "准备做男爵", "男爵已刷新，团队应准备控制男爵区域视野",
                base_score=2.5, priority="critical"))

        if game_time >= 300 and dragon_kills < 4:
            candidates.append(_SuggestionCandidate(
                "objective", "争夺小龙", f"当前龙魂进度{dragon_kills}/4，保持小龙控制",
                base_score=1.8, priority="high"))

        if elder_possible:
            candidates.append(_SuggestionCandidate(
                "objective", "远古龙！最高优先级",
                "已集齐4条龙，远古龙BUFF将决定胜负",
                base_score=3.0, priority="critical"))

        if game_time < 840:
            candidates.append(_SuggestionCandidate(
                "objective", "推外塔镀层", "镀层在14分钟消失，尽量多推",
                base_score=1.2, priority="medium"))

        return candidates


class _FarmRule:
    """Generates farming-related suggestions."""

    def evaluate(self, state: Dict[str, Any], phase: str) -> List[_SuggestionCandidate]:
        candidates = []
        players = state.get("players", [])
        game_time = state.get("game_time", 0)

        for player in players:
            cs = player.get("cs", 0)
            minutes = game_time / 60.0 if game_time > 0 else 1.0
            cs_per_min = _safe_div(cs, minutes)

            if cs_per_min < 6.0 and phase in ("early_laning", "mid_laning"):
                candidates.append(_SuggestionCandidate(
                    "farm", f"补刀效率偏低({cs_per_min:.1f}/min)，专注补刀",
                    f"当前CS/min: {cs_per_min:.1f}，目标7+",
                    base_score=1.5, priority="medium"))

            if cs_per_min < 4.0 and phase == "mid_game":
                candidates.append(_SuggestionCandidate(
                    "farm", "经济落后，需要收线补兵",
                    "中期补刀严重不足，需要利用侧线兵线补经济",
                    base_score=1.8, priority="high"))

        return candidates


class _RecallRule:
    """Generates recall-related suggestions based on gold and HP."""

    def evaluate(self, state: Dict[str, Any], phase: str) -> List[_SuggestionCandidate]:
        candidates = []
        players = state.get("players", [])

        for player in players:
            gold = player.get("gold", 0)
            items = player.get("items", [])

            if gold >= 1300 and len(items) < 3:
                candidates.append(_SuggestionCandidate(
                    "recall", "回城补装备",
                    f"手头有{gold}金币，可以购买关键装备提升战斗力",
                    base_score=1.0, priority="medium"))

            if gold >= 3000:
                candidates.append(_SuggestionCandidate(
                    "recall", "金币充足，尽快回城出大件",
                    f"持有{gold}金币，出大件后战力将大幅提升",
                    base_score=1.5, priority="high"))

        return candidates


class _TeamfightRule:
    """Generates teamfight-related suggestions."""

    def evaluate(self, state: Dict[str, Any], phase: str) -> List[_SuggestionCandidate]:
        candidates = []
        if phase in ("mid_game", "late_game", "ultra_late"):
            candidates.append(_SuggestionCandidate(
                "teamfight", "注意团战站位，保护后排",
                "团战即将爆发，确保站位安全",
                base_score=1.0, priority="medium"))
        return candidates


class _SplitPushRule:
    """Generates split push suggestions."""

    def evaluate(self, state: Dict[str, Any], phase: str) -> List[_SuggestionCandidate]:
        candidates = []
        if phase in ("mid_game", "late_game"):
            candidates.append(_SuggestionCandidate(
                "split", "考虑分推侧线施压",
                "队伍可以4-1分推创造地图压力",
                base_score=0.8, priority="low"))
        return candidates


class _WardRule:
    """Generates vision-related suggestions."""

    def evaluate(self, state: Dict[str, Any], phase: str) -> List[_SuggestionCandidate]:
        candidates = []
        game_time = state.get("game_time", 0)

        if game_time >= 300:
            candidates.append(_SuggestionCandidate(
                "ward", "放置视野控制关键区域",
                "确保龙坑/男爵坑/河道视野充足",
                base_score=0.7, priority="low"))

        if phase in ("mid_game", "late_game"):
            candidates.append(_SuggestionCandidate(
                "ward", "清除敌方视野",
                "使用扫描清除敌方深层视野",
                base_score=0.9, priority="medium"))

        return candidates


# ─── History Weight Calculator ───────────────────────────────────────────────

class _HistoryWeightCalculator:
    """Calculates suggestion weights based on historical match patterns."""

    def compute_weights(self, history: Dict[str, Any],
                        suggestion_type: str) -> float:
        if not history:
            return 1.0

        matches = history.get("matches", [])
        if not matches:
            return 1.0

        recent = matches[-10:]
        wins = sum(1 for m in recent if m.get("win"))
        win_rate = _safe_div(wins, len(recent))

        weight = 1.0
        if win_rate < 0.4 and suggestion_type == "objective":
            weight = 1.3
        elif win_rate > 0.6 and suggestion_type == "farm":
            weight = 0.8
        elif suggestion_type == "teamfight" and win_rate < 0.35:
            weight = 1.5

        return weight


# ─── Suggestion Scorer ───────────────────────────────────────────────────────

class _SuggestionScorer:
    """Scores and ranks suggestions."""

    def __init__(self) -> None:
        self._scoring_count = 0

    def score_and_rank(self, candidates: List[_SuggestionCandidate],
                       phase_weights: Dict[str, float],
                       history_calc: _HistoryWeightCalculator,
                       history: Dict[str, Any],
                       top_n: int = 5) -> List[_SuggestionCandidate]:
        self._scoring_count += 1

        for c in candidates:
            pw = phase_weights.get(c.suggestion_type, 1.0)
            hw = history_calc.compute_weights(history, c.suggestion_type)
            c.apply_weights(pw, hw)
            c.confidence = min(1.0, c.final_score / 3.0)

        candidates.sort(key=lambda c: c.final_score, reverse=True)

        seen_types = set()
        deduplicated = []
        for c in candidates:
            if c.suggestion_type not in seen_types or c.priority == "critical":
                deduplicated.append(c)
                seen_types.add(c.suggestion_type)
            if len(deduplicated) >= top_n:
                break

        return deduplicated


class IngameDecisionSuggestionEngine:
    """Generates tactical suggestions based on real-time game state and history.

    Public API: generate_suggestions, get_top_suggestion, get_suggestions_by_type,
                get_phase_info, get_stats
    """

    def __init__(self, top_n: int = 5) -> None:
        self.evolution_callback: Optional[Callable] = None
        self._op_count = 0
        self._suggestion_count = 0
        self._top_n = top_n
        self._phase_classifier = _GamePhaseClassifier()
        self._rules = [
            _ObjectiveRule(), _FarmRule(), _RecallRule(),
            _TeamfightRule(), _SplitPushRule(), _WardRule(),
        ]
        self._history_calc = _HistoryWeightCalculator()
        self._scorer = _SuggestionScorer()
        self._suggestion_history: deque = deque(maxlen=500)
        self._phase_history: deque = deque(maxlen=100)

    def _fire(self, et: str, data: Dict) -> None:
        if self.evolution_callback:
            self.evolution_callback({"type": et, "key": _EVOLUTION_KEY, **data})

    def generate_suggestions(self, game_state: Dict[str, Any],
                             history: Dict[str, Any] = None) -> Dict[str, Any]:
        """Generate ranked suggestions for current game state."""
        self._op_count += 1
        self._suggestion_count += 1
        game_time = game_state.get("game_time", 0)
        phase = self._phase_classifier.classify(game_time, game_state)
        phase_weights = self._phase_classifier.get_phase_priorities(phase)

        all_candidates = []
        for rule in self._rules:
            try:
                candidates = rule.evaluate(game_state, phase)
                all_candidates.extend(candidates)
            except Exception as e:
                logger.warning("Rule %s failed: %s", type(rule).__name__, e)

        ranked = self._scorer.score_and_rank(
            all_candidates, phase_weights, self._history_calc,
            history or {}, self._top_n)

        suggestions = [c.to_dict() for c in ranked]

        record = {
            "game_time": game_time,
            "phase": phase,
            "suggestion_count": len(suggestions),
            "top_priority": suggestions[0]["priority"] if suggestions else None,
        }
        self._suggestion_history.append(record)
        self._phase_history.append({"ts": game_time, "phase": phase})

        self._fire("suggestions_generated", {
            "count": len(suggestions),
            "phase": phase,
        })

        return {
            "status": "ok",
            "game_time": game_time,
            "phase": phase,
            "suggestions": suggestions,
            "total_candidates": len(all_candidates),
            "generation_num": self._suggestion_count,
        }

    def get_top_suggestion(self, game_state: Dict[str, Any],
                           history: Dict[str, Any] = None) -> Dict[str, Any]:
        """Get single highest-priority suggestion."""
        self._op_count += 1
        result = self.generate_suggestions(game_state, history)
        suggestions = result.get("suggestions", [])
        return {
            "status": "ok",
            "top_suggestion": suggestions[0] if suggestions else None,
            "phase": result.get("phase"),
        }

    def get_suggestions_by_type(self, game_state: Dict[str, Any],
                                 suggestion_type: str,
                                 history: Dict[str, Any] = None) -> Dict[str, Any]:
        """Get suggestions filtered by type."""
        self._op_count += 1
        result = self.generate_suggestions(game_state, history)
        filtered = [s for s in result.get("suggestions", [])
                    if s["type"] == suggestion_type]
        return {
            "status": "ok",
            "type_filter": suggestion_type,
            "suggestions": filtered,
        }

    def get_phase_info(self, game_time: float) -> Dict[str, Any]:
        """Get current phase classification and weights."""
        self._op_count += 1
        phase = self._phase_classifier.classify(game_time)
        weights = self._phase_classifier.get_phase_priorities(phase)
        return {
            "status": "ok",
            "game_time": game_time,
            "phase": phase,
            "weights": weights,
        }

    def get_stats(self) -> Dict[str, Any]:
        self._op_count += 1
        return {
            "status": "ok",
            "op_count": self._op_count,
            "suggestion_count": self._suggestion_count,
            "rules_count": len(self._rules),
            "top_n": self._top_n,
            "recent_phases": list(self._phase_history)[-10:],
            "suggestion_history_size": len(self._suggestion_history),
        }
