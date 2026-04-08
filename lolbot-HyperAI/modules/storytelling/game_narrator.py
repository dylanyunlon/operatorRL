"""
GameNarrator — Event-to-narrative generation engine.
======================================================

Converts structured game events from /lol/events into natural-language
commentary suitable for TTS output. Produces varied, context-aware
narration that avoids repetition and adapts tone to game state.

Architecture position:
    modules/storytelling/game_narrator.py   ← YOU ARE HERE
    ├─ Reads: /lol/events (GameEvent from perception)
    ├─ Reads: /lol/game_state (GameSnapshot for context)
    ├─ Publishes: /lol/narration (NarrationSegment)
    └─ Consumed by: modules/control/voice_output/voice_narrator.py

Apollo reference:
    modules/storytelling/storytelling_component.cc — narrative generation
"""

from __future__ import annotations

import hashlib
import logging
import random
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Deque, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

_NARRATION_CHANNEL: str = "/lol/narration"
_EVENT_CHANNEL: str = "/lol/events"
_STATE_CHANNEL: str = "/lol/game_state"
_COOLDOWN_SAME_TYPE_S: float = 10.0
_MAX_QUEUE_SIZE: int = 50
_RECENT_HASH_WINDOW: int = 30


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
    """A single narration unit ready for TTS."""
    text: str
    tone: NarrationTone = NarrationTone.NEUTRAL
    priority: NarrationPriority = NarrationPriority.MEDIUM
    timestamp: float = 0.0
    event_type: str = ""
    duration_hint_s: float = 0.0
    suppress_duplicate: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "text": self.text,
            "tone": self.tone.name,
            "priority": self.priority.value,
            "timestamp": self.timestamp,
            "event_type": self.event_type,
            "duration_hint_s": round(self.duration_hint_s, 1),
        }


# ─── Template collections ───────────────────────────────────────────────────

_KILL_TEMPLATES = [
    "{killer} takes down {victim}! {context}",
    "{killer} eliminates {victim}. {context}",
    "{victim} has been slain by {killer}. {context}",
    "And {killer} picks up the kill on {victim}! {context}",
    "{killer} with the takedown on {victim}. {context}",
]

_MULTI_KILL_TEMPLATES = [
    "{killer} is on a rampage with a {streak}!",
    "Incredible! {killer} gets a {streak}!",
    "{killer} unstoppable — {streak}!",
]

_OBJECTIVE_TEMPLATES = {
    "dragon": [
        "{team} secures the {dragon_type} dragon!",
        "{dragon_type} dragon goes to {team}.",
        "{team} takes {dragon_type} drake. {context}",
    ],
    "baron": [
        "{team} takes Baron Nashor! This could be the turning point.",
        "Baron secured by {team}! Power play incoming.",
        "{team} with the Baron take! Huge objective.",
    ],
    "herald": [
        "{team} picks up the Rift Herald.",
        "Herald goes to {team}. Tower pressure incoming.",
    ],
    "tower": [
        "{team} destroys a {lane} tower. Map control shifting.",
        "Tower down in {lane} for {team}.",
    ],
    "inhibitor": [
        "{team} takes the {lane} inhibitor! Super minions incoming.",
        "Inhibitor down in {lane}! {team} applying serious pressure.",
    ],
}

_TEAMFIGHT_TEMPLATES = [
    "Teamfight breaks out! {result}",
    "A massive teamfight erupts! {result}",
    "Both teams engage! {result}",
]

_WIN_PROB_TEMPLATES = {
    "ahead": [
        "Looking strong — win probability at {prob}%.",
        "In a commanding position at {prob}% win chance.",
    ],
    "behind": [
        "Tough spot — win probability down to {prob}%.",
        "Need a comeback — sitting at {prob}% win chance.",
    ],
    "even": [
        "Anyone's game — {prob}% win probability.",
        "Dead even at {prob}%.",
    ],
}

_GAME_PHASE_TEMPLATES = {
    "early": "Laning phase underway.",
    "mid": "Mid game — rotations and objectives matter now.",
    "late": "Late game — one fight could decide it all.",
}


class GameNarrator:
    """Stateful narrator that converts events into speech-ready text.

    Tracks recent narrations to avoid repetition, adapts tone based
    on game state, and prioritizes important events.

    Usage::

        narrator = GameNarrator()
        segments = narrator.narrate_event(event_dict, game_state_dict)
        for seg in segments:
            tts_queue.put(seg)
    """

    def __init__(self) -> None:
        self._recent_hashes: Deque[str] = deque(maxlen=_RECENT_HASH_WINDOW)
        self._last_event_times: Dict[str, float] = {}
        self._kill_count: int = 0
        self._narration_count: int = 0
        self._rng = random.Random(42)

    def narrate_event(
        self,
        event: Dict[str, Any],
        game_state: Optional[Dict[str, Any]] = None,
    ) -> List[NarrationSegment]:
        """Convert a game event into narration segments.

        Args:
            event: Event dict with at least 'type' field.
            game_state: Optional current game state for context.

        Returns:
            List of NarrationSegment (may be empty if suppressed).
        """
        event_type = event.get("type", "unknown")
        now = time.time()

        # Cooldown check
        last_time = self._last_event_times.get(event_type, 0)
        if now - last_time < _COOLDOWN_SAME_TYPE_S:
            if event_type not in ("multi_kill", "baron", "ace"):
                return []

        segments = []
        tone = self._determine_tone(game_state)

        if event_type == "champion_kill":
            segments = self._narrate_kill(event, game_state, tone)
        elif event_type == "multi_kill":
            segments = self._narrate_multi_kill(event, tone)
        elif event_type in ("dragon", "baron", "herald",
                            "tower", "inhibitor"):
            segments = self._narrate_objective(event, event_type, tone)
        elif event_type == "teamfight":
            segments = self._narrate_teamfight(event, tone)
        elif event_type == "win_probability_update":
            segments = self._narrate_win_prob(event, tone)
        elif event_type == "game_phase_change":
            segments = self._narrate_phase_change(event, tone)
        elif event_type == "ace":
            segments = self._narrate_ace(event, tone)
        else:
            return []

        # Dedup
        result = []
        for seg in segments:
            h = hashlib.md5(seg.text.encode()).hexdigest()[:8]
            if h not in self._recent_hashes:
                self._recent_hashes.append(h)
                result.append(seg)

        if result:
            self._last_event_times[event_type] = now
            self._narration_count += len(result)

        return result

    def _determine_tone(
        self, game_state: Optional[Dict[str, Any]]
    ) -> NarrationTone:
        if not game_state:
            return NarrationTone.NEUTRAL

        win_prob = game_state.get("win_probability", 0.5)
        gold_diff = game_state.get("gold_diff", 0)

        if win_prob > 0.7:
            return NarrationTone.CELEBRATORY
        elif win_prob < 0.3:
            return NarrationTone.WARNING
        elif abs(gold_diff) < 1000:
            return NarrationTone.TENSE
        elif gold_diff > 3000:
            return NarrationTone.EXCITED
        return NarrationTone.NEUTRAL

    def _narrate_kill(
        self,
        event: Dict[str, Any],
        game_state: Optional[Dict[str, Any]],
        tone: NarrationTone,
    ) -> List[NarrationSegment]:
        killer = event.get("killer", "Someone")
        victim = event.get("victim", "an enemy")
        context = ""

        if game_state:
            gd = game_state.get("gold_diff", 0)
            if abs(gd) > 3000:
                context = f"Gold lead now {abs(gd):,}."

        template = self._rng.choice(_KILL_TEMPLATES)
        text = template.format(
            killer=killer, victim=victim, context=context
        ).strip()

        self._kill_count += 1
        return [NarrationSegment(
            text=text, tone=tone,
            priority=NarrationPriority.MEDIUM,
            timestamp=time.time(), event_type="champion_kill",
            duration_hint_s=3.0,
        )]

    def _narrate_multi_kill(
        self, event: Dict[str, Any], tone: NarrationTone
    ) -> List[NarrationSegment]:
        killer = event.get("killer", "Someone")
        count = event.get("kill_count", 2)
        streak_names = {2: "Double Kill", 3: "Triple Kill",
                        4: "Quadra Kill", 5: "Penta Kill"}
        streak = streak_names.get(count, f"{count}-kill streak")

        template = self._rng.choice(_MULTI_KILL_TEMPLATES)
        text = template.format(killer=killer, streak=streak)

        priority = (NarrationPriority.CRITICAL if count >= 4
                     else NarrationPriority.HIGH)
        return [NarrationSegment(
            text=text, tone=NarrationTone.EXCITED,
            priority=priority, timestamp=time.time(),
            event_type="multi_kill", duration_hint_s=3.5,
        )]

    def _narrate_objective(
        self,
        event: Dict[str, Any],
        obj_type: str,
        tone: NarrationTone,
    ) -> List[NarrationSegment]:
        team = event.get("team", "A team")
        templates = _OBJECTIVE_TEMPLATES.get(obj_type, ["{team} takes an objective."])
        template = self._rng.choice(templates)

        kwargs = {"team": team, "context": ""}
        if obj_type == "dragon":
            kwargs["dragon_type"] = event.get("dragon_type", "elemental")
        if obj_type in ("tower", "inhibitor"):
            kwargs["lane"] = event.get("lane", "unknown")

        text = template.format(**kwargs).strip()
        priority = (NarrationPriority.HIGH if obj_type in ("baron", "inhibitor")
                     else NarrationPriority.MEDIUM)

        return [NarrationSegment(
            text=text, tone=tone, priority=priority,
            timestamp=time.time(), event_type=obj_type,
            duration_hint_s=4.0,
        )]

    def _narrate_teamfight(
        self, event: Dict[str, Any], tone: NarrationTone
    ) -> List[NarrationSegment]:
        winners = event.get("winning_team", "unknown")
        kills = event.get("total_kills", 0)
        result = f"{winners} wins the fight with {kills} kills."

        template = self._rng.choice(_TEAMFIGHT_TEMPLATES)
        text = template.format(result=result)

        return [NarrationSegment(
            text=text, tone=NarrationTone.EXCITED,
            priority=NarrationPriority.HIGH,
            timestamp=time.time(), event_type="teamfight",
            duration_hint_s=4.0,
        )]

    def _narrate_win_prob(
        self, event: Dict[str, Any], tone: NarrationTone
    ) -> List[NarrationSegment]:
        prob = event.get("probability", 0.5)
        prob_pct = round(prob * 100)

        if prob_pct > 60:
            templates = _WIN_PROB_TEMPLATES["ahead"]
        elif prob_pct < 40:
            templates = _WIN_PROB_TEMPLATES["behind"]
        else:
            templates = _WIN_PROB_TEMPLATES["even"]

        template = self._rng.choice(templates)
        text = template.format(prob=prob_pct)

        return [NarrationSegment(
            text=text, tone=tone,
            priority=NarrationPriority.LOW,
            timestamp=time.time(), event_type="win_probability",
            duration_hint_s=3.0,
        )]

    def _narrate_phase_change(
        self, event: Dict[str, Any], tone: NarrationTone
    ) -> List[NarrationSegment]:
        phase = event.get("phase", "")
        text = _GAME_PHASE_TEMPLATES.get(phase, "")
        if not text:
            return []
        return [NarrationSegment(
            text=text, tone=NarrationTone.NEUTRAL,
            priority=NarrationPriority.LOW,
            timestamp=time.time(), event_type="phase_change",
            duration_hint_s=2.5,
        )]

    def _narrate_ace(
        self, event: Dict[str, Any], tone: NarrationTone
    ) -> List[NarrationSegment]:
        team = event.get("team", "A team")
        text = f"ACE! {team} wipes the enemy team!"
        return [NarrationSegment(
            text=text, tone=NarrationTone.EXCITED,
            priority=NarrationPriority.CRITICAL,
            timestamp=time.time(), event_type="ace",
            duration_hint_s=3.0,
        )]

    # ─── Introspection ───────────────────────────────────────────────────

    def stats(self) -> Dict[str, Any]:
        return {
            "narration_count": self._narration_count,
            "kill_count": self._kill_count,
            "recent_hash_count": len(self._recent_hashes),
            "cooldown_types": len(self._last_event_times),
        }

    def reset(self) -> None:
        self._recent_hashes.clear()
        self._last_event_times.clear()
        self._kill_count = 0
        self._narration_count = 0


# ═══════════════════════════════════════════════════════════════════════════════
# Claude22 V3: Momentum-aware narrative engine + bilingual + chain composition
# ═══════════════════════════════════════════════════════════════════════════════
#
# Design spec (Apollo pattern):
#   从 GameNarrator 的事件→叙事转换管道 这个好例子开始。
#   然后，遵循该模式实现 MomentumNarrator，让 叙事系统 可以 根据比赛势头
#   自动调整语气和内容密度，并能 在团战/关键事件时切换到高能模式。
#   接着 在 NarrationPipeline 引入 多阶段处理管道，使 事件 能够 经过
#   过滤→丰富→格式化→去重 四个阶段，同时 NarrationContext 优化 上下文注入。

from modules.storytelling.commentary_template import (
    BilingualLibrary,
    Language,
    MomentumAwareSelector,
    TemplateCategory,
    TemplateChain,
    create_default_bilingual_library,
)


# ─── Narration context — enriched game state for template rendering ──────────

@dataclass
class NarrationContext:
    """Enriched context for template rendering.

    Aggregates data from multiple channels into a single variable dict
    suitable for template rendering. Updated every Proc() cycle.

    Apollo parallel: planning uses fused perception + prediction context.
    """
    game_time_s: float = 0.0
    game_phase: str = "early_game"
    momentum_score: float = 0.0
    win_probability: float = 0.5
    gold_diff: int = 0
    kill_diff: int = 0
    dragon_count: int = 0
    baron_active: bool = False
    recent_kill_streak: int = 0
    active_player_champion: str = ""
    active_player_team: str = ""

    def to_template_vars(self) -> Dict[str, Any]:
        """Convert to template variable dict."""
        return {
            "game_time": f"{int(self.game_time_s // 60)}:{int(self.game_time_s % 60):02d}",
            "phase": self.game_phase,
            "prob": round(self.win_probability * 100),
            "gold_diff": self.gold_diff,
            "kill_diff": self.kill_diff,
            "momentum": "positive" if self.momentum_score > 0.1 else (
                "negative" if self.momentum_score < -0.1 else "neutral"),
            "champion": self.active_player_champion,
            "team": self.active_player_team,
        }


# ─── Narration pipeline stage ───────────────────────────────────────────────

class NarrationStage:
    """Base class for narration pipeline stages."""

    def process(
        self,
        segment: NarrationSegment,
        context: NarrationContext,
    ) -> Optional[NarrationSegment]:
        """Process a narration segment. Return None to drop it."""
        return segment


class RelevanceFilter(NarrationStage):
    """Drop narrations that are not relevant to the current game state.

    Examples:
    - Drop "consider warding" if game just started (< 90s)
    - Drop item suggestions if the player just died
    """

    def process(
        self,
        segment: NarrationSegment,
        context: NarrationContext,
    ) -> Optional[NarrationSegment]:
        # Very early game: suppress strategy narrations
        if context.game_time_s < 90.0 and segment.event_type == "strategy":
            return None
        return segment


class PriorityBooster(NarrationStage):
    """Boost priority for narrations during key moments.

    During teamfights, baron, or close games, boost priority to ensure
    important narrations get through the rate limiter.
    """

    def process(
        self,
        segment: NarrationSegment,
        context: NarrationContext,
    ) -> Optional[NarrationSegment]:
        boosted = False

        # Boost during baron
        if context.baron_active and segment.event_type == "objective":
            segment = NarrationSegment(
                text=segment.text,
                tone=segment.tone,
                priority=NarrationPriority.CRITICAL,
                timestamp=segment.timestamp,
                event_type=segment.event_type,
                duration_hint_s=segment.duration_hint_s,
            )
            boosted = True

        # Boost during close games
        if 0.4 < context.win_probability < 0.6 and not boosted:
            if segment.priority == NarrationPriority.MEDIUM:
                segment = NarrationSegment(
                    text=segment.text,
                    tone=segment.tone,
                    priority=NarrationPriority.HIGH,
                    timestamp=segment.timestamp,
                    event_type=segment.event_type,
                    duration_hint_s=segment.duration_hint_s,
                )

        return segment


class ToneAdapter(NarrationStage):
    """Adapt narration tone based on momentum.

    Uses MomentumAwareSelector to override tone based on game state.
    """

    def __init__(self) -> None:
        self._selector = MomentumAwareSelector()

    def process(
        self,
        segment: NarrationSegment,
        context: NarrationContext,
    ) -> Optional[NarrationSegment]:
        suggested = self._selector.suggest_tone(
            momentum_score=context.momentum_score,
            win_probability=context.win_probability,
            game_phase=context.game_phase,
        )

        # Map MomentumAwareSelector tone to NarrationTone
        tone_map = {
            "NEUTRAL": NarrationTone.NEUTRAL,
            "HYPE": NarrationTone.EXCITED,
            "TENSE": NarrationTone.TENSE,
            "CALM": NarrationTone.ENCOURAGING,
            "URGENT": NarrationTone.WARNING,
        }
        new_tone = tone_map.get(suggested.name, segment.tone)

        return NarrationSegment(
            text=segment.text,
            tone=new_tone,
            priority=segment.priority,
            timestamp=segment.timestamp,
            event_type=segment.event_type,
            duration_hint_s=segment.duration_hint_s,
        )


# ─── Narration pipeline ─────────────────────────────────────────────────────

class NarrationPipeline:
    """Multi-stage narration processing pipeline.

    Events flow through: Filter → Enrich → Tone-adapt → Boost → Output

    Apollo parallel: perception pipeline (lidar → fusion → tracking).

    Usage::
        pipeline = NarrationPipeline()
        pipeline.add_stage(RelevanceFilter())
        pipeline.add_stage(ToneAdapter())
        pipeline.add_stage(PriorityBooster())

        result = pipeline.process(segment, context)
        if result is not None:
            # publish to /lol/narration
    """

    def __init__(self) -> None:
        self._stages: List[NarrationStage] = []
        self._processed_count: int = 0
        self._dropped_count: int = 0

    def add_stage(self, stage: NarrationStage) -> None:
        self._stages.append(stage)

    def process(
        self,
        segment: NarrationSegment,
        context: NarrationContext,
    ) -> Optional[NarrationSegment]:
        """Process a segment through all pipeline stages."""
        self._processed_count += 1
        current = segment
        for stage in self._stages:
            current = stage.process(current, context)
            if current is None:
                self._dropped_count += 1
                return None
        return current

    def stats(self) -> Dict[str, Any]:
        return {
            "stages": len(self._stages),
            "processed": self._processed_count,
            "dropped": self._dropped_count,
            "pass_rate": round(
                (self._processed_count - self._dropped_count)
                / max(1, self._processed_count), 3
            ),
        }


# ─── MomentumNarrator — V3 game narrator with momentum awareness ────────────

class MomentumNarrator:
    """V3 narrator with momentum-aware tone, bilingual support, and pipeline.

    Wraps the existing GameNarrator event processing with:
    1. NarrationPipeline for multi-stage processing
    2. BilingualLibrary for EN/ZH output
    3. MomentumAwareSelector for dynamic tone
    4. TemplateChain for multi-sentence composition

    Not a subclass of GameNarrator — composes with it.
    GameNarrator handles event→segment conversion (unchanged).
    MomentumNarrator handles segment post-processing (new).

    Usage::
        narrator = MomentumNarrator(language=Language.ZH)
        narrator.set_context(NarrationContext(
            momentum_score=0.5, win_probability=0.65,
        ))

        # Process segments from GameNarrator
        enhanced = narrator.enhance(raw_segment)
        if enhanced:
            publish(enhanced)
    """

    def __init__(self, language: Language = Language.EN) -> None:
        self._library = create_default_bilingual_library(language)
        self._selector = MomentumAwareSelector()
        self._pipeline = NarrationPipeline()
        self._chain = TemplateChain(self._library.base)
        self._context = NarrationContext()
        self._enhanced_count: int = 0

        # Build default pipeline
        self._pipeline.add_stage(RelevanceFilter())
        self._pipeline.add_stage(ToneAdapter())
        self._pipeline.add_stage(PriorityBooster())

    def set_context(self, ctx: NarrationContext) -> None:
        """Update the current game context."""
        self._context = ctx

    def set_language(self, lang: Language) -> None:
        self._library.set_language(lang)

    def enhance(self, segment: NarrationSegment) -> Optional[NarrationSegment]:
        """Enhance a raw narration segment through the pipeline.

        This is called after GameNarrator produces a segment.
        Applies momentum-aware tone, priority boosting, and filtering.
        """
        result = self._pipeline.process(segment, self._context)
        if result is not None:
            self._enhanced_count += 1
        return result

    def compose_commentary(
        self,
        event_type: str,
        variables: Dict[str, Any],
    ) -> Optional[str]:
        """Generate momentum-aware bilingual commentary for an event.

        Maps event_type to TemplateCategory and generates appropriate text.
        """
        cat_map = {
            "kill": TemplateCategory.KILL,
            "objective": TemplateCategory.OBJECTIVE,
            "teamfight": TemplateCategory.TEAMFIGHT,
            "win_prob": TemplateCategory.WIN_PROB,
            "strategy": TemplateCategory.STRATEGY,
            "item": TemplateCategory.ITEM,
        }
        category = cat_map.get(event_type, TemplateCategory.GENERIC)

        # Merge context variables with event variables
        merged = {**self._context.to_template_vars(), **variables}

        return self._selector.select_with_momentum(
            library=self._library.base,
            category=category,
            variables=merged,
            momentum_score=self._context.momentum_score,
            win_probability=self._context.win_probability,
            game_phase=self._context.game_phase,
            game_time_s=int(self._context.game_time_s),
        )

    def stats(self) -> Dict[str, Any]:
        return {
            "enhanced_count": self._enhanced_count,
            "library": self._library.stats(),
            "pipeline": self._pipeline.stats(),
            "language": self._library.language.value,
        }
