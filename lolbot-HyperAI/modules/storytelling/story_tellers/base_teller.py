"""
BaseTeller — Abstract interface for story tellers.
=====================================================
lolbot-HyperAI · modules/storytelling/story_tellers

查看 Apollo ``modules/storytelling/story_tellers/base_teller.h`` 上现有
``BaseTeller`` 的实现方式, 理解其模式, 特别是 ``Init()`` + ``Process()``
纯虚接口和 ``Stories`` 输出收集的设计。从 Apollo BaseTeller 这个好例子
开始。然后, 遵循该模式实现一个新的 ``BaseTeller`` 抽象类, 让所有 teller
可以通过统一接口接收事件和游戏状态, 并能输出 NarrationSegment。接着
引入 cooldown 管理和 duplicate 检测, 使 teller 能够避免重复播报, 同时
优化模板随机选择以产生自然变化。

位置: lolbot-HyperAI/modules/storytelling/story_tellers/base_teller.py

Apollo reference:
    modules/storytelling/story_tellers/base_teller.h — pure virtual
"""

from __future__ import annotations

import abc
import hashlib
import logging
import random
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Deque, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


# ── Shared types (imported from existing game_narrator.py) ───────────────────
# We import from the existing module to preserve Claude1-27 code

class NarrationTone(Enum):
    """Tone adapts based on game state."""
    NEUTRAL = auto()
    EXCITED = auto()
    TENSE = auto()
    ENCOURAGING = auto()
    WARNING = auto()
    CELEBRATORY = auto()


class NarrationPriority(Enum):
    """Priority determines queue ordering and TTS urgency."""
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4


@dataclass
class NarrationSegment:
    """A single narration unit ready for TTS.

    This is the output type for all story tellers.
    """
    text: str
    tone: NarrationTone = NarrationTone.NEUTRAL
    priority: NarrationPriority = NarrationPriority.MEDIUM
    timestamp: float = 0.0
    event_type: str = ""
    duration_hint_s: float = 0.0
    suppress_duplicate: bool = True
    teller_name: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "text": self.text,
            "tone": self.tone.name,
            "priority": self.priority.value,
            "timestamp": self.timestamp,
            "event_type": self.event_type,
            "duration_hint_s": round(self.duration_hint_s, 1),
            "teller_name": self.teller_name,
        }


@dataclass
class GameContext:
    """Game state context passed to tellers each frame.

    Provides everything a teller needs to decide tone and relevance.
    """
    game_time: float = 0.0
    gold_diff: float = 0.0
    win_probability: float = 0.5
    ally_kills: int = 0
    enemy_kills: int = 0
    ally_turrets: int = 0
    enemy_turrets: int = 0
    player_champion: str = ""
    game_phase: str = ""  # early/mid/late
    is_behind: bool = False


@dataclass
class GameEvent:
    """A single game event to be narrated.

    Produced by perception's event detector, consumed by tellers.
    """
    event_type: str = ""
    event_data: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = 0.0
    priority: int = 2  # 1=low, 4=critical
    event_id: str = ""

    def content_hash(self) -> str:
        """Hash for duplicate detection."""
        content = f"{self.event_type}:{sorted(self.event_data.items())}"
        return hashlib.md5(content.encode()).hexdigest()[:12]


class BaseTeller(abc.ABC):
    """Abstract base class for all story tellers.

    Apollo equivalent: ``BaseTeller`` in base_teller.h.

    Each concrete teller handles one category of game events
    (teamfights, objectives, deaths, items, vision). The teller:
    1. Receives events + game context via ``process()``
    2. Decides if an event is narration-worthy
    3. Selects a template and fills it with event data
    4. Returns NarrationSegment(s) or empty list

    Subclasses MUST implement:
        - name() -> str
        - handled_event_types() -> Set[str]
        - _generate(event, context) -> Optional[NarrationSegment]

    The base class provides:
        - Cooldown management (per event type)
        - Duplicate detection (recent hash window)
        - Template randomization helpers
        - Tone inference from game context
    """

    def __init__(
        self,
        cooldown_s: float = 10.0,
        recent_window: int = 30,
    ) -> None:
        self._cooldown_s = cooldown_s
        self._recent_hashes: Deque[str] = deque(maxlen=recent_window)
        self._last_narration_time: Dict[str, float] = {}
        self._narration_count: int = 0
        self._suppressed_count: int = 0

    @abc.abstractmethod
    def name(self) -> str:
        """Teller identifier (e.g., 'teamfight', 'objective')."""
        ...

    @abc.abstractmethod
    def handled_event_types(self) -> Set[str]:
        """Set of event_type strings this teller handles."""
        ...

    @abc.abstractmethod
    def _generate(
        self,
        event: GameEvent,
        context: GameContext,
    ) -> Optional[NarrationSegment]:
        """Core narration generation — implement in subclass.

        Returns a NarrationSegment if the event should be narrated,
        or None to suppress.
        """
        ...

    def process(
        self,
        events: List[GameEvent],
        context: GameContext,
    ) -> List[NarrationSegment]:
        """Process a batch of events and return narrations.

        Apollo equivalent: ``BaseTeller::Process(frame)``

        Handles cooldown, dedup, and delegates to ``_generate()``.
        """
        handled = self.handled_event_types()
        results: List[NarrationSegment] = []

        for event in events:
            if event.event_type not in handled:
                continue

            # Cooldown check
            if self._is_on_cooldown(event.event_type):
                self._suppressed_count += 1
                continue

            # Duplicate check
            content_hash = event.content_hash()
            if content_hash in self._recent_hashes:
                self._suppressed_count += 1
                continue

            # Generate narration
            segment = self._generate(event, context)
            if segment is not None:
                segment.teller_name = self.name()
                segment.timestamp = time.time()
                results.append(segment)
                self._recent_hashes.append(content_hash)
                self._last_narration_time[event.event_type] = time.time()
                self._narration_count += 1

        return results

    def _is_on_cooldown(self, event_type: str) -> bool:
        """Check if this event type is on cooldown."""
        last_time = self._last_narration_time.get(event_type, 0.0)
        return (time.time() - last_time) < self._cooldown_s

    def infer_tone(self, context: GameContext) -> NarrationTone:
        """Infer narration tone from game context."""
        if context.win_probability > 0.75:
            return NarrationTone.CELEBRATORY
        if context.win_probability < 0.35:
            return NarrationTone.WARNING
        if abs(context.gold_diff) > 3000:
            return NarrationTone.TENSE
        if context.is_behind:
            return NarrationTone.ENCOURAGING
        return NarrationTone.NEUTRAL

    @staticmethod
    def pick_template(templates: List[str]) -> str:
        """Randomly select a template string for variety."""
        return random.choice(templates) if templates else ""

    def stats(self) -> Dict[str, Any]:
        """Teller statistics for monitoring."""
        return {
            "name": self.name(),
            "handled_types": sorted(self.handled_event_types()),
            "narration_count": self._narration_count,
            "suppressed_count": self._suppressed_count,
            "cooldown_s": self._cooldown_s,
            "active_cooldowns": {
                etype: round(time.time() - t, 1)
                for etype, t in self._last_narration_time.items()
                if (time.time() - t) < self._cooldown_s
            },
        }
