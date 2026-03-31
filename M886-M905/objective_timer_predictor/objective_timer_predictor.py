#!/usr/bin/env python3
"""
M896 — ObjectiveTimerPredictor
================================
Predicts optimal timing for dragon/herald/baron contests based on
game state, team compositions, and historical patterns.

Dependencies: M895
Reference: M866-M885 objective_timing_engine pattern
"""
from __future__ import annotations
import asyncio, logging, math, time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional
from enum import Enum, auto

logger = logging.getLogger("M896.ObjectiveTimerPredictor")


class ObjectiveType(Enum):
    DRAGON = "dragon"
    RIFT_HERALD = "rift_herald"
    BARON = "baron"
    ELDER_DRAGON = "elder_dragon"
    VOID_GRUBS = "void_grubs"


class ContestRecommendation(Enum):
    TAKE = "take"
    CONTEST = "contest"
    CONCEDE = "concede"
    TRADE = "trade"


@dataclass
class ObjectiveState:
    obj_type: ObjectiveType
    spawn_time: float  # game time in seconds
    is_alive: bool = True
    last_taken_by: str = ""  # "ORDER" or "CHAOS"
    take_count_order: int = 0
    take_count_chaos: int = 0
    respawn_timer: float = 0  # seconds until next spawn

    @property
    def next_spawn(self) -> float:
        return self.spawn_time + self.respawn_timer

    def to_dict(self) -> Dict[str, Any]:
        return {"type": self.obj_type.value, "alive": self.is_alive,
                "spawn": self.spawn_time, "next_spawn": self.next_spawn,
                "order_takes": self.take_count_order, "chaos_takes": self.take_count_chaos}


OBJECTIVE_SPAWN_TIMES = {
    ObjectiveType.DRAGON: 300,        # 5:00
    ObjectiveType.RIFT_HERALD: 480,   # 8:00 (was 14:00 pre-S14)
    ObjectiveType.BARON: 1200,        # 20:00
    ObjectiveType.ELDER_DRAGON: 2100, # 35:00
    ObjectiveType.VOID_GRUBS: 300,    # 5:00
}

OBJECTIVE_RESPAWN_TIMES = {
    ObjectiveType.DRAGON: 300,
    ObjectiveType.RIFT_HERALD: 0,     # single spawn
    ObjectiveType.BARON: 360,
    ObjectiveType.ELDER_DRAGON: 360,
    ObjectiveType.VOID_GRUBS: 240,
}


@dataclass
class ContestWindow:
    objective: ObjectiveType
    window_start: float  # game time
    window_end: float
    recommendation: ContestRecommendation
    confidence: float = 0.5
    reasoning: str = ""
    setup_actions: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {"objective": self.objective.value,
                "window": f"{self.window_start:.0f}-{self.window_end:.0f}",
                "action": self.recommendation.value,
                "confidence": round(self.confidence, 2),
                "reason": self.reasoning, "setup": self.setup_actions}


class ObjectiveTimerPredictor:
    """
    Predicts objective contest windows and recommends actions.

    Uses real-time game data from M895 KDA tracker and M894 composition
    analysis to determine:
    1. When objectives will spawn
    2. Whether to contest, concede, or trade
    3. Optimal setup timing and positioning

    Updates every 5 seconds to provide real-time guidance.
    """

    def __init__(self, kda_tracker=None, comp_evaluator=None):
        self._kda = kda_tracker
        self._comp = comp_evaluator
        self._objectives: Dict[ObjectiveType, ObjectiveState] = {}
        self._upcoming_windows: List[ContestWindow] = []
        self._listeners: Dict[str, List[Callable]] = {}
        self._poll_task: Optional[asyncio.Task] = None
        self._shutdown = asyncio.Event()
        self._current_game_time: float = 0
        self._stats = {"predictions": 0, "alerts_sent": 0}
        self._init_objectives()
        logger.info("ObjectiveTimerPredictor initialized")

    def _init_objectives(self):
        for obj_type, spawn in OBJECTIVE_SPAWN_TIMES.items():
            self._objectives[obj_type] = ObjectiveState(
                obj_type=obj_type, spawn_time=spawn,
                respawn_timer=OBJECTIVE_RESPAWN_TIMES.get(obj_type, 300),
            )

    def on(self, event: str, cb: Callable):
        self._listeners.setdefault(event, []).append(cb)

    async def _emit(self, event: str, data: Any = None):
        for cb in self._listeners.get(event, []):
            try:
                if asyncio.iscoroutinefunction(cb):
                    await cb(data)
                else:
                    cb(data)
            except Exception as exc:
                logger.error("Emit error: %s", exc)

    async def start(self):
        self._shutdown.clear()
        self._poll_task = asyncio.create_task(self._predict_loop(), name="objective-predictor")
        logger.info("ObjectiveTimerPredictor started")

    async def stop(self):
        self._shutdown.set()
        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
        logger.info("Stopped. Stats: %s", self._stats)

    async def _predict_loop(self):
        while not self._shutdown.is_set():
            try:
                await self._update_predictions()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("Predict loop error: %s", exc)
            await asyncio.sleep(5.0)

    async def _update_predictions(self):
        """Recalculate objective windows based on current state."""
        gt = self._current_game_time
        windows = []

        for obj_type, state in self._objectives.items():
            spawn = state.spawn_time
            if gt < spawn - 60:
                continue  # too early

            if spawn - 45 <= gt <= spawn + 30:
                # Approaching spawn window
                window = self._evaluate_contest(obj_type, gt)
                windows.append(window)
                self._stats["predictions"] += 1

                # Alert if within 30 seconds
                if spawn - 30 <= gt <= spawn:
                    self._stats["alerts_sent"] += 1
                    await self._emit("objective_alert", window.to_dict())

        self._upcoming_windows = windows

    def _evaluate_contest(self, obj_type: ObjectiveType, game_time: float) -> ContestWindow:
        """Evaluate whether to contest an objective."""
        gold_diff = 0
        kill_diff = 0
        if self._kda:
            timeline = self._kda.get_gold_diff_timeline()
            if timeline:
                gold_diff = timeline[-1].get("gold", 0)
                kill_diff = timeline[-1].get("kills", 0)

        # Simple heuristic scoring
        score = 0
        if gold_diff > 3000:
            score += 3
        elif gold_diff > 1000:
            score += 1
        elif gold_diff < -3000:
            score -= 3
        elif gold_diff < -1000:
            score -= 1

        if kill_diff > 5:
            score += 2
        elif kill_diff < -5:
            score -= 2

        # Baron requires more advantage
        if obj_type == ObjectiveType.BARON:
            score -= 1  # higher bar for Baron

        if score >= 2:
            rec = ContestRecommendation.TAKE
            reasoning = "Team has significant advantage, take objective"
            confidence = min(0.9, 0.6 + score * 0.05)
        elif score >= 0:
            rec = ContestRecommendation.CONTEST
            reasoning = "Game is close, prepare to contest"
            confidence = 0.5
        elif score >= -2:
            rec = ContestRecommendation.TRADE
            reasoning = "Slight disadvantage, look for trade opportunity"
            confidence = 0.55
        else:
            rec = ContestRecommendation.CONCEDE
            reasoning = "Significant disadvantage, avoid 50/50"
            confidence = 0.65

        setup = self._get_setup_actions(obj_type, rec, game_time)
        state = self._objectives[obj_type]
        return ContestWindow(
            objective=obj_type, window_start=state.spawn_time - 45,
            window_end=state.spawn_time + 30, recommendation=rec,
            confidence=confidence, reasoning=reasoning, setup_actions=setup,
        )

    def _get_setup_actions(self, obj_type: ObjectiveType,
                          rec: ContestRecommendation, gt: float) -> List[str]:
        actions = []
        state = self._objectives[obj_type]
        time_until = state.spawn_time - gt

        if time_until > 30:
            actions.append(f"Prepare vision around {obj_type.value} in {time_until:.0f}s")
        if time_until > 15:
            actions.append("Clear enemy wards, place control ward")
        if rec in (ContestRecommendation.TAKE, ContestRecommendation.CONTEST):
            actions.append("Group with team, avoid getting picked")
            if obj_type == ObjectiveType.BARON:
                actions.append("Ensure all ultimates are available")
        elif rec == ContestRecommendation.TRADE:
            actions.append("If enemy starts objective, push opposite side")
        return actions

    def update_game_time(self, game_time: float):
        self._current_game_time = game_time

    def report_objective_taken(self, obj_type: ObjectiveType, taken_by: str):
        state = self._objectives[obj_type]
        state.is_alive = False
        state.last_taken_by = taken_by
        if taken_by == "ORDER":
            state.take_count_order += 1
        else:
            state.take_count_chaos += 1
        state.spawn_time = self._current_game_time + state.respawn_timer
        state.is_alive = True

    def get_upcoming_windows(self) -> List[Dict[str, Any]]:
        return [w.to_dict() for w in self._upcoming_windows]

    def export_stats(self) -> Dict[str, Any]:
        return {"predictor_stats": self._stats,
                "objectives": {k.value: v.to_dict() for k, v in self._objectives.items()}}



# ---------------------------------------------------------------------------
# Extended ObjectiveTimerPredictor utilities
# ---------------------------------------------------------------------------

class DragonSoulTracker:
    """Tracks dragon soul progress for both teams."""

    SOUL_REQUIREMENT = 4

    def __init__(self):
        self._order_dragons: List[str] = []
        self._chaos_dragons: List[str] = []
        self._dragon_types = ["infernal", "mountain", "ocean", "cloud", "hextech", "chemtech"]

    def record_dragon(self, team: str, dragon_type: str):
        if team == "ORDER":
            self._order_dragons.append(dragon_type)
        else:
            self._chaos_dragons.append(dragon_type)

    @property
    def order_count(self) -> int:
        return len(self._order_dragons)

    @property
    def chaos_count(self) -> int:
        return len(self._chaos_dragons)

    @property
    def order_has_soul(self) -> bool:
        return self.order_count >= self.SOUL_REQUIREMENT

    @property
    def chaos_has_soul(self) -> bool:
        return self.chaos_count >= self.SOUL_REQUIREMENT

    def next_is_soul_point(self, team: str) -> bool:
        count = self.order_count if team == "ORDER" else self.chaos_count
        return count == self.SOUL_REQUIREMENT - 1

    def get_priority_score(self) -> float:
        """Higher score = higher priority to contest dragon."""
        base = 5.0
        if self.next_is_soul_point("ORDER"):
            base += 3.0
        if self.next_is_soul_point("CHAOS"):
            base += 2.0
        if self.order_has_soul or self.chaos_has_soul:
            base -= 2.0  # soul already taken
        return min(10.0, base)

    def status(self) -> Dict[str, Any]:
        return {
            "order_dragons": self._order_dragons,
            "chaos_dragons": self._chaos_dragons,
            "order_has_soul": self.order_has_soul,
            "chaos_has_soul": self.chaos_has_soul,
            "next_is_soul": {
                "ORDER": self.next_is_soul_point("ORDER"),
                "CHAOS": self.next_is_soul_point("CHAOS"),
            },
            "priority": round(self.get_priority_score(), 1),
        }


class ObjectiveHistoryTracker:
    """Tracks historical objective timings for prediction accuracy."""

    def __init__(self):
        self._take_history: List[Dict[str, Any]] = []

    def record_take(self, obj_type: str, game_time: float, team: str,
                    setup_time: float = 0, contest_result: str = "uncontested"):
        self._take_history.append({
            "type": obj_type, "time": game_time, "team": team,
            "setup": setup_time, "contest": contest_result,
        })

    def average_take_time(self, obj_type: str) -> float:
        times = [h["time"] for h in self._take_history if h["type"] == obj_type]
        return sum(times) / len(times) if times else 0

    def contest_rate(self, obj_type: str) -> float:
        relevant = [h for h in self._take_history if h["type"] == obj_type]
        contested = [h for h in relevant if h["contest"] != "uncontested"]
        return len(contested) / len(relevant) * 100 if relevant else 0

    def get_history(self) -> List[Dict[str, Any]]:
        return list(self._take_history)


class JungleTimerManager:
    """Manages all jungle camp spawn timers."""

    CAMP_TIMERS = {
        "blue_buff": {"initial": 90, "respawn": 300},
        "red_buff": {"initial": 90, "respawn": 300},
        "gromp": {"initial": 102, "respawn": 120},
        "wolves": {"initial": 102, "respawn": 120},
        "raptors": {"initial": 102, "respawn": 120},
        "krugs": {"initial": 102, "respawn": 120},
        "scuttle": {"initial": 210, "respawn": 150},
    }

    def __init__(self):
        self._camp_states: Dict[str, Dict[str, Any]] = {}
        for camp, timers in self.CAMP_TIMERS.items():
            for side in ["blue_side", "red_side"]:
                key = f"{side}_{camp}"
                self._camp_states[key] = {
                    "alive": True, "next_spawn": timers["initial"],
                    "respawn": timers["respawn"],
                }

    def mark_cleared(self, camp_key: str, game_time: float):
        if camp_key in self._camp_states:
            state = self._camp_states[camp_key]
            state["alive"] = False
            state["next_spawn"] = game_time + state["respawn"]

    def get_upcoming_spawns(self, game_time: float, window: float = 60) -> List[Dict[str, Any]]:
        upcoming = []
        for key, state in self._camp_states.items():
            if not state["alive"] and state["next_spawn"] <= game_time + window:
                upcoming.append({
                    "camp": key, "spawn_in": state["next_spawn"] - game_time,
                })
        upcoming.sort(key=lambda x: x["spawn_in"])
        return upcoming



# ---------------------------------------------------------------------------
# Extended ObjectiveTimerPredictor utilities — metrics, serialization, diagnostics
# ---------------------------------------------------------------------------

class ObjectiveTimerPredictorMetrics:
    """Collects performance metrics for ObjectiveTimerPredictor."""

    def __init__(self):
        self._operation_times: List[float] = []
        self._error_counts: Dict[str, int] = collections.defaultdict(int)
        self._invocations = 0

    def record_operation(self, duration_ms: float):
        self._invocations += 1
        self._operation_times.append(duration_ms)
        if len(self._operation_times) > 1000:
            self._operation_times = self._operation_times[-1000:]

    def record_error(self, error_type: str):
        self._error_counts[error_type] += 1

    def get_summary(self) -> Dict[str, Any]:
        if not self._operation_times:
            return {"invocations": self._invocations, "errors": dict(self._error_counts)}
        sorted_times = sorted(self._operation_times)
        n = len(sorted_times)
        return {
            "invocations": self._invocations,
            "avg_ms": round(sum(sorted_times) / n, 2),
            "p50_ms": round(sorted_times[n // 2], 2),
            "p95_ms": round(sorted_times[int(n * 0.95)], 2),
            "p99_ms": round(sorted_times[int(n * 0.99)], 2),
            "max_ms": round(sorted_times[-1], 2),
            "errors": dict(self._error_counts),
        }


class ObjectiveTimerPredictorSerializer:
    """Serialization utilities for ObjectiveTimerPredictor state."""

    @staticmethod
    def serialize_state(state: Dict[str, Any]) -> str:
        return json.dumps(state, indent=2, ensure_ascii=False, default=str)

    @staticmethod
    def deserialize_state(data: str) -> Dict[str, Any]:
        try:
            return json.loads(data)
        except json.JSONDecodeError as exc:
            logger.error("Deserialize error: %s", exc)
            return {}

    @staticmethod
    def compute_state_hash(state: Dict[str, Any]) -> str:
        serialized = json.dumps(state, sort_keys=True, default=str)
        return hashlib.sha256(serialized.encode()).hexdigest()[:16]


class ObjectiveTimerPredictorDiagnostics:
    """Diagnostic tools for ObjectiveTimerPredictor troubleshooting."""

    def __init__(self, instance):
        self._instance = instance
        self._diagnostic_log: List[Dict[str, Any]] = []

    def run_self_test(self) -> Dict[str, Any]:
        """Run basic self-diagnostics."""
        results = {
            "module": "ObjectiveTimerPredictor",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "checks": [],
        }

        # Check 1: Instance exists
        results["checks"].append({
            "name": "instance_valid",
            "passed": self._instance is not None,
        })

        # Check 2: Has export_stats method
        has_stats = hasattr(self._instance, "export_stats")
        results["checks"].append({
            "name": "has_export_stats",
            "passed": has_stats,
        })

        # Check 3: export_stats returns valid data
        if has_stats:
            try:
                stats = self._instance.export_stats()
                results["checks"].append({
                    "name": "stats_callable",
                    "passed": isinstance(stats, dict),
                    "detail": f"{len(stats)} keys returned",
                })
            except Exception as exc:
                results["checks"].append({
                    "name": "stats_callable",
                    "passed": False,
                    "detail": str(exc),
                })

        # Check 4: Memory footprint estimate
        import sys
        size = sys.getsizeof(self._instance)
        results["checks"].append({
            "name": "memory_footprint",
            "passed": size < 10_000_000,  # 10MB threshold
            "detail": f"{size} bytes",
        })

        self._diagnostic_log.append(results)
        return results

    def get_diagnostic_history(self) -> List[Dict[str, Any]]:
        return list(self._diagnostic_log)


class ObjectiveTimerPredictorEventLogger:
    """Structured event logger for ObjectiveTimerPredictor with rotation."""

    def __init__(self, max_events: int = 500):
        self._events: List[Dict[str, Any]] = []
        self._max = max_events

    def log(self, event_type: str, data: Optional[Dict] = None, level: str = "info"):
        self._events.append({
            "type": event_type,
            "level": level,
            "data": data or {},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        if len(self._events) > self._max:
            self._events = self._events[-self._max:]

    def get_events(self, event_type: Optional[str] = None,
                   level: Optional[str] = None,
                   limit: int = 50) -> List[Dict[str, Any]]:
        filtered = self._events
        if event_type:
            filtered = [e for e in filtered if e["type"] == event_type]
        if level:
            filtered = [e for e in filtered if e["level"] == level]
        return filtered[-limit:]

    def count_by_type(self) -> Dict[str, int]:
        return dict(collections.Counter(e["type"] for e in self._events))

    def count_by_level(self) -> Dict[str, int]:
        return dict(collections.Counter(e["level"] for e in self._events))

    @property
    def total(self) -> int:
        return len(self._events)
