"""
IngameVoiceNarrator — Converts decision suggestions to short voice commands.

Architecture (拿来主义):
  history_intel_voice_briefer.py（M764）— voice briefing generation, TTS formatting
  realtime_voice_command_generator.py — real-time voice command patterns

Location: integrations/lol-history/src/lol_history/ingame_voice_narrator.py

Design Notes (Knuth-level critique):
  User:
    - Voice commands are ≤5 seconds spoken time (≤15 Chinese characters).
    - Frequency control: ≤1 command per 30 seconds to avoid distraction.
    - Urgency grading: flash/baron/teamfight = immediate; items/recall = delayed.
    - Commands are self-contained; no context from previous commands needed.
  System:
    - Template-based generation: zero latency, no LLM inference.
    - Cooldown tracked per-suggestion-type: high-priority overrides cooldown.
    - TTS output format: plain text with SSML markers for pause/emphasis.
    - Queue with priority: critical suggestions skip the queue.
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)
_EVOLUTION_KEY = "integrations.lol_history.ingame_voice_narrator.v1"


def _safe_div(a: float, b: float, d: float = 0.0) -> float:
    return a / b if b else d


# ─── Urgency Classification ─────────────────────────────────────────────────

class UrgencyLevel:
    """Urgency levels for voice output scheduling."""
    IMMEDIATE = 0
    HIGH = 1
    NORMAL = 2
    LOW = 3
    DEFERRED = 4

    PRIORITY_TO_URGENCY = {
        "critical": IMMEDIATE,
        "high": HIGH,
        "medium": NORMAL,
        "low": LOW,
        "info": DEFERRED,
    }

    TYPE_URGENCY_OVERRIDE = {
        "flash_warning": IMMEDIATE,
        "baron_call": IMMEDIATE,
        "elder_call": IMMEDIATE,
        "teamfight": HIGH,
        "gank_alert": HIGH,
        "objective": HIGH,
        "recall": NORMAL,
        "farm": LOW,
        "ward": LOW,
        "split": LOW,
        "trade": NORMAL,
    }

    @classmethod
    def classify(cls, suggestion: Dict[str, Any]) -> int:
        stype = suggestion.get("type", "")
        priority = suggestion.get("priority", "medium")
        type_urgency = cls.TYPE_URGENCY_OVERRIDE.get(stype)
        priority_urgency = cls.PRIORITY_TO_URGENCY.get(priority, cls.NORMAL)
        if type_urgency is not None:
            return min(type_urgency, priority_urgency)
        return priority_urgency


# ─── Voice Templates ─────────────────────────────────────────────────────────

class _VoiceTemplateEngine:
    """Template engine for generating short voice commands in Chinese."""

    TEMPLATES = {
        "objective": {
            "critical": "立刻做{target}！",
            "high": "准备{target}",
            "medium": "注意{target}时机",
            "low": "可以考虑{target}",
        },
        "farm": {
            "high": "补刀，补刀",
            "medium": "注意补兵",
            "low": "清线补经济",
        },
        "recall": {
            "high": "立刻回城出装",
            "medium": "找机会回城",
            "low": "可以回城了",
        },
        "teamfight": {
            "critical": "团战！注意站位！",
            "high": "准备团战",
            "medium": "团战站位注意",
        },
        "ward": {
            "high": "放眼！",
            "medium": "插视野",
            "low": "记得放眼",
        },
        "split": {
            "high": "分推侧线",
            "medium": "去边线带线",
            "low": "可以分推",
        },
        "gank_alert": {
            "critical": "小心Gank！撤退！",
            "high": "注意Gank",
            "medium": "对面可能来抓",
        },
        "flash_warning": {
            "critical": "闪现！闪现！",
        },
        "baron_call": {
            "critical": "开男爵！立刻！",
        },
        "elder_call": {
            "critical": "远古龙！全力争夺！",
        },
    }

    DEFAULT_TEMPLATE = "注意{text}"

    def generate(self, suggestion: Dict[str, Any]) -> str:
        """Generate voice command text from suggestion."""
        stype = suggestion.get("type", "unknown")
        priority = suggestion.get("priority", "medium")
        text = suggestion.get("text", "")

        type_templates = self.TEMPLATES.get(stype, {})
        template = type_templates.get(priority)

        if not template:
            for p in ["critical", "high", "medium", "low"]:
                if p in type_templates:
                    template = type_templates[p]
                    break

        if not template:
            template = self.DEFAULT_TEMPLATE

        target = self._extract_target(text)
        return template.format(target=target, text=text[:15])

    def _extract_target(self, text: str) -> str:
        """Extract key target from suggestion text for template filling."""
        objective_keywords = {
            "男爵": "男爵", "baron": "男爵",
            "小龙": "小龙", "dragon": "小龙", "龙": "小龙",
            "远古龙": "远古龙", "elder": "远古龙",
            "峡谷先锋": "先锋", "herald": "先锋",
            "塔": "推塔", "tower": "推塔", "turret": "推塔",
            "镀层": "镀层",
        }
        text_lower = text.lower()
        for keyword, target in objective_keywords.items():
            if keyword in text_lower:
                return target
        return text[:8] if text else "目标"

    def get_template_count(self) -> int:
        return sum(len(v) for v in self.TEMPLATES.values())


# ─── Cooldown Manager ────────────────────────────────────────────────────────

class _CooldownManager:
    """Manages per-type cooldowns to prevent voice spam."""

    def __init__(self, global_cooldown: float = 30.0) -> None:
        self._global_cooldown = global_cooldown
        self._type_cooldowns: Dict[str, float] = {}
        self._last_global_narration: float = 0.0
        self._last_type_narration: Dict[str, float] = {}
        self._override_count = 0
        self._blocked_count = 0

    def can_narrate(self, suggestion_type: str, urgency: int,
                     current_time: float) -> Tuple[bool, str]:
        """Check if narration is allowed. Returns (allowed, reason)."""
        type_cd = self._type_cooldowns.get(suggestion_type, self._global_cooldown)

        if urgency == UrgencyLevel.IMMEDIATE:
            self._override_count += 1
            return True, "immediate_override"

        time_since_global = current_time - self._last_global_narration
        if time_since_global < self._global_cooldown:
            if urgency <= UrgencyLevel.HIGH:
                self._override_count += 1
                return True, "high_priority_override"
            self._blocked_count += 1
            return False, f"global_cooldown ({time_since_global:.1f}s < {self._global_cooldown}s)"

        last_type = self._last_type_narration.get(suggestion_type, 0.0)
        time_since_type = current_time - last_type
        if time_since_type < type_cd:
            self._blocked_count += 1
            return False, f"type_cooldown ({time_since_type:.1f}s < {type_cd}s)"

        return True, "allowed"

    def record_narration(self, suggestion_type: str, current_time: float) -> None:
        self._last_global_narration = current_time
        self._last_type_narration[suggestion_type] = current_time

    def set_type_cooldown(self, suggestion_type: str, cooldown: float) -> None:
        self._type_cooldowns[suggestion_type] = cooldown

    def get_stats(self) -> Dict[str, Any]:
        return {
            "global_cooldown": self._global_cooldown,
            "type_cooldowns": dict(self._type_cooldowns),
            "last_global": self._last_global_narration,
            "override_count": self._override_count,
            "blocked_count": self._blocked_count,
        }


# ─── Priority Queue ──────────────────────────────────────────────────────────

class _NarrationQueue:
    """Priority queue for pending voice narrations."""

    def __init__(self, max_size: int = 20) -> None:
        self._queue: List[Tuple[int, float, Dict]] = []
        self._max_size = max_size
        self._enqueue_count = 0
        self._dequeue_count = 0

    def enqueue(self, narration: Dict[str, Any], urgency: int,
                 timestamp: float) -> bool:
        self._enqueue_count += 1
        if len(self._queue) >= self._max_size:
            if urgency > self._queue[-1][0]:
                return False
            self._queue.pop()
        self._queue.append((urgency, timestamp, narration))
        self._queue.sort(key=lambda x: (x[0], x[1]))
        return True

    def dequeue(self) -> Optional[Dict[str, Any]]:
        if not self._queue:
            return None
        self._dequeue_count += 1
        _, _, narration = self._queue.pop(0)
        return narration

    def peek(self) -> Optional[Dict[str, Any]]:
        if not self._queue:
            return None
        return self._queue[0][2]

    def size(self) -> int:
        return len(self._queue)

    def clear(self) -> int:
        count = len(self._queue)
        self._queue.clear()
        return count

    def get_stats(self) -> Dict[str, Any]:
        return {
            "current_size": len(self._queue),
            "max_size": self._max_size,
            "enqueue_count": self._enqueue_count,
            "dequeue_count": self._dequeue_count,
        }


# ─── TTS Formatter ───────────────────────────────────────────────────────────

class _TTSFormatter:
    """Formats voice commands for TTS output with SSML markers."""

    def __init__(self) -> None:
        self._format_count = 0

    def format_plain(self, text: str) -> str:
        """Format as plain text (for basic TTS engines)."""
        self._format_count += 1
        text = text.strip()
        if not text.endswith(("！", "。", "？")):
            text += "。"
        return text

    def format_ssml(self, text: str, urgency: int) -> str:
        """Format as SSML for advanced TTS engines."""
        self._format_count += 1
        rate = "fast" if urgency <= UrgencyLevel.HIGH else "medium"
        volume = "loud" if urgency <= UrgencyLevel.IMMEDIATE else "medium"
        return (
            f'<speak>'
            f'<prosody rate="{rate}" volume="{volume}">'
            f'{text}'
            f'</prosody>'
            f'</speak>'
        )

    def estimate_duration_seconds(self, text: str) -> float:
        """Estimate spoken duration in seconds (Chinese: ~4 chars/second)."""
        char_count = len(text.replace(" ", "").replace("！", "").replace("。", ""))
        return max(1.0, char_count / 4.0)

    def get_stats(self) -> Dict[str, Any]:
        return {"format_count": self._format_count}


# ─── Narration History ───────────────────────────────────────────────────────

class _NarrationHistory:
    """Tracks narration history for analytics and feedback."""

    def __init__(self, max_records: int = 300) -> None:
        self._records: deque = deque(maxlen=max_records)
        self._type_counts: Dict[str, int] = defaultdict(int)
        self._urgency_counts: Dict[int, int] = defaultdict(int)

    def record(self, narration: Dict[str, Any]) -> None:
        self._records.append(narration)
        self._type_counts[narration.get("type", "unknown")] += 1
        self._urgency_counts[narration.get("urgency", 2)] += 1

    def get_recent(self, limit: int = 20) -> List[Dict]:
        return list(self._records)[-limit:]

    def get_stats(self) -> Dict[str, Any]:
        return {
            "total_narrations": len(self._records),
            "type_counts": dict(self._type_counts),
            "urgency_counts": dict(self._urgency_counts),
        }


class IngameVoiceNarrator:
    """Converts decision suggestions to short voice commands with cooldown control.

    Public API: narrate_suggestion, check_cooldown, format_for_tts,
                get_next_queued, flush_queue, get_narration_history, get_stats
    """

    def __init__(self, global_cooldown: float = 30.0,
                 max_chars: int = 15) -> None:
        self.evolution_callback: Optional[Callable] = None
        self._op_count = 0
        self._narration_count = 0
        self._max_chars = max_chars
        self._template_engine = _VoiceTemplateEngine()
        self._cooldown = _CooldownManager(global_cooldown=global_cooldown)
        self._queue = _NarrationQueue()
        self._tts = _TTSFormatter()
        self._history = _NarrationHistory()
        self._wall_clock_offset = time.monotonic()

    def _fire(self, et: str, data: Dict) -> None:
        if self.evolution_callback:
            self.evolution_callback({"type": et, "key": _EVOLUTION_KEY, **data})

    def _current_time(self) -> float:
        return time.monotonic()

    def narrate_suggestion(self, suggestion: Dict[str, Any]) -> Dict[str, Any]:
        """Convert a suggestion to a voice narration, respecting cooldowns."""
        self._op_count += 1
        stype = suggestion.get("type", "unknown")
        urgency = UrgencyLevel.classify(suggestion)
        now = self._current_time()

        voice_text = self._template_engine.generate(suggestion)
        if len(voice_text) > self._max_chars:
            voice_text = voice_text[:self._max_chars]

        allowed, reason = self._cooldown.can_narrate(stype, urgency, now)

        narration = {
            "type": stype,
            "voice_text": voice_text,
            "urgency": urgency,
            "allowed": allowed,
            "reason": reason,
            "timestamp": now,
            "duration_estimate": self._tts.estimate_duration_seconds(voice_text),
            "plain_tts": self._tts.format_plain(voice_text),
            "ssml_tts": self._tts.format_ssml(voice_text, urgency),
        }

        if allowed:
            self._narration_count += 1
            self._cooldown.record_narration(stype, now)
            self._history.record(narration)
            narration["narration_num"] = self._narration_count
            self._fire("narration_delivered", {
                "type": stype, "urgency": urgency,
            })
        else:
            self._queue.enqueue(narration, urgency, now)
            narration["queued"] = True

        return {"status": "ok", **narration}

    def check_cooldown(self) -> Dict[str, Any]:
        """Check current cooldown state."""
        self._op_count += 1
        return {
            "status": "ok",
            "cooldown_stats": self._cooldown.get_stats(),
            "queue_size": self._queue.size(),
        }

    def format_for_tts(self, text: str, urgency: int = UrgencyLevel.NORMAL) -> Dict[str, Any]:
        """Format arbitrary text for TTS output."""
        self._op_count += 1
        return {
            "status": "ok",
            "plain": self._tts.format_plain(text),
            "ssml": self._tts.format_ssml(text, urgency),
            "duration_estimate": self._tts.estimate_duration_seconds(text),
        }

    def get_next_queued(self) -> Dict[str, Any]:
        """Dequeue next pending narration."""
        self._op_count += 1
        narration = self._queue.dequeue()
        if narration:
            self._narration_count += 1
            self._cooldown.record_narration(
                narration.get("type", "unknown"), self._current_time())
            self._history.record(narration)
        return {
            "status": "ok",
            "narration": narration,
            "queue_remaining": self._queue.size(),
        }

    def flush_queue(self) -> Dict[str, Any]:
        """Clear all queued narrations."""
        self._op_count += 1
        cleared = self._queue.clear()
        return {"status": "ok", "cleared": cleared}

    def get_narration_history(self, limit: int = 20) -> Dict[str, Any]:
        """Get recent narration history."""
        self._op_count += 1
        return {
            "status": "ok",
            "history": self._history.get_recent(limit),
            "history_stats": self._history.get_stats(),
        }

    def set_cooldown(self, suggestion_type: str, seconds: float) -> Dict[str, Any]:
        """Set custom cooldown for a suggestion type."""
        self._op_count += 1
        self._cooldown.set_type_cooldown(suggestion_type, seconds)
        return {
            "status": "ok",
            "type": suggestion_type,
            "cooldown": seconds,
        }

    def get_stats(self) -> Dict[str, Any]:
        self._op_count += 1
        return {
            "status": "ok",
            "op_count": self._op_count,
            "narration_count": self._narration_count,
            "max_chars": self._max_chars,
            "template_count": self._template_engine.get_template_count(),
            "cooldown": self._cooldown.get_stats(),
            "queue": self._queue.get_stats(),
            "tts": self._tts.get_stats(),
            "history": self._history.get_stats(),
        }
