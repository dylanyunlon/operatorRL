"""
modules/control/narration/game_narrator.py — Natural language game narration.
==============================================================================
Claude19 · Wires into ControlComponent to generate spoken commentary

Converts structured game events and predictions into natural language
sentences suitable for TTS. This replaces the ad-hoc string formatting
scattered across modules with a centralized narration engine.

Apollo analogy: modules/storytelling/story_telling_component.cc generates
narrative descriptions of driving scenarios for user-facing messages.

File location: lolbot-HyperAI/modules/control/narration/game_narrator.py
"""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class NarrationLine:
    """A single narration output."""
    text: str
    category: str        # win_update, kill, objective, strategy, phase
    priority: int = 1    # 0=critical, 1=high, 2=medium, 3=ambient
    game_time: float = 0.0
    ttl_s: float = 10.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "text": self.text,
            "category": self.category,
            "priority": self.priority,
            "game_time": round(self.game_time, 1),
        }


class GameNarrator:
    """Generates natural language narration from structured game data.

    Usage::
        narrator = GameNarrator()
        lines = narrator.narrate_win_update(0.72, 0.65, "BLUE", 900.0)
        for line in lines:
            voice_queue.enqueue(line.text, line.category, line.priority)
    """

    _COOLDOWNS: Dict[str, float] = {
        "win_update": 30.0,
        "kill": 5.0,
        "objective": 3.0,
        "strategy": 20.0,
        "phase": 60.0,
        "momentum": 30.0,
    }

    def __init__(self) -> None:
        self._last_fire: Dict[str, float] = {}
        self._narration_count: int = 0

    def _check_cooldown(self, category: str, game_time: float) -> bool:
        """Return True if category is off cooldown."""
        cooldown = self._COOLDOWNS.get(category, 10.0)
        last = self._last_fire.get(category, 0.0)
        return (game_time - last) >= cooldown

    def _mark_fired(self, category: str, game_time: float) -> None:
        self._last_fire[category] = game_time
        self._narration_count += 1

    # ─── Win Probability Updates ────────────────────────────────────

    def narrate_win_update(
        self,
        current_prob: float,
        previous_prob: float,
        active_team: str,
        game_time: float,
    ) -> List[NarrationLine]:
        """Generate narration for win probability changes."""
        if not self._check_cooldown("win_update", game_time):
            return []

        our_prob = current_prob if active_team == "BLUE" else (1 - current_prob)
        prev_our = previous_prob if active_team == "BLUE" else (1 - previous_prob)
        delta = our_prob - prev_our
        pct = our_prob * 100

        lines = []

        if abs(delta) < 0.03:
            # Minor change — ambient update
            if pct > 60:
                text = random.choice([
                    f"We're in a good spot — {pct:.0f}% win probability.",
                    f"Sitting at {pct:.0f}% — keep up the pressure.",
                ])
            elif pct < 40:
                text = random.choice([
                    f"We're behind at {pct:.0f}% — need to find an opening.",
                    f"Down to {pct:.0f}% — play smart, wait for mistakes.",
                ])
            else:
                text = f"Game is close — {pct:.0f}% win probability."
            lines.append(NarrationLine(
                text=text, category="win_update",
                priority=3, game_time=game_time,
            ))
        elif delta > 0.08:
            text = random.choice([
                f"Big swing our way! Up to {pct:.0f}%.",
                f"Momentum shift — we jumped to {pct:.0f}%!",
            ])
            lines.append(NarrationLine(
                text=text, category="win_update",
                priority=1, game_time=game_time,
            ))
        elif delta < -0.08:
            text = random.choice([
                f"We lost ground — dropped to {pct:.0f}%.",
                f"Bad exchange. Down to {pct:.0f}%.",
            ])
            lines.append(NarrationLine(
                text=text, category="win_update",
                priority=1, game_time=game_time,
            ))

        if lines:
            self._mark_fired("win_update", game_time)
        return lines

    # ─── Kill Events ────────────────────────────────────────────────

    def narrate_kill(
        self,
        killer: str,
        victim: str,
        is_ally_kill: bool,
        game_time: float,
        multi_kill: int = 0,
    ) -> List[NarrationLine]:
        if not self._check_cooldown("kill", game_time):
            return []

        lines = []

        if multi_kill >= 4:
            text = f"QUADRA KILL by {killer}!"
            prio = 0
        elif multi_kill == 3:
            text = f"TRIPLE KILL for {killer}!"
            prio = 0
        elif multi_kill == 2:
            text = f"Double kill for {killer}!"
            prio = 1
        elif is_ally_kill:
            text = random.choice([
                f"{killer} takes down {victim}.",
                f"Nice! {killer} eliminates {victim}.",
            ])
            prio = 2
        else:
            text = random.choice([
                f"{victim} has been slain by {killer}.",
                f"We lost {victim} to {killer}.",
            ])
            prio = 2

        lines.append(NarrationLine(
            text=text, category="kill",
            priority=prio, game_time=game_time,
        ))
        self._mark_fired("kill", game_time)
        return lines

    # ─── Objective Events ───────────────────────────────────────────

    def narrate_objective(
        self,
        objective: str,
        team: str,
        is_ally: bool,
        game_time: float,
    ) -> List[NarrationLine]:
        if not self._check_cooldown("objective", game_time):
            return []

        obj_name = objective.replace("_", " ").title()

        if is_ally:
            text = random.choice([
                f"We secured {obj_name}!",
                f"Nice objective — {obj_name} is ours.",
            ])
            prio = 1
        else:
            text = random.choice([
                f"They took {obj_name}.",
                f"Enemy secures {obj_name} — we need to contest next time.",
            ])
            prio = 1

        self._mark_fired("objective", game_time)
        return [NarrationLine(
            text=text, category="objective",
            priority=prio, game_time=game_time,
        )]

    # ─── Strategy Advice ────────────────────────────────────────────

    def narrate_strategy(
        self,
        action: str,
        reasoning: str,
        urgency: float,
        game_time: float,
    ) -> List[NarrationLine]:
        if not self._check_cooldown("strategy", game_time):
            return []

        prio = 1 if urgency > 0.7 else 2

        # Trim to reasonable TTS length
        if len(reasoning) > 120:
            reasoning = reasoning[:117] + "..."

        self._mark_fired("strategy", game_time)
        return [NarrationLine(
            text=reasoning, category="strategy",
            priority=prio, game_time=game_time,
        )]

    # ─── Phase Transitions ──────────────────────────────────────────

    def narrate_phase_change(
        self, from_phase: str, to_phase: str, game_time: float,
    ) -> List[NarrationLine]:
        if not self._check_cooldown("phase", game_time):
            return []

        phase_tips = {
            "LANING": "Laning phase — focus on CS and trading.",
            "EARLY_SKIRMISH": "Early skirmishes starting — watch your map.",
            "MID_GAME": "Mid game — group for objectives.",
            "LATE_MID": "Late mid game — baron dances begin.",
            "LATE_GAME": "Late game — one fight can decide everything.",
        }
        text = phase_tips.get(to_phase, f"Game entering {to_phase} phase.")

        self._mark_fired("phase", game_time)
        return [NarrationLine(
            text=text, category="phase",
            priority=2, game_time=game_time,
        )]

    # ─── Momentum ───────────────────────────────────────────────────

    def narrate_momentum_shift(
        self, from_state: str, to_state: str, reason: str, game_time: float,
    ) -> List[NarrationLine]:
        if not self._check_cooldown("momentum", game_time):
            return []

        shift_texts = {
            "SURGING": "We have huge momentum — press the advantage!",
            "GAINING": "Momentum in our favor — keep it up.",
            "NEUTRAL": "Game is evening out.",
            "LOSING": "They're building momentum — play cautiously.",
            "COLLAPSING": "We're in trouble — avoid fights, farm safely.",
        }
        text = shift_texts.get(to_state, f"Momentum shifted to {to_state}.")
        if reason:
            text += f" ({reason})"

        prio = 1 if to_state in ("SURGING", "COLLAPSING") else 2

        self._mark_fired("momentum", game_time)
        return [NarrationLine(
            text=text, category="momentum",
            priority=prio, game_time=game_time,
        )]

    def stats(self) -> Dict[str, Any]:
        return {"narration_count": self._narration_count}

    def reset(self) -> None:
        self._last_fire.clear()
        self._narration_count = 0
