#!/usr/bin/env python3
"""
M898 — StrategyVoiceAdvisor
=============================
TTS voice advisor converting predictions and objective timers to speech.

Reference: M866-M885 voice_coach_narrator pattern
"""
from __future__ import annotations
import asyncio, collections, json, logging, math, os, sqlite3, time, hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
from enum import Enum, auto
from pathlib import Path

logger = logging.getLogger("M898.StrategyVoiceAdvisor")


class EventSeverity(Enum):
    INFO = auto()
    WARNING = auto()
    CRITICAL = auto()


@dataclass
class VoiceMessage:
    text: str
    severity: EventSeverity
    game_time: float
    category: str
    priority: int = 5
    cooldown_key: str = ""
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> Dict[str, Any]:
        return {"text": self.text, "severity": self.severity.name,
                "time": round(self.game_time, 1), "category": self.category,
                "priority": self.priority}


class CooldownManager:
    """Prevents spamming the same advice repeatedly."""
    def __init__(self):
        self._last_fired: Dict[str, float] = {}
        self._cooldowns: Dict[str, float] = {
            "objective_alert": 30, "win_prob_change": 45,
            "gold_swing": 20, "team_fight": 15, "general": 60,
        }

    def can_fire(self, key: str) -> bool:
        now = time.monotonic()
        cd = self._cooldowns.get(key, self._cooldowns["general"])
        last = self._last_fired.get(key, 0)
        if now - last >= cd:
            self._last_fired[key] = now
            return True
        return False


class TTSEngine:
    """Text-to-speech engine abstraction."""
    def __init__(self):
        self._enabled = True
        self._volume = 0.8
        self._rate = 180  # words per minute

    async def speak(self, text: str, priority: int = 5):
        if not self._enabled:
            return
        logger.info("[TTS] %s", text)
        # Production: pyttsx3 or system TTS
        # import pyttsx3; engine = pyttsx3.init(); engine.say(text); engine.runAndWait()

    def set_volume(self, vol: float): self._volume = max(0, min(1, vol))
    def set_rate(self, rate: int): self._rate = max(100, min(300, rate))
    def enable(self): self._enabled = True
    def disable(self): self._enabled = False


class StrategyVoiceAdvisor:
    """
    Converts game analysis into TTS voice guidance during 30-min sessions.

    Listens to events from:
    - M896 ObjectiveTimerPredictor → objective alerts
    - M897 WinProbabilityLiveEngine → momentum shifts
    - M895 RealTimeKDATracker → kill sprees, gold swings

    Manages cooldowns to avoid overwhelming the player with advice.
    """

    def __init__(self, win_engine=None, obj_predictor=None, kda_tracker=None):
        self._win = win_engine
        self._obj = obj_predictor
        self._kda = kda_tracker
        self._tts = TTSEngine()
        self._cooldown = CooldownManager()
        self._message_queue: List[VoiceMessage] = []
        self._message_history: List[VoiceMessage] = []
        self._poll_task: Optional[asyncio.Task] = None
        self._shutdown = asyncio.Event()
        self._session_start: Optional[float] = None
        self._stats = {"messages_spoken": 0, "messages_suppressed": 0, "session_duration": 0}
        logger.info("StrategyVoiceAdvisor initialized")

    async def start(self):
        self._shutdown.clear()
        self._session_start = time.monotonic()
        self._register_listeners()
        self._poll_task = asyncio.create_task(self._process_loop(), name="voice-advisor")
        logger.info("Voice advisor started")

    async def stop(self):
        self._shutdown.set()
        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
            try: await self._poll_task
            except asyncio.CancelledError: pass
        if self._session_start:
            self._stats["session_duration"] = time.monotonic() - self._session_start
        logger.info("Voice advisor stopped. Stats: %s", self._stats)

    def _register_listeners(self):
        if self._obj: self._obj.on("objective_alert", self._on_objective_alert)
        if self._win: self._win.on("trend_change", self._on_trend_change)
        if self._kda:
            self._kda.on("gold_swing", self._on_gold_swing)
            self._kda.on("kill_spree", self._on_kill_spree)

    async def _on_objective_alert(self, data):
        obj = data.get("objective", "objective")
        action = data.get("action", "prepare")
        msg = f"Attention: {obj} spawning soon. Recommendation: {action}"
        self._enqueue(msg, EventSeverity.WARNING, data.get("time", 0), "objective_alert", 3)

    async def _on_trend_change(self, data):
        trend = data.get("new", "stable")
        prob = data.get("prob", 0.5)
        if trend == "declining":
            msg = f"Win probability dropping to {prob*100:.0f}%. Play more carefully."
            self._enqueue(msg, EventSeverity.WARNING, 0, "win_prob_change", 4)
        elif trend == "improving":
            msg = f"Momentum shifting in our favor. Win probability: {prob*100:.0f}%."
            self._enqueue(msg, EventSeverity.INFO, 0, "win_prob_change", 6)

    async def _on_gold_swing(self, data):
        diff = data.get("diff", 0)
        direction = "gained" if diff > 0 else "lost"
        msg = f"Significant gold swing: {direction} {abs(diff):.0f} gold."
        self._enqueue(msg, EventSeverity.INFO, data.get("time", 0), "gold_swing", 5)

    async def _on_kill_spree(self, data):
        change = data.get("kill_diff_change", 0)
        if change > 0:
            msg = f"Great team fight! {change} kill advantage gained."
        else:
            msg = f"Team fight lost. {abs(change)} kills behind. Regroup and play safe."
        self._enqueue(msg, EventSeverity.INFO if change > 0 else EventSeverity.WARNING,
                      data.get("time", 0), "team_fight", 4)

    def _enqueue(self, text: str, severity: EventSeverity, game_time: float,
                 category: str, priority: int):
        msg = VoiceMessage(text=text, severity=severity, game_time=game_time,
                           category=category, priority=priority, cooldown_key=category)
        self._message_queue.append(msg)
        self._message_queue.sort(key=lambda m: m.priority)

    async def _process_loop(self):
        while not self._shutdown.is_set():
            try:
                while self._message_queue:
                    msg = self._message_queue.pop(0)
                    if self._cooldown.can_fire(msg.cooldown_key):
                        await self._tts.speak(msg.text, msg.priority)
                        self._message_history.append(msg)
                        self._stats["messages_spoken"] += 1
                    else:
                        self._stats["messages_suppressed"] += 1
            except asyncio.CancelledError: raise
            except Exception as exc:
                logger.error("Process loop error: %s", exc)
            await asyncio.sleep(0.5)

    def get_message_history(self) -> List[Dict[str, Any]]:
        return [m.to_dict() for m in self._message_history[-50:]]

    def export_stats(self) -> Dict[str, Any]:
        return {"advisor_stats": self._stats, "messages_in_queue": len(self._message_queue),
                "total_spoken": len(self._message_history)}



# ---------------------------------------------------------------------------
# Extended StrategyVoiceAdvisor utilities
# ---------------------------------------------------------------------------

class AdviceTemplate:
    """Predefined advice templates for common game situations."""

    TEMPLATES = {
        "dragon_soul_point": "Dragon soul point! This is a must-contest dragon. Group early and establish vision.",
        "baron_power_play": "Team has a significant lead. Consider Baron to close out the game.",
        "behind_safe_play": "We're behind in gold. Focus on farming safely and avoid risky fights.",
        "enemy_power_spike": "Enemy carry just completed a major item. Be cautious in fights.",
        "split_push_opportunity": "Enemy is grouped mid. Consider splitting a side lane for pressure.",
        "elder_dragon_spawn": "Elder Dragon spawning soon. This could decide the game. All ultimates must be ready.",
        "inhibitor_down": "Inhibitor is down. Use super minion pressure to control the map.",
        "ace_capitalize": "Enemy team aced! Push objectives — don't reset without taking something.",
        "death_timer_warning": "Long death timers now. One mistake could cost the game. Play safe.",
        "ward_reminder": "Vision is low. Buy control wards and sweep objectives before contesting.",
    }

    @classmethod
    def get(cls, key: str) -> str:
        return cls.TEMPLATES.get(key, "")

    @classmethod
    def all_keys(cls) -> List[str]:
        return list(cls.TEMPLATES.keys())


class SituationalAdvisor:
    """Generates context-aware advice based on game state."""

    def __init__(self):
        self._last_advice_time: Dict[str, float] = {}
        self._advice_cooldown = 60  # seconds between same advice type

    def evaluate(self, game_time: float, gold_diff: float, kill_diff: int,
                 team_data: Optional[Dict] = None) -> List[VoiceMessage]:
        messages = []

        # Behind warnings
        if gold_diff < -5000 and self._can_advise("behind_safe_play", game_time):
            messages.append(VoiceMessage(
                text=AdviceTemplate.get("behind_safe_play"),
                severity=EventSeverity.WARNING, game_time=game_time,
                category="macro", priority=3, cooldown_key="behind_safe_play",
            ))
            self._last_advice_time["behind_safe_play"] = game_time

        # Ahead opportunity
        if gold_diff > 5000 and self._can_advise("baron_power_play", game_time):
            messages.append(VoiceMessage(
                text=AdviceTemplate.get("baron_power_play"),
                severity=EventSeverity.INFO, game_time=game_time,
                category="macro", priority=4, cooldown_key="baron_power_play",
            ))
            self._last_advice_time["baron_power_play"] = game_time

        # Death timer warning (late game)
        if game_time > 2100 and self._can_advise("death_timer_warning", game_time):
            messages.append(VoiceMessage(
                text=AdviceTemplate.get("death_timer_warning"),
                severity=EventSeverity.WARNING, game_time=game_time,
                category="macro", priority=2, cooldown_key="death_timer_warning",
            ))
            self._last_advice_time["death_timer_warning"] = game_time

        # Ward reminder every 3 minutes
        if game_time > 300 and game_time % 180 < 5 and self._can_advise("ward_reminder", game_time):
            messages.append(VoiceMessage(
                text=AdviceTemplate.get("ward_reminder"),
                severity=EventSeverity.INFO, game_time=game_time,
                category="vision", priority=6, cooldown_key="ward_reminder",
            ))
            self._last_advice_time["ward_reminder"] = game_time

        return messages

    def _can_advise(self, key: str, game_time: float) -> bool:
        last = self._last_advice_time.get(key, -999)
        return game_time - last >= self._advice_cooldown


class VoiceHistoryExporter:
    """Exports voice advice history for post-game review."""

    @staticmethod
    def export_timeline(messages: List[VoiceMessage]) -> List[Dict[str, Any]]:
        timeline = []
        for msg in messages:
            timeline.append({
                "time": round(msg.game_time, 1),
                "text": msg.text,
                "severity": msg.severity.name,
                "category": msg.category,
            })
        return timeline

    @staticmethod
    def compute_stats(messages: List[VoiceMessage]) -> Dict[str, Any]:
        if not messages:
            return {"count": 0}
        categories = collections.Counter(m.category for m in messages)
        severities = collections.Counter(m.severity.name for m in messages)
        return {
            "count": len(messages),
            "categories": dict(categories),
            "severities": dict(severities),
            "first_advice_time": round(messages[0].game_time, 1) if messages else 0,
            "last_advice_time": round(messages[-1].game_time, 1) if messages else 0,
            "avg_interval_seconds": round(
                (messages[-1].game_time - messages[0].game_time) / max(len(messages) - 1, 1), 1
            ) if len(messages) > 1 else 0,
        }


class TTSQueueManager:
    """Advanced TTS queue with priority-based interruption."""

    def __init__(self):
        self._queue: List[VoiceMessage] = []
        self._max_queue_size = 10
        self._currently_speaking = False

    def enqueue(self, msg: VoiceMessage):
        if len(self._queue) >= self._max_queue_size:
            # Drop lowest priority
            self._queue.sort(key=lambda m: m.priority, reverse=True)
            if self._queue and self._queue[-1].priority > msg.priority:
                self._queue.pop()
            else:
                return  # new message is lower priority than all queued
        self._queue.append(msg)
        self._queue.sort(key=lambda m: m.priority)

    def dequeue(self) -> Optional[VoiceMessage]:
        if not self._queue:
            return None
        return self._queue.pop(0)

    def clear(self):
        self._queue.clear()

    @property
    def size(self) -> int:
        return len(self._queue)

    def should_interrupt(self, new_msg: VoiceMessage) -> bool:
        """Check if a new critical message should interrupt current speech."""
        return new_msg.severity == EventSeverity.CRITICAL and new_msg.priority <= 2



# ---------------------------------------------------------------------------
# Extended StrategyVoiceAdvisor utilities — metrics, serialization, diagnostics
# ---------------------------------------------------------------------------

class StrategyVoiceAdvisorMetrics:
    """Collects performance metrics for StrategyVoiceAdvisor."""

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


class StrategyVoiceAdvisorSerializer:
    """Serialization utilities for StrategyVoiceAdvisor state."""

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


class StrategyVoiceAdvisorDiagnostics:
    """Diagnostic tools for StrategyVoiceAdvisor troubleshooting."""

    def __init__(self, instance):
        self._instance = instance
        self._diagnostic_log: List[Dict[str, Any]] = []

    def run_self_test(self) -> Dict[str, Any]:
        """Run basic self-diagnostics."""
        results = {
            "module": "StrategyVoiceAdvisor",
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


class StrategyVoiceAdvisorEventLogger:
    """Structured event logger for StrategyVoiceAdvisor with rotation."""

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
