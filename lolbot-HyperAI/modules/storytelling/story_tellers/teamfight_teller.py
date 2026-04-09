"""
TeamfightTeller — Teamfight event narration.
===============================================
lolbot-HyperAI · modules/storytelling/story_tellers

Apollo reference:
    modules/storytelling/story_tellers/close_to_junction_teller.cc
    (domain analog: junction → teamfight)

Narrates teamfight outcomes, multi-kills, ace events, and combat
encounters with contextual tone and varied templates.

位置: lolbot-HyperAI/modules/storytelling/story_tellers/teamfight_teller.py
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

_TEAMFIGHT_WIN_TEMPLATES = [
    "Team wins the fight {score}! {detail}",
    "Great teamfight! Your team comes out ahead {score}. {detail}",
    "A decisive clash — your team takes it {score}. {detail}",
    "Your team dominates the teamfight {score}. {detail}",
    "{score} in the teamfight for your team. {detail}",
]

_TEAMFIGHT_LOSS_TEMPLATES = [
    "Lost the teamfight {score}. {detail}",
    "Tough fight — enemy takes it {score}. {detail}",
    "The enemy wins the engagement {score}. {detail}",
    "A rough teamfight, {score} against your team. {detail}",
]

_MULTIKILL_TEMPLATES = {
    "double": [
        "{player} picks up a double kill!",
        "Double kill for {player}!",
    ],
    "triple": [
        "{player} scores a triple kill!",
        "Triple kill! {player} is on fire!",
    ],
    "quadra": [
        "{player} with the quadra kill!",
        "Incredible quadra kill from {player}!",
    ],
    "penta": [
        "PENTAKILL! {player} cleans up the entire enemy team!",
        "{player} PENTAKILL! Absolutely legendary!",
    ],
}

_ACE_TEMPLATES = [
    "ACE! The entire enemy team is down!",
    "Your team aces the enemy! All five eliminated!",
    "That's an ace! Enemy team wiped out!",
]

_ACE_AGAINST_TEMPLATES = [
    "The enemy aces your team. Play safe and regroup.",
    "Aced. Wait for respawns before contesting anything.",
    "Enemy gets the ace. Stay near base until everyone's back.",
]


class TeamfightTeller(BaseTeller):
    """Narrates teamfight events, multi-kills, and aces.

    Apollo equivalent: ``CloseToJunctionTeller`` — detects when the
    vehicle approaches a junction and creates a "close to junction"
    story. Our equivalent detects teamfight events and creates
    narration about combat outcomes.
    """

    def __init__(self, cooldown_s: float = 8.0) -> None:
        super().__init__(cooldown_s=cooldown_s)

    def name(self) -> str:
        return "teamfight"

    def handled_event_types(self) -> Set[str]:
        return {
            "teamfight_start",
            "teamfight_end",
            "multikill",
            "ace",
            "ace_against",
        }

    def _generate(
        self,
        event: GameEvent,
        context: GameContext,
    ) -> Optional[NarrationSegment]:
        """Generate teamfight narration from event data."""
        etype = event.event_type
        data = event.event_data

        if etype == "teamfight_end":
            return self._narrate_teamfight_end(data, context)
        elif etype == "multikill":
            return self._narrate_multikill(data, context)
        elif etype == "ace":
            return self._narrate_ace(data, context, for_ally=True)
        elif etype == "ace_against":
            return self._narrate_ace(data, context, for_ally=False)
        elif etype == "teamfight_start":
            # Don't narrate fight start — wait for outcome
            return None

        return None

    def _narrate_teamfight_end(
        self,
        data: Dict[str, Any],
        context: GameContext,
    ) -> Optional[NarrationSegment]:
        """Narrate a completed teamfight."""
        ally_kills = data.get("ally_kills", 0)
        enemy_kills = data.get("enemy_kills", 0)
        won = ally_kills > enemy_kills

        # Skip boring 1v1 trades
        total = ally_kills + enemy_kills
        if total < 3:
            return None

        score = f"{ally_kills}-{enemy_kills}"
        detail = ""
        if data.get("baron_context"):
            detail = "Baron was in play."
        elif data.get("dragon_context"):
            detail = f"Dragon soul at stake."

        if won:
            template = self.pick_template(_TEAMFIGHT_WIN_TEMPLATES)
            tone = NarrationTone.EXCITED
            priority = NarrationPriority.HIGH
        else:
            template = self.pick_template(_TEAMFIGHT_LOSS_TEMPLATES)
            tone = NarrationTone.TENSE if context.is_behind else NarrationTone.NEUTRAL
            priority = NarrationPriority.MEDIUM

        text = template.format(score=score, detail=detail).strip()

        return NarrationSegment(
            text=text,
            tone=tone,
            priority=priority,
            event_type="teamfight_end",
            duration_hint_s=3.0,
        )

    def _narrate_multikill(
        self,
        data: Dict[str, Any],
        context: GameContext,
    ) -> Optional[NarrationSegment]:
        """Narrate a multi-kill event."""
        kill_type = data.get("kill_type", "double").lower()
        player = data.get("player_name", "A player")
        is_ally = data.get("is_ally", True)

        templates = _MULTIKILL_TEMPLATES.get(kill_type)
        if not templates:
            return None

        template = self.pick_template(templates)
        text = template.format(player=player)

        # Penta kills are always critical priority
        if kill_type == "penta":
            priority = NarrationPriority.CRITICAL
            tone = NarrationTone.CELEBRATORY if is_ally else NarrationTone.TENSE
        elif kill_type == "quadra":
            priority = NarrationPriority.HIGH
            tone = NarrationTone.EXCITED if is_ally else NarrationTone.WARNING
        else:
            priority = NarrationPriority.MEDIUM
            tone = NarrationTone.EXCITED if is_ally else NarrationTone.NEUTRAL

        return NarrationSegment(
            text=text,
            tone=tone,
            priority=priority,
            event_type=f"multikill_{kill_type}",
            duration_hint_s=2.5,
        )

    def _narrate_ace(
        self,
        data: Dict[str, Any],
        context: GameContext,
        for_ally: bool,
    ) -> Optional[NarrationSegment]:
        """Narrate an ace event."""
        if for_ally:
            template = self.pick_template(_ACE_TEMPLATES)
            tone = NarrationTone.CELEBRATORY
            priority = NarrationPriority.HIGH
        else:
            template = self.pick_template(_ACE_AGAINST_TEMPLATES)
            tone = NarrationTone.WARNING
            priority = NarrationPriority.HIGH

        return NarrationSegment(
            text=template,
            tone=tone,
            priority=priority,
            event_type="ace" if for_ally else "ace_against",
            duration_hint_s=3.0,
        )
