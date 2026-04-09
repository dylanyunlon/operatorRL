"""
ObjectiveTeller — Objective event narration (dragon/baron/turret/herald).
==========================================================================
lolbot-HyperAI · modules/storytelling/story_tellers

Apollo parallel: close_to_junction_teller handles navigation landmarks;
ObjectiveTeller handles LoL map objectives (strategic landmarks).

位置: lolbot-HyperAI/modules/storytelling/story_tellers/objective_teller.py
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Set

from modules.storytelling.story_tellers.base_teller import (
    BaseTeller,
    GameContext,
    GameEvent,
    NarrationPriority,
    NarrationSegment,
    NarrationTone,
)

logger = logging.getLogger(__name__)

# ── Templates ────────────────────────────────────────────────────────────────

_DRAGON_ALLY_TEMPLATES = [
    "Your team secures the {dragon_type} dragon! {context}",
    "{dragon_type} dragon goes to your team. {context}",
    "Nice! Ally team takes the {dragon_type} drake. {context}",
]

_DRAGON_ENEMY_TEMPLATES = [
    "Enemy takes the {dragon_type} dragon. {context}",
    "{dragon_type} dragon goes to the enemy. {context}",
    "The enemy secures {dragon_type} drake. {context}",
]

_DRAGON_SOUL_TEMPLATES = [
    "DRAGON SOUL! Your team claims the {dragon_type} soul!",
    "{dragon_type} Soul secured! Huge power spike for your team!",
]

_DRAGON_SOUL_ENEMY_TEMPLATES = [
    "Enemy gets {dragon_type} Soul. This is a critical disadvantage.",
    "{dragon_type} Soul goes to the enemy. Play very carefully.",
]

_BARON_ALLY_TEMPLATES = [
    "BARON NASHOR secured! Push with the empowered minions!",
    "Your team takes Baron! Use the buff wisely — push lanes.",
    "Baron is down — your team. Push objectives with the buff!",
]

_BARON_ENEMY_TEMPLATES = [
    "Enemy takes Baron Nashor. Defend carefully and don't overextend.",
    "Baron goes to the enemy. Play safe and clear the empowered minions.",
    "Enemy Baron. Focus on wave clear and defending turrets.",
]

_HERALD_TEMPLATES = [
    "Rift Herald secured by your team! Use it on a lane.",
    "Your team picks up the Herald. Good for pushing a tower.",
]

_TURRET_ALLY_TEMPLATES = [
    "Your team destroys a{position} turret! Map control expanding.",
    "Turret down! Your team takes a{position} tower.",
]

_TURRET_ENEMY_TEMPLATES = [
    "Enemy takes a{position} turret. Watch your map pressure.",
    "Your team loses a{position} tower. Adjust your positioning.",
]

_INHIBITOR_TEMPLATES = [
    "INHIBITOR DOWN! Super minions incoming in that lane!",
    "Your team takes an inhibitor! Super minions will push.",
]


class ObjectiveTeller(BaseTeller):
    """Narrates objective events — dragons, baron, turrets, herald.

    Strategic importance determines narration priority:
    - Dragon soul / Baron: CRITICAL
    - Dragon / Inhibitor: HIGH
    - Turret / Herald: MEDIUM
    """

    def __init__(self, cooldown_s: float = 5.0) -> None:
        super().__init__(cooldown_s=cooldown_s)

    def name(self) -> str:
        return "objective"

    def handled_event_types(self) -> Set[str]:
        return {
            "dragon_kill",
            "dragon_soul",
            "baron_kill",
            "herald_kill",
            "turret_destroy",
            "inhibitor_destroy",
        }

    def _generate(
        self,
        event: GameEvent,
        context: GameContext,
    ) -> Optional[NarrationSegment]:
        """Route to specific objective narration."""
        etype = event.event_type
        data = event.event_data

        if etype == "dragon_kill":
            return self._narrate_dragon(data, context)
        elif etype == "dragon_soul":
            return self._narrate_dragon_soul(data, context)
        elif etype == "baron_kill":
            return self._narrate_baron(data, context)
        elif etype == "herald_kill":
            return self._narrate_herald(data, context)
        elif etype == "turret_destroy":
            return self._narrate_turret(data, context)
        elif etype == "inhibitor_destroy":
            return self._narrate_inhibitor(data, context)

        return None

    def _narrate_dragon(
        self,
        data: Dict[str, Any],
        context: GameContext,
    ) -> Optional[NarrationSegment]:
        """Narrate a dragon kill."""
        dragon_type = data.get("dragon_type", "Elemental")
        is_ally = data.get("is_ally", True)
        ally_dragons = data.get("ally_dragon_count", 0)
        enemy_dragons = data.get("enemy_dragon_count", 0)

        # Context about dragon count
        ctx = ""
        if is_ally and ally_dragons >= 3:
            ctx = "One more for Dragon Soul!"
        elif not is_ally and enemy_dragons >= 3:
            ctx = "Enemy is one away from Dragon Soul — contest the next one!"

        if is_ally:
            template = self.pick_template(_DRAGON_ALLY_TEMPLATES)
            tone = NarrationTone.EXCITED
        else:
            template = self.pick_template(_DRAGON_ENEMY_TEMPLATES)
            tone = NarrationTone.TENSE if enemy_dragons >= 2 else NarrationTone.NEUTRAL

        text = template.format(dragon_type=dragon_type, context=ctx).strip()

        return NarrationSegment(
            text=text,
            tone=tone,
            priority=NarrationPriority.HIGH,
            event_type="dragon_kill",
            duration_hint_s=3.0,
        )

    def _narrate_dragon_soul(
        self,
        data: Dict[str, Any],
        context: GameContext,
    ) -> Optional[NarrationSegment]:
        """Narrate a dragon soul acquisition — always critical."""
        dragon_type = data.get("dragon_type", "Elemental")
        is_ally = data.get("is_ally", True)

        if is_ally:
            template = self.pick_template(_DRAGON_SOUL_TEMPLATES)
            tone = NarrationTone.CELEBRATORY
        else:
            template = self.pick_template(_DRAGON_SOUL_ENEMY_TEMPLATES)
            tone = NarrationTone.WARNING

        text = template.format(dragon_type=dragon_type)

        return NarrationSegment(
            text=text,
            tone=tone,
            priority=NarrationPriority.CRITICAL,
            event_type="dragon_soul",
            duration_hint_s=4.0,
        )

    def _narrate_baron(
        self,
        data: Dict[str, Any],
        context: GameContext,
    ) -> Optional[NarrationSegment]:
        """Narrate Baron Nashor — always high priority."""
        is_ally = data.get("is_ally", True)

        if is_ally:
            template = self.pick_template(_BARON_ALLY_TEMPLATES)
            tone = NarrationTone.CELEBRATORY
        else:
            template = self.pick_template(_BARON_ENEMY_TEMPLATES)
            tone = NarrationTone.WARNING

        return NarrationSegment(
            text=template,
            tone=tone,
            priority=NarrationPriority.CRITICAL,
            event_type="baron_kill",
            duration_hint_s=4.0,
        )

    def _narrate_herald(
        self,
        data: Dict[str, Any],
        context: GameContext,
    ) -> Optional[NarrationSegment]:
        """Narrate Rift Herald."""
        is_ally = data.get("is_ally", True)
        if not is_ally:
            return None  # Don't narrate enemy herald (low impact)

        template = self.pick_template(_HERALD_TEMPLATES)
        return NarrationSegment(
            text=template,
            tone=NarrationTone.NEUTRAL,
            priority=NarrationPriority.MEDIUM,
            event_type="herald_kill",
            duration_hint_s=2.0,
        )

    def _narrate_turret(
        self,
        data: Dict[str, Any],
        context: GameContext,
    ) -> Optional[NarrationSegment]:
        """Narrate turret destruction."""
        is_ally = data.get("is_ally", True)
        lane = data.get("lane", "")
        position = f" {lane}" if lane else ""

        if is_ally:
            template = self.pick_template(_TURRET_ALLY_TEMPLATES)
            tone = NarrationTone.EXCITED
        else:
            template = self.pick_template(_TURRET_ENEMY_TEMPLATES)
            tone = NarrationTone.TENSE if context.is_behind else NarrationTone.NEUTRAL

        text = template.format(position=position)

        return NarrationSegment(
            text=text,
            tone=tone,
            priority=NarrationPriority.MEDIUM,
            event_type="turret_destroy",
            duration_hint_s=2.0,
        )

    def _narrate_inhibitor(
        self,
        data: Dict[str, Any],
        context: GameContext,
    ) -> Optional[NarrationSegment]:
        """Narrate inhibitor destruction."""
        is_ally = data.get("is_ally", True)
        if not is_ally:
            text = "Your team loses an inhibitor. Defend against super minions."
            tone = NarrationTone.WARNING
        else:
            template = self.pick_template(_INHIBITOR_TEMPLATES)
            text = template
            tone = NarrationTone.EXCITED

        return NarrationSegment(
            text=text,
            tone=tone,
            priority=NarrationPriority.HIGH,
            event_type="inhibitor_destroy",
            duration_hint_s=3.0,
        )
