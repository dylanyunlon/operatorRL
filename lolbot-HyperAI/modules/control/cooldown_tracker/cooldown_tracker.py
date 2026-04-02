"""
CooldownTracker — Summoner spell and ultimate ability cooldown tracking.
=========================================================================

Tracks cooldown states for all 10 players' summoner spells and
ultimates, providing real-time availability windows for strategic
decision-making (engage timing, objective contests, gank windows).

Architecture position:
    modules/control/cooldown_tracker/cooldown_tracker.py   ← YOU ARE HERE
    ├─ Reads: /lol/game_state (GameSnapshot)
    ├─ Reads: /lol/events (ability usage events)
    ├─ Publishes: /lol/cooldowns (CooldownState)
    └─ Consumed by: modules/planning/macro/macro_planner.py
                    modules/prediction/team_fight/teamfight_predictor.py

Apollo reference:
    modules/control/controller_agent.cc — timing-critical state machine
    modules/prediction/predictor_manager/ — temporal prediction

Design notes:
    - Flash = 300s (255s with Cosmic Insight + Ionian Boots)
    - Teleport = 360s base (reduced with towers destroyed)
    - Ultimates tracked per champion with known base cooldowns
    - CDR (Ability Haste) factored into cooldown calculations
    - Uncertainty: enemy cooldowns estimated, ally confirmed
    - Integration with teamfight predictor for fight assessment
"""

from __future__ import annotations

import logging
import math
import time
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, List, Optional, Set, Tuple

from cyber.component.timer_component import ComponentConfig, TimerComponent
from cyber.node.node import CyberNode, Reader, Writer
from cyber.logger.cyber_logger import get_logger

logger = get_logger("cooldown_tracker")

# ─── Constants ───────────────────────────────────────────────────────────────

_COOLDOWN_INTERVAL_MS = 200.0  # 5Hz — timing-critical
_MAX_PLAYERS = 10
_ALLY_TEAM_SIZE = 5

# Base cooldowns (seconds) for summoner spells
_SUMMONER_SPELL_COOLDOWNS: Dict[str, float] = {
    "Flash": 300.0,
    "Teleport": 360.0,
    "Ignite": 180.0,
    "Heal": 240.0,
    "Exhaust": 210.0,
    "Barrier": 180.0,
    "Cleanse": 210.0,
    "Ghost": 210.0,
    "Smite": 90.0,
    "Mark": 80.0,        # ARAM snowball
    "Clarity": 240.0,
}

# CDR from items/runes that affect summoner spell cooldowns
_COSMIC_INSIGHT_CDR = 0.082      # 18 summoner spell haste → ~8.2% CDR
_LUCIDITY_BOOTS_CDR = 0.091      # 20 summoner spell haste → ~9.1% CDR
_HEXFLASH_CD = 20.0

# Base ultimate cooldowns per level bracket (level 6/11/16)
# Stored as (lv6, lv11, lv16) tuples for common champions
_ULTIMATE_COOLDOWNS: Dict[str, Tuple[float, float, float]] = {
    "Ahri": (130, 105, 80),
    "Amumu": (150, 130, 110),
    "Annie": (120, 100, 80),
    "Ashe": (100, 80, 60),
    "Blitzcrank": (100, 80, 60),
    "Darius": (120, 100, 80),
    "Ezreal": (120, 105, 90),
    "Garen": (120, 100, 80),
    "Jinx": (90, 75, 60),
    "Lux": (80, 60, 40),
    "Malphite": (130, 105, 80),
    "MissFortune": (120, 110, 100),
    "Morgana": (120, 100, 80),
    "Senna": (160, 130, 100),
    "Thresh": (140, 120, 100),
    "Yasuo": (80, 55, 30),
    "Zed": (120, 90, 60),
}
_DEFAULT_ULT_COOLDOWN: Tuple[float, float, float] = (120, 100, 80)


class CooldownType(Enum):
    """Types of tracked cooldowns."""
    SUMMONER_SPELL_1 = auto()
    SUMMONER_SPELL_2 = auto()
    ULTIMATE = auto()


class CooldownConfidence(Enum):
    """Confidence level in the cooldown estimate."""
    CONFIRMED = auto()     # Ally — we know for certain
    ESTIMATED = auto()     # Enemy — we saw it used
    ASSUMED = auto()       # Enemy — no data, assume available
    UNKNOWN = auto()


@dataclass
class SpellCooldown:
    """Tracked cooldown for a single spell."""
    spell_name: str
    cooldown_type: CooldownType
    base_cooldown_s: float
    effective_cooldown_s: float  # after CDR
    used_at_game_time_s: float = 0.0
    available_at_game_time_s: float = 0.0
    confidence: CooldownConfidence = CooldownConfidence.ASSUMED
    times_used: int = 0

    @property
    def is_available(self) -> bool:
        """Check if the spell is off cooldown (requires current time)."""
        # This property can't know current time; use remaining_s() instead
        return self.available_at_game_time_s <= 0.0

    def remaining_s(self, current_game_time_s: float) -> float:
        """Seconds remaining on cooldown."""
        return max(0.0, self.available_at_game_time_s - current_game_time_s)

    def mark_used(self, game_time_s: float) -> None:
        """Record that this spell was used."""
        self.used_at_game_time_s = game_time_s
        self.available_at_game_time_s = game_time_s + self.effective_cooldown_s
        self.confidence = CooldownConfidence.CONFIRMED
        self.times_used += 1


@dataclass
class PlayerCooldowns:
    """All tracked cooldowns for a single player."""
    player_name: str
    team: str  # "ally" or "enemy"
    champion_name: str = ""
    champion_level: int = 1
    ability_haste: int = 0

    summoner_1: Optional[SpellCooldown] = None
    summoner_2: Optional[SpellCooldown] = None
    ultimate: Optional[SpellCooldown] = None

    has_cosmic_insight: bool = False
    has_lucidity_boots: bool = False

    def all_available(self, game_time_s: float) -> bool:
        """Check if all spells are available."""
        for spell in [self.summoner_1, self.summoner_2, self.ultimate]:
            if spell and spell.remaining_s(game_time_s) > 0:
                return False
        return True

    def flash_remaining(self, game_time_s: float) -> Optional[float]:
        """Get flash cooldown remaining (convenience method)."""
        for spell in [self.summoner_1, self.summoner_2]:
            if spell and spell.spell_name == "Flash":
                return spell.remaining_s(game_time_s)
        return None

    def ult_remaining(self, game_time_s: float) -> Optional[float]:
        """Get ultimate cooldown remaining."""
        if self.ultimate:
            return self.ultimate.remaining_s(game_time_s)
        return None


@dataclass
class TeamCooldownSummary:
    """Aggregate cooldown state for a team."""
    flashes_available: int = 0
    flashes_total: int = 0
    ults_available: int = 0
    ults_total: int = 0
    next_flash_available_s: float = 0.0  # soonest flash coming back
    next_ult_available_s: float = 0.0


@dataclass
class CooldownState:
    """Published cooldown state for downstream modules."""
    timestamp_ns: int
    game_time_s: float
    players: Dict[str, PlayerCooldowns]
    ally_summary: TeamCooldownSummary
    enemy_summary: TeamCooldownSummary
    engagement_windows: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        result = {
            "game_time_s": self.game_time_s,
            "ally": {
                "flashes": f"{self.ally_summary.flashes_available}/{self.ally_summary.flashes_total}",
                "ults": f"{self.ally_summary.ults_available}/{self.ally_summary.ults_total}",
            },
            "enemy": {
                "flashes": f"{self.enemy_summary.flashes_available}/{self.enemy_summary.flashes_total}",
                "ults": f"{self.enemy_summary.ults_available}/{self.enemy_summary.ults_total}",
            },
            "engagement_windows": self.engagement_windows,
        }
        return result


def _ability_haste_to_cdr(haste: int) -> float:
    """Convert ability haste to CDR percentage (0.0 to 1.0)."""
    return haste / (100.0 + haste)


def _get_ult_base_cooldown(
    champion_name: str, level: int,
) -> float:
    """Get ultimate base cooldown for a champion at given level."""
    cds = _ULTIMATE_COOLDOWNS.get(champion_name, _DEFAULT_ULT_COOLDOWN)
    if level >= 16:
        return cds[2]
    if level >= 11:
        return cds[1]
    return cds[0]


def _compute_effective_cooldown(
    base_cd: float,
    ability_haste: int = 0,
    has_cosmic_insight: bool = False,
    has_lucidity_boots: bool = False,
    is_summoner_spell: bool = False,
) -> float:
    """Compute effective cooldown after all CDR sources."""
    if is_summoner_spell:
        # Summoner spell haste is separate from ability haste
        summoner_haste = 0
        if has_cosmic_insight:
            summoner_haste += 18
        if has_lucidity_boots:
            summoner_haste += 20
        cdr = _ability_haste_to_cdr(summoner_haste)
    else:
        cdr = _ability_haste_to_cdr(ability_haste)

    return base_cd * (1.0 - cdr)


class CooldownTracker(TimerComponent):
    """Tracks summoner spell and ultimate cooldowns for all players.

    Each ``Proc()`` cycle:
    1. Reads GameSnapshot for player list, levels, items
    2. Reads events for spell usage triggers
    3. Updates cooldown timers
    4. Computes team-level cooldown summary
    5. Identifies engagement windows
    6. Publishes CooldownState on ``/lol/cooldowns``
    """

    def __init__(self) -> None:
        super().__init__(
            config=ComponentConfig(
                name="cooldown_tracker",
                interval_ms=_COOLDOWN_INTERVAL_MS,
                warn_threshold_ms=180.0,
            ),
        )
        self.node = CyberNode("cooldown_tracker")

        self._game_state_reader: Optional[Reader] = None
        self._events_reader: Optional[Reader] = None
        self._cooldown_writer: Optional[Writer] = None

        self._players: Dict[str, PlayerCooldowns] = {}
        self._current_game_time: float = 0.0
        self._processed_event_ids: Set[str] = set()
        self._last_state: Optional[CooldownState] = None

    def Init(self) -> bool:
        try:
            self._game_state_reader = self.node.create_reader(
                "/lol/game_state", queue_size=4
            )
            self._events_reader = self.node.create_reader(
                "/lol/events", queue_size=64
            )
            self._cooldown_writer = self.node.create_writer("/lol/cooldowns")
            self._players.clear()
            self._processed_event_ids.clear()
            logger.info("CooldownTracker initialized")
            return True
        except Exception as exc:
            logger.error("CooldownTracker Init failed: %s", exc)
            return False

    def Proc(self) -> bool:
        try:
            game_state = (
                self._game_state_reader.get_latest()
                if self._game_state_reader else None
            )

            if game_state and hasattr(game_state, "game_time"):
                self._current_game_time = game_state.game_time

            # Initialize player tracking from game state
            if game_state:
                self._update_players_from_state(game_state)

            # Process spell usage events
            self._process_events()

            # Compute state
            state = self._compute_cooldown_state()
            self._last_state = state

            if self._cooldown_writer:
                self._cooldown_writer.write(state)

            return True
        except Exception as exc:
            logger.error("CooldownTracker Proc error: %s", exc)
            return False

    def _update_players_from_state(self, game_state: Any) -> None:
        """Initialize or update player cooldown tracking from game state."""
        players_data = getattr(game_state, "players", None)
        if not players_data:
            return

        player_list = (
            players_data if isinstance(players_data, (list, tuple))
            else players_data.values() if isinstance(players_data, dict)
            else []
        )

        for p in player_list:
            name = getattr(p, "name", None) or getattr(p, "summoner_name", "")
            if not name:
                continue

            if name not in self._players:
                champion = getattr(p, "champion", "") or getattr(p, "champion_name", "")
                team = getattr(p, "team", "unknown")
                team_str = (
                    "ally" if str(team).upper() in ("ALLY", "ORDER", "BLUE")
                    else "enemy"
                )

                # Get summoner spells
                spell1_name = getattr(p, "spell1", "") or getattr(p, "summoner_spell_1", "Flash")
                spell2_name = getattr(p, "spell2", "") or getattr(p, "summoner_spell_2", "Ignite")

                player_cd = PlayerCooldowns(
                    player_name=name,
                    team=team_str,
                    champion_name=str(champion),
                )

                # Initialize summoner spells
                base1 = _SUMMONER_SPELL_COOLDOWNS.get(str(spell1_name), 300.0)
                base2 = _SUMMONER_SPELL_COOLDOWNS.get(str(spell2_name), 300.0)

                player_cd.summoner_1 = SpellCooldown(
                    spell_name=str(spell1_name),
                    cooldown_type=CooldownType.SUMMONER_SPELL_1,
                    base_cooldown_s=base1,
                    effective_cooldown_s=_compute_effective_cooldown(
                        base1, is_summoner_spell=True
                    ),
                    confidence=(
                        CooldownConfidence.CONFIRMED if team_str == "ally"
                        else CooldownConfidence.ASSUMED
                    ),
                )
                player_cd.summoner_2 = SpellCooldown(
                    spell_name=str(spell2_name),
                    cooldown_type=CooldownType.SUMMONER_SPELL_2,
                    base_cooldown_s=base2,
                    effective_cooldown_s=_compute_effective_cooldown(
                        base2, is_summoner_spell=True
                    ),
                    confidence=(
                        CooldownConfidence.CONFIRMED if team_str == "ally"
                        else CooldownConfidence.ASSUMED
                    ),
                )

                self._players[name] = player_cd

            # Update level and ability haste
            player_cd = self._players[name]
            level = getattr(p, "level", 1) or 1
            player_cd.champion_level = level

            haste = getattr(p, "ability_haste", 0) or 0
            player_cd.ability_haste = haste

            # Update ultimate cooldown
            ult_base = _get_ult_base_cooldown(
                player_cd.champion_name, level
            )
            if player_cd.ultimate is None:
                player_cd.ultimate = SpellCooldown(
                    spell_name=f"{player_cd.champion_name}_R",
                    cooldown_type=CooldownType.ULTIMATE,
                    base_cooldown_s=ult_base,
                    effective_cooldown_s=_compute_effective_cooldown(
                        ult_base, ability_haste=haste
                    ),
                    confidence=(
                        CooldownConfidence.CONFIRMED
                        if player_cd.team == "ally"
                        else CooldownConfidence.ASSUMED
                    ),
                )
            else:
                player_cd.ultimate.base_cooldown_s = ult_base
                player_cd.ultimate.effective_cooldown_s = (
                    _compute_effective_cooldown(ult_base, ability_haste=haste)
                )

    def _process_events(self) -> None:
        """Process spell usage events to start cooldown timers."""
        if not self._events_reader:
            return

        events = self._events_reader.get_all_pending()
        if not events:
            return

        for event_list in events:
            if not isinstance(event_list, list):
                event_list = [event_list]

            for event in event_list:
                event_id = getattr(event, "event_id", None) or id(event)
                if event_id in self._processed_event_ids:
                    continue
                self._processed_event_ids.add(event_id)

                event_type_str = str(
                    getattr(event, "event_type", getattr(event, "type", ""))
                ).upper()

                player_name = (
                    getattr(event, "caster", "")
                    or getattr(event, "killer_name", "")
                    or getattr(event, "player_name", "")
                )

                if "SUMMONER" in event_type_str or "SPELL" in event_type_str:
                    spell_name = str(getattr(event, "spell_name", ""))
                    self._on_summoner_spell_used(
                        str(player_name), spell_name
                    )
                elif "ULTIMATE" in event_type_str or "ULT" in event_type_str:
                    self._on_ultimate_used(str(player_name))

        # Trim
        if len(self._processed_event_ids) > 5000:
            trimmed = sorted(self._processed_event_ids)[-2500:]
            self._processed_event_ids = set(trimmed)

    def _on_summoner_spell_used(
        self, player_name: str, spell_name: str,
    ) -> None:
        """Handle summoner spell usage."""
        player = self._players.get(player_name)
        if not player:
            return

        for spell in [player.summoner_1, player.summoner_2]:
            if spell and spell.spell_name == spell_name:
                spell.mark_used(self._current_game_time)
                logger.debug(
                    "%s used %s, CD=%.0fs",
                    player_name, spell_name,
                    spell.effective_cooldown_s,
                )
                break

    def _on_ultimate_used(self, player_name: str) -> None:
        """Handle ultimate ability usage."""
        player = self._players.get(player_name)
        if not player or not player.ultimate:
            return

        player.ultimate.mark_used(self._current_game_time)
        logger.debug(
            "%s used ultimate, CD=%.0fs",
            player_name, player.ultimate.effective_cooldown_s,
        )

    def _compute_cooldown_state(self) -> CooldownState:
        """Compute aggregate cooldown state."""
        gt = self._current_game_time

        ally_summary = TeamCooldownSummary()
        enemy_summary = TeamCooldownSummary()

        for player in self._players.values():
            summary = (
                ally_summary if player.team == "ally" else enemy_summary
            )

            # Flash tracking
            for spell in [player.summoner_1, player.summoner_2]:
                if spell and spell.spell_name == "Flash":
                    summary.flashes_total += 1
                    rem = spell.remaining_s(gt)
                    if rem <= 0:
                        summary.flashes_available += 1
                    elif (summary.next_flash_available_s == 0
                          or rem < summary.next_flash_available_s):
                        summary.next_flash_available_s = rem

            # Ultimate tracking
            if player.ultimate:
                summary.ults_total += 1
                rem = player.ultimate.remaining_s(gt)
                if rem <= 0:
                    summary.ults_available += 1
                elif (summary.next_ult_available_s == 0
                      or rem < summary.next_ult_available_s):
                    summary.next_ult_available_s = rem

        # Engagement windows
        windows = self._find_engagement_windows(gt)

        return CooldownState(
            timestamp_ns=time.time_ns(),
            game_time_s=gt,
            players=self._players,
            ally_summary=ally_summary,
            enemy_summary=enemy_summary,
            engagement_windows=windows,
        )

    def _find_engagement_windows(
        self, game_time_s: float,
    ) -> List[Dict[str, Any]]:
        """Identify favorable engagement windows based on cooldowns."""
        windows = []

        # Window: enemy flashes down
        enemy_no_flash = []
        for player in self._players.values():
            if player.team != "enemy":
                continue
            for spell in [player.summoner_1, player.summoner_2]:
                if (spell and spell.spell_name == "Flash"
                        and spell.remaining_s(game_time_s) > 0):
                    enemy_no_flash.append({
                        "player": player.player_name,
                        "remaining_s": round(spell.remaining_s(game_time_s)),
                    })

        if enemy_no_flash:
            windows.append({
                "type": "enemy_flash_down",
                "targets": enemy_no_flash,
                "recommendation": "Consider engaging — enemy flash(es) on CD",
            })

        # Window: enemy ultimates down
        enemy_no_ult = []
        for player in self._players.values():
            if player.team != "enemy":
                continue
            if (player.ultimate
                    and player.ultimate.remaining_s(game_time_s) > 10.0):
                enemy_no_ult.append({
                    "player": player.player_name,
                    "champion": player.champion_name,
                    "remaining_s": round(
                        player.ultimate.remaining_s(game_time_s)
                    ),
                })

        if len(enemy_no_ult) >= 2:
            windows.append({
                "type": "enemy_ults_down",
                "targets": enemy_no_ult,
                "recommendation": (
                    f"{len(enemy_no_ult)} enemy ultimates on cooldown"
                ),
            })

        return windows

    # ─── Query API ───────────────────────────────────────────────────────

    def get_player_cooldowns(
        self, player_name: str,
    ) -> Optional[PlayerCooldowns]:
        return self._players.get(player_name)

    def get_cooldown_state(self) -> Optional[CooldownState]:
        return self._last_state

    def is_flash_available(
        self, player_name: str,
    ) -> Optional[bool]:
        """Quick check: is this player's flash available?"""
        player = self._players.get(player_name)
        if not player:
            return None
        rem = player.flash_remaining(self._current_game_time)
        return rem is not None and rem <= 0

    def status(self) -> Dict[str, Any]:
        base = super().status()
        base.update({
            "tracked_players": len(self._players),
            "game_time": self._current_game_time,
        })
        if self._last_state:
            base["ally_summary"] = {
                "flashes": f"{self._last_state.ally_summary.flashes_available}/{self._last_state.ally_summary.flashes_total}",
                "ults": f"{self._last_state.ally_summary.ults_available}/{self._last_state.ally_summary.ults_total}",
            }
        return base
