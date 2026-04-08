"""
CommentaryTemplate — Template-based commentary engine with variety.
====================================================================

Manages commentary template libraries organized by event category,
game phase, and emotional tone. Provides weighted random selection
with recency-aware suppression to ensure variety.

Architecture position:
    modules/storytelling/commentary_template.py   ← YOU ARE HERE
    ├─ Used by: modules/storytelling/game_narrator.py
    └─ Loaded from: data/templates/ (optional external YAML)
"""

from __future__ import annotations

import hashlib
import logging
import random
import re
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Deque, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

_SUPPRESS_WINDOW: int = 20
_TEMPLATE_VAR_RE = re.compile(r"\{(\w+)\}")


class TemplateCategory(Enum):
    KILL = "kill"
    OBJECTIVE = "objective"
    TEAMFIGHT = "teamfight"
    WIN_PROB = "win_prob"
    PHASE = "phase"
    ITEM = "item"
    STRATEGY = "strategy"
    GENERIC = "generic"


class EmotionalTone(Enum):
    NEUTRAL = auto()
    HYPE = auto()
    TENSE = auto()
    CALM = auto()
    URGENT = auto()


@dataclass
class Template:
    """Single commentary template with metadata."""
    text: str
    category: TemplateCategory
    tone: EmotionalTone = EmotionalTone.NEUTRAL
    weight: float = 1.0
    min_game_time_s: int = 0
    max_game_time_s: int = 99999
    required_vars: Set[str] = field(default_factory=set)

    def __post_init__(self):
        self.required_vars = set(_TEMPLATE_VAR_RE.findall(self.text))

    @property
    def template_hash(self) -> str:
        return hashlib.md5(self.text.encode()).hexdigest()[:8]


class TemplateLibrary:
    """Manages a collection of templates with weighted random selection.

    Features:
        - Category and tone filtering
        - Weighted random selection
        - Recency suppression (avoid repeating same template)
        - Variable validation before rendering
        - Game-time-aware filtering
    """

    def __init__(self, suppress_window: int = _SUPPRESS_WINDOW) -> None:
        self._templates: Dict[TemplateCategory, List[Template]] = defaultdict(list)
        self._recent_hashes: Deque[str] = deque(maxlen=suppress_window)
        self._rng = random.Random(int(time.time()))
        self._total_count: int = 0
        self._selection_count: int = 0

    def add(self, template: Template) -> None:
        self._templates[template.category].append(template)
        self._total_count += 1

    def add_text(
        self,
        text: str,
        category: TemplateCategory,
        tone: EmotionalTone = EmotionalTone.NEUTRAL,
        weight: float = 1.0,
    ) -> None:
        self.add(Template(text=text, category=category,
                          tone=tone, weight=weight))

    def add_batch(
        self,
        texts: List[str],
        category: TemplateCategory,
        tone: EmotionalTone = EmotionalTone.NEUTRAL,
    ) -> int:
        count = 0
        for text in texts:
            self.add_text(text, category, tone)
            count += 1
        return count

    def select(
        self,
        category: TemplateCategory,
        tone: Optional[EmotionalTone] = None,
        game_time_s: int = 0,
        available_vars: Optional[Set[str]] = None,
    ) -> Optional[Template]:
        """Select a template with weighted random, suppressing recent picks.

        Args:
            category: Template category to filter.
            tone: Optional emotional tone filter.
            game_time_s: Current game time for time-based filtering.
            available_vars: Variables that will be provided for rendering.

        Returns:
            Selected Template or None if no matching templates.
        """
        candidates = self._templates.get(category, [])
        if not candidates:
            return None

        filtered = []
        for t in candidates:
            if tone and t.tone != tone and t.tone != EmotionalTone.NEUTRAL:
                continue
            if game_time_s < t.min_game_time_s:
                continue
            if game_time_s > t.max_game_time_s:
                continue
            if available_vars and not t.required_vars.issubset(available_vars):
                continue
            if t.template_hash in self._recent_hashes:
                continue
            filtered.append(t)

        if not filtered:
            filtered = [t for t in candidates
                        if not available_vars
                        or t.required_vars.issubset(available_vars)]
            if not filtered:
                return None

        weights = [t.weight for t in filtered]
        total = sum(weights)
        if total <= 0:
            return None

        chosen = self._rng.choices(filtered, weights=weights, k=1)[0]
        self._recent_hashes.append(chosen.template_hash)
        self._selection_count += 1
        return chosen

    def render(
        self,
        template: Template,
        variables: Dict[str, Any],
    ) -> str:
        """Render a template with variable substitution.

        Missing variables are replaced with empty string.
        """
        text = template.text
        for var_name in template.required_vars:
            value = variables.get(var_name, "")
            text = text.replace("{" + var_name + "}", str(value))
        return text.strip()

    def select_and_render(
        self,
        category: TemplateCategory,
        variables: Dict[str, Any],
        tone: Optional[EmotionalTone] = None,
        game_time_s: int = 0,
    ) -> Optional[str]:
        """Select a template and render it in one call."""
        template = self.select(
            category, tone, game_time_s,
            available_vars=set(variables.keys()),
        )
        if template is None:
            return None
        return self.render(template, variables)

    def count(self, category: Optional[TemplateCategory] = None) -> int:
        if category:
            return len(self._templates.get(category, []))
        return self._total_count

    def categories(self) -> List[str]:
        return [c.value for c in self._templates.keys()]

    def stats(self) -> Dict[str, Any]:
        return {
            "total_templates": self._total_count,
            "categories": len(self._templates),
            "selection_count": self._selection_count,
            "suppress_window": len(self._recent_hashes),
        }

    def reset_suppression(self) -> None:
        self._recent_hashes.clear()


def create_default_library() -> TemplateLibrary:
    """Create a library pre-loaded with default commentary templates."""
    lib = TemplateLibrary()

    lib.add_batch([
        "{killer} takes down {victim}! {context}",
        "{killer} eliminates {victim}. {context}",
        "{victim} falls to {killer}. {context}",
        "And {killer} gets the kill on {victim}!",
        "{killer} with a clean takedown on {victim}.",
        "That's a kill for {killer} onto {victim}. {context}",
        "{victim} caught out and {killer} capitalizes!",
    ], TemplateCategory.KILL, EmotionalTone.NEUTRAL)

    lib.add_batch([
        "{team} secures {objective}! {context}",
        "{objective} goes to {team}. {context}",
        "Big objective — {team} takes {objective}.",
        "{team} with the {objective} secure! {context}",
    ], TemplateCategory.OBJECTIVE, EmotionalTone.HYPE)

    lib.add_batch([
        "Teamfight breaks out near {location}! {result}",
        "Massive engage! {result}",
        "Both teams collide! {result}",
        "A {size} teamfight erupts! {result}",
    ], TemplateCategory.TEAMFIGHT, EmotionalTone.HYPE)

    lib.add_batch([
        "Win probability now at {prob}%.",
        "Sitting at {prob}% win chance. {context}",
        "{prob}% — {context}",
    ], TemplateCategory.WIN_PROB, EmotionalTone.CALM)

    lib.add_batch([
        "Consider building {item} here — {reason}.",
        "{item} would be strong right now. {reason}",
        "Good time for {item}. {reason}",
    ], TemplateCategory.ITEM, EmotionalTone.CALM)

    lib.add_batch([
        "{action} is the play right now. {reason}",
        "Recommend {action}. {reason}",
        "The smart move is {action}. {reason}",
    ], TemplateCategory.STRATEGY, EmotionalTone.NEUTRAL)

    return lib


# ═══════════════════════════════════════════════════════════════════════════════
# Claude22 V3: Bilingual (EN/ZH) template library + momentum-aware selection
# ═══════════════════════════════════════════════════════════════════════════════
#
# Design spec (Apollo pattern):
#   从 TemplateLibrary 的加权随机选择+抑制窗口 这个好例子开始。
#   然后，遵循该模式实现 BilingualTemplate，让 解说系统 可以 同时输出中英文解说，
#   并能 根据用户语言偏好自动选择。
#   接着 MomentumAwareSelector 引入 动量感知选择逻辑，使 解说语气 能够
#   随比赛势头自动调整（顺风鼓励/逆风紧张），同时 TemplateChain 优化 多句组合。


class Language(Enum):
    """Supported commentary languages."""
    EN = "en"
    ZH = "zh"


@dataclass
class BilingualTemplate:
    """A template with both English and Chinese text.

    Used by BilingualLibrary for automatic language switching.
    Falls back to English if Chinese text is not provided.
    """
    en_text: str
    zh_text: str = ""
    category: TemplateCategory = TemplateCategory.GENERIC
    tone: EmotionalTone = EmotionalTone.NEUTRAL
    weight: float = 1.0
    min_game_time_s: int = 0
    max_game_time_s: int = 99999
    required_vars: Set[str] = field(default_factory=set)

    def __post_init__(self):
        # Extract vars from both languages
        en_vars = set(_TEMPLATE_VAR_RE.findall(self.en_text))
        zh_vars = set(_TEMPLATE_VAR_RE.findall(self.zh_text)) if self.zh_text else set()
        self.required_vars = en_vars | zh_vars
        if not self.zh_text:
            self.zh_text = self.en_text  # fallback

    def get_text(self, lang: Language = Language.EN) -> str:
        if lang == Language.ZH:
            return self.zh_text
        return self.en_text

    @property
    def template_hash(self) -> str:
        return hashlib.md5(self.en_text.encode()).hexdigest()[:8]


class BilingualLibrary:
    """Template library supporting English and Chinese commentary.

    Wraps TemplateLibrary with bilingual awareness.
    All V1 TemplateLibrary methods are accessible via .base property.

    Usage::
        lib = BilingualLibrary(language=Language.ZH)
        lib.add_bilingual(BilingualTemplate(
            en_text="{killer} takes down {victim}!",
            zh_text="{killer} 击杀了 {victim}！",
            category=TemplateCategory.KILL,
        ))

        text = lib.select_and_render(
            TemplateCategory.KILL,
            {"killer": "Yasuo", "victim": "Teemo"},
        )
        # → "Yasuo 击杀了 Teemo！"
    """

    def __init__(
        self,
        language: Language = Language.EN,
        suppress_window: int = _SUPPRESS_WINDOW,
    ) -> None:
        self._language = language
        self._base = TemplateLibrary(suppress_window=suppress_window)
        self._bilingual_map: Dict[str, BilingualTemplate] = {}

    @property
    def base(self) -> TemplateLibrary:
        return self._base

    @property
    def language(self) -> Language:
        return self._language

    def set_language(self, lang: Language) -> None:
        self._language = lang

    def add_bilingual(self, bt: BilingualTemplate) -> None:
        """Add a bilingual template."""
        # Store mapping from hash to bilingual template
        text = bt.get_text(self._language)
        t = Template(
            text=text,
            category=bt.category,
            tone=bt.tone,
            weight=bt.weight,
            min_game_time_s=bt.min_game_time_s,
            max_game_time_s=bt.max_game_time_s,
        )
        self._bilingual_map[t.template_hash] = bt
        self._base.add(t)

    def add_bilingual_batch(
        self,
        en_texts: List[str],
        zh_texts: List[str],
        category: TemplateCategory,
        tone: EmotionalTone = EmotionalTone.NEUTRAL,
    ) -> int:
        """Add a batch of bilingual templates."""
        count = 0
        for i, en_text in enumerate(en_texts):
            zh_text = zh_texts[i] if i < len(zh_texts) else ""
            self.add_bilingual(BilingualTemplate(
                en_text=en_text,
                zh_text=zh_text,
                category=category,
                tone=tone,
            ))
            count += 1
        return count

    def select_and_render(
        self,
        category: TemplateCategory,
        variables: Dict[str, Any],
        tone: Optional[EmotionalTone] = None,
        game_time_s: int = 0,
    ) -> Optional[str]:
        """Select and render in the current language."""
        template = self._base.select(
            category, tone, game_time_s,
            available_vars=set(variables.keys()),
        )
        if template is None:
            return None

        # Try to get bilingual version
        bt = self._bilingual_map.get(template.template_hash)
        if bt and self._language == Language.ZH:
            text = bt.zh_text
            for var_name in bt.required_vars:
                value = variables.get(var_name, "")
                text = text.replace("{" + var_name + "}", str(value))
            return text.strip()

        return self._base.render(template, variables)

    def stats(self) -> Dict[str, Any]:
        base_stats = self._base.stats()
        base_stats["language"] = self._language.value
        base_stats["bilingual_count"] = len(self._bilingual_map)
        return base_stats


# ─── Momentum-aware template selector ───────────────────────────────────────

class MomentumAwareSelector:
    """Selects commentary tone based on game momentum.

    When the user's team has momentum (getting kills, taking objectives),
    the commentary becomes encouraging/hyped. When losing momentum,
    it becomes more analytical/cautious.

    Apollo parallel: planning adapts strategy based on environment state.

    Usage::
        selector = MomentumAwareSelector()
        tone = selector.suggest_tone(
            momentum_score=0.7,     # positive momentum
            win_probability=0.65,
            game_phase="mid_game",
        )
    """

    # Momentum score thresholds for tone mapping
    _TONE_MAP = [
        (-1.0, -0.5, EmotionalTone.URGENT),    # heavy losing momentum
        (-0.5, -0.1, EmotionalTone.TENSE),      # mild losing momentum
        (-0.1,  0.1, EmotionalTone.NEUTRAL),     # stable/even
        ( 0.1,  0.5, EmotionalTone.CALM),        # mild winning momentum
        ( 0.5,  1.0, EmotionalTone.HYPE),         # strong winning momentum
    ]

    def suggest_tone(
        self,
        momentum_score: float,
        win_probability: float = 0.5,
        game_phase: str = "mid_game",
    ) -> EmotionalTone:
        """Suggest commentary tone based on momentum and game state.

        Args:
            momentum_score: -1.0 (losing hard) to 1.0 (winning hard)
            win_probability: 0.0 to 1.0
            game_phase: early/mid/late game

        Returns:
            Suggested EmotionalTone for commentary.
        """
        # Clamp momentum
        momentum_score = max(-1.0, min(1.0, momentum_score))

        # Base tone from momentum
        base_tone = EmotionalTone.NEUTRAL
        for low, high, tone in self._TONE_MAP:
            if low <= momentum_score < high:
                base_tone = tone
                break

        # Override for extreme win probability
        if win_probability > 0.85:
            base_tone = EmotionalTone.HYPE
        elif win_probability < 0.15:
            base_tone = EmotionalTone.URGENT

        # Late game amplifier — everything is more intense
        if game_phase == "late_game":
            if base_tone == EmotionalTone.CALM:
                base_tone = EmotionalTone.HYPE
            elif base_tone == EmotionalTone.TENSE:
                base_tone = EmotionalTone.URGENT

        return base_tone

    def select_with_momentum(
        self,
        library: TemplateLibrary,
        category: TemplateCategory,
        variables: Dict[str, Any],
        momentum_score: float,
        win_probability: float = 0.5,
        game_phase: str = "mid_game",
        game_time_s: int = 0,
    ) -> Optional[str]:
        """Select and render a template with momentum-aware tone."""
        tone = self.suggest_tone(momentum_score, win_probability, game_phase)
        return library.select_and_render(
            category, variables, tone=tone, game_time_s=game_time_s,
        )


# ─── Template chain — multi-sentence commentary ─────────────────────────────

class TemplateChain:
    """Composes multi-sentence commentary from template fragments.

    Builds natural commentary by chaining a primary template with
    optional context, analysis, and follow-up fragments.

    Usage::
        chain = TemplateChain(library)
        text = chain.compose(
            primary_category=TemplateCategory.KILL,
            followup_category=TemplateCategory.WIN_PROB,
            variables={"killer": "Yasuo", "victim": "Teemo",
                       "prob": 65, "context": ""},
        )
        # → "Yasuo takes down Teemo! Win probability now at 65%."
    """

    def __init__(self, library: TemplateLibrary) -> None:
        self._library = library

    def compose(
        self,
        primary_category: TemplateCategory,
        variables: Dict[str, Any],
        followup_category: Optional[TemplateCategory] = None,
        tone: Optional[EmotionalTone] = None,
        game_time_s: int = 0,
        max_sentences: int = 2,
    ) -> Optional[str]:
        """Compose a multi-sentence commentary."""
        parts = []

        # Primary sentence
        primary = self._library.select_and_render(
            primary_category, variables, tone=tone, game_time_s=game_time_s,
        )
        if primary:
            parts.append(primary)

        # Follow-up sentence
        if followup_category and len(parts) < max_sentences:
            followup = self._library.select_and_render(
                followup_category, variables, tone=tone,
                game_time_s=game_time_s,
            )
            if followup:
                parts.append(followup)

        if not parts:
            return None

        return " ".join(parts)


def create_default_bilingual_library(
    language: Language = Language.EN,
) -> BilingualLibrary:
    """Create a library pre-loaded with bilingual templates."""
    lib = BilingualLibrary(language=language)

    lib.add_bilingual_batch(
        en_texts=[
            "{killer} takes down {victim}! {context}",
            "{killer} eliminates {victim}. {context}",
            "{victim} falls to {killer}. {context}",
            "And {killer} gets the kill on {victim}!",
        ],
        zh_texts=[
            "{killer} 击杀了 {victim}！{context}",
            "{killer} 消灭了 {victim}。{context}",
            "{victim} 倒在了 {killer} 手下。{context}",
            "{killer} 拿到了 {victim} 的人头！",
        ],
        category=TemplateCategory.KILL,
        tone=EmotionalTone.NEUTRAL,
    )

    lib.add_bilingual_batch(
        en_texts=[
            "{team} secures {objective}! {context}",
            "Big objective — {team} takes {objective}.",
        ],
        zh_texts=[
            "{team} 拿下了 {objective}！{context}",
            "重大目标 — {team} 拿下 {objective}。",
        ],
        category=TemplateCategory.OBJECTIVE,
        tone=EmotionalTone.HYPE,
    )

    lib.add_bilingual_batch(
        en_texts=[
            "Win probability now at {prob}%.",
            "Sitting at {prob}% win chance. {context}",
        ],
        zh_texts=[
            "当前胜率 {prob}%。",
            "胜率 {prob}%。{context}",
        ],
        category=TemplateCategory.WIN_PROB,
        tone=EmotionalTone.CALM,
    )

    lib.add_bilingual_batch(
        en_texts=[
            "{action} is the play right now. {reason}",
            "Recommend {action}. {reason}",
        ],
        zh_texts=[
            "现在应该 {action}。{reason}",
            "建议 {action}。{reason}",
        ],
        category=TemplateCategory.STRATEGY,
        tone=EmotionalTone.NEUTRAL,
    )

    return lib
