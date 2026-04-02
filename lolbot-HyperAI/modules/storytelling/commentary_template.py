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
