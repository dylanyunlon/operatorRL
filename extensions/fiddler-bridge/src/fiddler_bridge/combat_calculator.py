"""
Combat Calculator — Server-authoritative combat result parsing.

Parses server-determined combat results including damage events,
damage breakdowns, combat outcomes, effective HP, and DPS calculations.

Location: extensions/fiddler-bridge/src/fiddler_bridge/combat_calculator.py
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "fiddler_bridge.combat_calculator.v1"


class CombatCalculator:
    """Server-authoritative combat result calculator.

    Parses and accumulates damage events, computes damage breakdowns,
    determines combat outcomes, and calculates effective HP/DPS.
    """

    def __init__(self) -> None:
        # (source, target) -> {damage_type -> total}
        self._damage: dict[tuple[str, str], dict[str, float]] = defaultdict(
            lambda: defaultdict(float)
        )
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def parse_damage_event(self, event: dict[str, Any]) -> dict[str, Any]:
        """Parse a raw damage event.

        Args:
            event: Event dict with source, target, amount, damage_type.

        Returns:
            Normalized damage event dict.
        """
        return {
            "source": event.get("source", ""),
            "target": event.get("target", ""),
            "amount": event.get("amount", 0),
            "damage_type": event.get("damage_type", "unknown"),
        }

    def record_damage(
        self,
        source: str,
        target: str,
        amount: float,
        damage_type: str = "unknown",
    ) -> None:
        """Record a damage instance.

        Args:
            source: Damage source champion.
            target: Damage target champion.
            amount: Damage amount.
            damage_type: "magic", "physical", "true", or "unknown".
        """
        self._damage[(source, target)][damage_type] += amount

    def get_total_damage(self, source: str, target: str) -> float:
        """Get total damage from source to target.

        Args:
            source: Source champion.
            target: Target champion.

        Returns:
            Total damage dealt.
        """
        breakdown = self._damage.get((source, target))
        if breakdown is None:
            return 0.0
        return sum(breakdown.values())

    def get_damage_breakdown(
        self, source: str, target: str
    ) -> dict[str, float]:
        """Get damage breakdown by type.

        Args:
            source: Source champion.
            target: Target champion.

        Returns:
            Dict mapping damage_type to total amount.
        """
        breakdown = self._damage.get((source, target))
        if breakdown is None:
            return {}
        return dict(breakdown)

    def determine_combat_result(
        self,
        attacker_hp_before: float,
        attacker_hp_after: float,
        defender_hp_before: float,
        defender_hp_after: float,
    ) -> dict[str, Any]:
        """Determine combat result.

        Args:
            attacker_hp_before: Attacker HP before combat.
            attacker_hp_after: Attacker HP after combat.
            defender_hp_before: Defender HP before combat.
            defender_hp_after: Defender HP after combat.

        Returns:
            Result dict with winner, damage_dealt, damage_taken.
        """
        attacker_damage = defender_hp_before - defender_hp_after
        defender_damage = attacker_hp_before - attacker_hp_after

        if defender_hp_after <= 0 and attacker_hp_after > 0:
            winner = "attacker"
        elif attacker_hp_after <= 0 and defender_hp_after > 0:
            winner = "defender"
        elif attacker_hp_after <= 0 and defender_hp_after <= 0:
            winner = "trade"
        else:
            winner = "attacker" if attacker_damage > defender_damage else "defender"

        return {
            "winner": winner,
            "attacker_damage_dealt": attacker_damage,
            "defender_damage_dealt": defender_damage,
            "attacker_hp_remaining": max(0, attacker_hp_after),
            "defender_hp_remaining": max(0, defender_hp_after),
        }

    def effective_hp(
        self,
        base_hp: float,
        armor: float = 0.0,
        magic_resist: float = 0.0,
    ) -> float:
        """Calculate effective HP considering resistances.

        Uses the standard LoL formula:
        EHP_physical = HP * (1 + armor/100)
        EHP_magic = HP * (1 + mr/100)
        Returns averaged EHP.

        Args:
            base_hp: Base health points.
            armor: Armor value.
            magic_resist: Magic resistance value.

        Returns:
            Effective HP (averaged).
        """
        ehp_phys = base_hp * (1 + armor / 100.0)
        ehp_magic = base_hp * (1 + magic_resist / 100.0)
        return (ehp_phys + ehp_magic) / 2.0

    def calculate_dps(
        self, total_damage: float, duration_seconds: float
    ) -> float:
        """Calculate damage per second.

        Args:
            total_damage: Total damage dealt.
            duration_seconds: Combat duration in seconds.

        Returns:
            DPS value.
        """
        if duration_seconds <= 0:
            return 0.0
        return total_damage / duration_seconds

    def reset(self) -> None:
        """Reset all damage records."""
        self._damage.clear()

    def _fire_evolution(self, data: dict[str, Any]) -> None:
        """Fire evolution callback."""
        if self.evolution_callback is None:
            return
        enriched = {
            "module": "combat_calculator",
            "timestamp": time.time(),
            **data,
        }
        try:
            self.evolution_callback(enriched)
        except Exception as exc:
            logger.warning("Evolution callback error: %s", exc)
