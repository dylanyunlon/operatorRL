"""
CrossGameCoachingTemplateEngine — Generate coaching advice using cross-game templates.

Uses universal coaching concepts (positioning, timing, resource management) to
generate advice that can be specialized per game.

Location: integrations/lol-history/src/lol_history/cross_game_coaching_template_engine.py

Reference (拿来主義):
  - integrations/lol-history/src/lol_history/history_driven_coaching_advisor.py（M605）:
    coaching advice generation
  - integrations/lol-history/src/lol_history/protocol_anomaly_coaching_translator.py（M654）:
    rule→advice mapping

Design Notes (Knuth-level critique):
  User:
    - register_template() adds universal templates — game-agnostic.
    - generate_advice() specializes template for specific game context.
    - get_templates() lists available templates for UI display.
  System:
    - Templates are parameterized strings with {game_type}, {context} placeholders.
    - Per-game specialization registered separately from core templates.
    - Advice priority ordering built in.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "integrations.lol_history.cross_game_coaching_template_engine.v1"


class CoachingTemplate:
    __slots__ = ("name", "category", "universal_text", "priority", "game_specializations")

    def __init__(
        self, name: str, category: str, universal_text: str,
        priority: int = 5, game_specializations: Optional[Dict[str, str]] = None,
    ) -> None:
        self.name = name
        self.category = category  # positioning, timing, resource, combat, vision, macro
        self.universal_text = universal_text
        self.priority = priority  # 1=highest
        self.game_specializations = game_specializations or {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "category": self.category,
            "universal_text": self.universal_text,
            "priority": self.priority,
            "specialized_games": list(self.game_specializations.keys()),
        }


# Default cross-game templates
_DEFAULT_TEMPLATES = [
    CoachingTemplate("safe_positioning", "positioning",
        "Maintain safe positioning relative to threats — stay near cover/allies.",
        priority=2,
        game_specializations={
            "lol": "Stay behind your front line; respect enemy flash ranges.",
            "dota2": "Keep high ground advantage; watch for blink initiations.",
            "mahjong": "Maintain a safe hand — prioritize defense when behind.",
        }),
    CoachingTemplate("resource_efficiency", "resource",
        "Optimize resource acquisition — avoid waste, time pickups efficiently.",
        priority=3,
        game_specializations={
            "lol": "Keep CS/min above 7; back-time on cannon waves.",
            "dota2": "Stack camps before farming; use courier efficiently.",
            "mahjong": "Discard tiles that minimize point loss potential.",
        }),
    CoachingTemplate("timing_awareness", "timing",
        "Track key cooldowns and timing windows for optimal play.",
        priority=1,
        game_specializations={
            "lol": "Track enemy summoner spells; dragon spawns at 5:00.",
            "dota2": "Track Roshan timer; buyback cooldowns before fights.",
            "mahjong": "Watch discard timing for opponent hand reading.",
        }),
    CoachingTemplate("map_control", "macro",
        "Establish and maintain map/board control through vision and presence.",
        priority=2,
        game_specializations={
            "lol": "Ward river before objectives; control side bushes.",
            "dota2": "Place observer wards on high ground; deward enemy vision.",
            "mahjong": "Control the discard flow; avoid feeding dangerous tiles.",
        }),
]


class CrossGameCoachingTemplateEngine:
    """Cross-game coaching template engine.

    Public API:
        register_template(template)
        add_game_specialization(template_name, game_type, text)
        generate_advice(game_type, context) -> list[dict]
        get_templates(category=None) -> list[dict]
        get_stats() -> dict
    """

    def __init__(self, load_defaults: bool = True) -> None:
        self._templates: Dict[str, CoachingTemplate] = {}
        self._generate_count: int = 0
        self.evolution_callback: Optional[Callable[[Dict[str, Any]], None]] = None

        if load_defaults:
            for t in _DEFAULT_TEMPLATES:
                self._templates[t.name] = t

    def register_template(self, template: CoachingTemplate) -> None:
        self._templates[template.name] = template
        self._fire("template_registered", {"name": template.name})

    def add_game_specialization(
        self, template_name: str, game_type: str, text: str,
    ) -> bool:
        tpl = self._templates.get(template_name)
        if tpl is None:
            return False
        tpl.game_specializations[game_type] = text
        return True

    def generate_advice(
        self,
        game_type: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """Generate coaching advice for a specific game.

        Args:
            game_type: Target game.
            context: Optional game state context for filtering.

        Returns:
            List of advice dicts sorted by priority.
        """
        self._generate_count += 1
        context = context or {}
        advice_list: List[Dict[str, Any]] = []

        for tpl in self._templates.values():
            # Use game-specific text if available, else universal
            text = tpl.game_specializations.get(game_type, tpl.universal_text)
            advice_list.append({
                "template": tpl.name,
                "category": tpl.category,
                "advice": text,
                "priority": tpl.priority,
                "is_specialized": game_type in tpl.game_specializations,
            })

        # Sort by priority (lower = more important)
        advice_list.sort(key=lambda x: x["priority"])
        return advice_list

    def get_templates(self, category: Optional[str] = None) -> List[Dict[str, Any]]:
        result = []
        for tpl in self._templates.values():
            if category is not None and tpl.category != category:
                continue
            result.append(tpl.to_dict())
        return result

    def get_stats(self) -> Dict[str, Any]:
        return {
            "template_count": len(self._templates),
            "generate_count": self._generate_count,
            "categories": list(set(t.category for t in self._templates.values())),
        }

    def _fire(self, event_type: str, data: Dict[str, Any]) -> None:
        data["component"] = _EVOLUTION_KEY
        data["ts"] = time.time()
        cb = self.evolution_callback
        if cb is not None:
            try:
                cb({"type": event_type, **data})
            except Exception:
                logger.exception("evolution_callback raised")
