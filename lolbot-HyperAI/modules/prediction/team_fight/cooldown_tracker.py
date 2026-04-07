"""
CooldownTracker — Summoner spell and ultimate cooldown estimation.
===================================================================
lolbot-HyperAI · Prediction Layer

Tracks Flash, Teleport, and ultimate cooldowns by observing events.
Provides estimated availability for teamfight prediction scoring.

Architecture position:
    modules/prediction/team_fight/cooldown_tracker.py   ← YOU ARE HERE
    ├─ Input: GameEvent stream (kill events contain summoner spell usage hints)
    ├─ Input: GameSnapshot (player levels for ult cooldown scaling)
    ├─ Output: CooldownState per player
    └─ Consumed by: TeamfightPredictor for fight scoring

Design notes:
    - Flash: 300s cooldown (with Cosmic Insight: 285s)
    - Teleport: 360s cooldown
    - Ultimate: champion-dependent, estimated from level
    - We cannot directly observe spell usage from LCU API during game,
      so we infer from death events (flash often used before death)
      and maintain conservative estimates
"""

from __future__ import annotations
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from modules.common.adapters.game_messages import (
    GameEvent, GameSnapshot, PlayerState, TeamSide,
)

_FLASH_CD_S = 300.0
_TP_CD_S = 360.0
_IGNITE_CD_S = 180.0
_EXHAUST_CD_S = 210.0
_HEAL_CD_S = 240.0
_BARRIER_CD_S = 180.0

_SPELL_COOLDOWNS: Dict[str, float] = {
    "Flash": _FLASH_CD_S,
    "Teleport": _TP_CD_S,
    "Ignite": _IGNITE_CD_S,
    "Exhaust": _EXHAUST_CD_S,
    "Heal": _HEAL_CD_S,
    "Barrier": _BARRIER_CD_S,
    "Smite": 90.0,
    "Ghost": 210.0,
    "Cleanse": 210.0,
}

# Base ultimate cooldowns by level bracket (rough estimate)
_ULT_BASE_CD_S = {
    6: 120.0, 7: 115.0, 8: 110.0, 9: 105.0, 10: 100.0,
    11: 90.0, 12: 85.0, 13: 80.0, 14: 75.0, 15: 70.0,
    16: 60.0, 17: 55.0, 18: 50.0,
}


@dataclass
class SpellCooldown:
    """Cooldown state for a single spell."""
    spell_name: str = ""
    base_cooldown_s: float = 0.0
    last_used_game_time: float = -9999.0
    is_known_used: bool = False

    def estimated_ready_time(self) -> float:
        if not self.is_known_used:
            return 0.0  # assume available
        return self.last_used_game_time + self.base_cooldown_s

    def is_likely_up(self, game_time: float) -> bool:
        return game_time >= self.estimated_ready_time()

    def time_remaining(self, game_time: float) -> float:
        return max(0.0, self.estimated_ready_time() - game_time)


@dataclass
class PlayerCooldowns:
    """All tracked cooldowns for a single player."""
    name: str = ""
    team: TeamSide = TeamSide.UNKNOWN
    spell_d: SpellCooldown = field(default_factory=SpellCooldown)
    spell_f: SpellCooldown = field(default_factory=SpellCooldown)
    ultimate: SpellCooldown = field(default_factory=lambda: SpellCooldown(
        spell_name="Ultimate", base_cooldown_s=100.0,
    ))

    @property
    def flash_up(self) -> bool:
        for spell in (self.spell_d, self.spell_f):
            if spell.spell_name == "Flash":
                return not spell.is_known_used or spell.is_likely_up(0)
        return True  # assume up if not Flash holder

    def to_dict(self, game_time: float) -> Dict[str, Any]:
        return {
            "name": self.name,
            "spell_d": {
                "name": self.spell_d.spell_name,
                "up": self.spell_d.is_likely_up(game_time),
                "remaining": round(self.spell_d.time_remaining(game_time), 0),
            },
            "spell_f": {
                "name": self.spell_f.spell_name,
                "up": self.spell_f.is_likely_up(game_time),
                "remaining": round(self.spell_f.time_remaining(game_time), 0),
            },
            "ult_up": self.ultimate.is_likely_up(game_time),
        }


class CooldownTracker:
    """Tracks estimated spell cooldowns for all players.

    Usage::
        tracker = CooldownTracker()
        tracker.update_from_snapshot(snapshot)
        tracker.record_spell_usage("PlayerName", "Flash", game_time)
        state = tracker.get_player("PlayerName")
    """

    def __init__(self) -> None:
        self._players: Dict[str, PlayerCooldowns] = {}

    def update_from_snapshot(self, snapshot: GameSnapshot) -> None:
        """Initialize/update player data from game snapshot."""
        for player in snapshot.all_players:
            if player.summoner_name not in self._players:
                cd = PlayerCooldowns(
                    name=player.summoner_name,
                    team=player.team,
                )
                # Set spell cooldowns
                d_name = player.spell_d
                f_name = player.spell_f
                cd.spell_d = SpellCooldown(
                    spell_name=d_name,
                    base_cooldown_s=_SPELL_COOLDOWNS.get(d_name, 300.0),
                )
                cd.spell_f = SpellCooldown(
                    spell_name=f_name,
                    base_cooldown_s=_SPELL_COOLDOWNS.get(f_name, 300.0),
                )
                self._players[player.summoner_name] = cd

            # Update ult cooldown estimate based on level
            pc = self._players[player.summoner_name]
            if player.level >= 6:
                pc.ultimate.base_cooldown_s = _ULT_BASE_CD_S.get(
                    player.level, 100.0
                )

    def record_death(self, player_name: str, game_time: float) -> None:
        """When a player dies, conservatively assume Flash was used.

        This is a heuristic: most players flash before dying.
        """
        pc = self._players.get(player_name)
        if pc is None:
            return
        for spell in (pc.spell_d, pc.spell_f):
            if spell.spell_name == "Flash" and spell.is_likely_up(game_time):
                spell.last_used_game_time = game_time
                spell.is_known_used = True

    def record_spell_usage(
        self, player_name: str, spell_name: str, game_time: float
    ) -> None:
        """Record explicit spell usage (if we can observe it)."""
        pc = self._players.get(player_name)
        if pc is None:
            return
        for spell in (pc.spell_d, pc.spell_f):
            if spell.spell_name == spell_name:
                spell.last_used_game_time = game_time
                spell.is_known_used = True
                return
        if spell_name == "Ultimate":
            pc.ultimate.last_used_game_time = game_time
            pc.ultimate.is_known_used = True

    def get_player(self, name: str) -> Optional[PlayerCooldowns]:
        return self._players.get(name)

    def team_flash_count(self, team: TeamSide, game_time: float) -> int:
        """Count how many players on a team likely have Flash available."""
        count = 0
        for pc in self._players.values():
            if pc.team != team:
                continue
            for spell in (pc.spell_d, pc.spell_f):
                if spell.spell_name == "Flash" and spell.is_likely_up(game_time):
                    count += 1
                    break
        return count

    def team_ult_count(self, team: TeamSide, game_time: float) -> int:
        """Count how many ultimates are likely available on a team."""
        count = 0
        for pc in self._players.values():
            if pc.team != team:
                continue
            if pc.ultimate.is_likely_up(game_time):
                count += 1
        return count

    def all_cooldowns(self, game_time: float) -> Dict[str, Dict[str, Any]]:
        return {
            name: pc.to_dict(game_time)
            for name, pc in self._players.items()
        }


# ═══════════════════════════════════════════════════════════════════════════
# Claude20: Extended cooldown tracker with team scoring and fight readiness
# ═══════════════════════════════════════════════════════════════════════════


@dataclass
class FightReadiness:
    """Team fight readiness assessment based on cooldowns.

    Claude20: Aggregates per-player cooldowns into a team-level
    fight readiness score for teamfight_predictor consumption.
    """
    team: TeamSide = TeamSide.UNKNOWN
    flash_available_count: int = 0
    ult_available_count: int = 0
    total_alive: int = 5
    readiness_score: float = 0.0
    limiting_factor: str = ""
    game_time: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "team": self.team.value if hasattr(self.team, 'value') else str(self.team),
            "flash_up": self.flash_available_count,
            "ult_up": self.ult_available_count,
            "alive": self.total_alive,
            "readiness": round(self.readiness_score, 3),
            "limiting": self.limiting_factor,
        }


@dataclass
class CooldownEvent:
    """Record of a cooldown state change.

    Claude20: For post-game analysis and accuracy tracking.
    """
    game_time: float
    player_name: str
    spell_name: str
    event_type: str  # "used", "ready", "death_inferred"
    estimated_ready_time: float = 0.0


class CooldownTrackerV2(CooldownTracker):
    """Extended cooldown tracker with fight readiness scoring.

    Claude20: Adds team-level fight readiness assessment, cooldown
    event history, and accuracy tracking. All existing CooldownTracker
    methods preserved.

    Usage::
        tracker = CooldownTrackerV2()
        tracker.update_from_snapshot(snapshot)
        readiness = tracker.assess_fight_readiness(TeamSide.BLUE, game_time)
        if readiness.readiness_score > 0.7:
            planning.recommend_engage()
    """

    def __init__(self) -> None:
        super().__init__()
        self._events: List[CooldownEvent] = []
        self._assessment_count: int = 0

    def record_death(self, player_name: str, game_time: float) -> None:
        """Record death with event logging."""
        super().record_death(player_name, game_time)
        self._events.append(CooldownEvent(
            game_time=game_time,
            player_name=player_name,
            spell_name="Flash",
            event_type="death_inferred",
        ))

    def record_spell_usage(
        self, player_name: str, spell_name: str, game_time: float,
    ) -> None:
        """Record spell usage with event logging."""
        super().record_spell_usage(player_name, spell_name, game_time)
        self._events.append(CooldownEvent(
            game_time=game_time,
            player_name=player_name,
            spell_name=spell_name,
            event_type="used",
        ))

    def assess_fight_readiness(
        self,
        team: TeamSide,
        game_time: float,
        alive_players: Optional[List[str]] = None,
    ) -> FightReadiness:
        """Assess a team's readiness for a team fight.

        Claude20: Computes a composite score based on flash availability,
        ultimate availability, and alive count.

        Args:
            team: Which team to assess.
            game_time: Current game time.
            alive_players: Optional list of alive player names.

        Returns:
            FightReadiness with composite score.
        """
        self._assessment_count += 1

        flash_up = self.team_flash_count(team, game_time)
        ult_up = self.team_ult_count(team, game_time)

        # Count alive
        total_alive = 5
        if alive_players is not None:
            team_players = [
                name for name, pc in self._players.items()
                if pc.team == team
            ]
            total_alive = sum(
                1 for p in team_players if p in alive_players
            )

        # Composite score: weighted average of available resources
        # Flash: 0.3 weight, Ults: 0.4 weight, Alive: 0.3 weight
        flash_ratio = flash_up / max(total_alive, 1)
        ult_ratio = ult_up / max(total_alive, 1)
        alive_ratio = total_alive / 5.0

        score = (flash_ratio * 0.3) + (ult_ratio * 0.4) + (alive_ratio * 0.3)

        # Determine limiting factor
        limiting = ""
        if alive_ratio < 0.6:
            limiting = f"Only {total_alive} alive"
        elif flash_ratio < 0.4:
            limiting = f"Only {flash_up} flashes up"
        elif ult_ratio < 0.4:
            limiting = f"Only {ult_up} ults up"

        return FightReadiness(
            team=team,
            flash_available_count=flash_up,
            ult_available_count=ult_up,
            total_alive=total_alive,
            readiness_score=min(1.0, score),
            limiting_factor=limiting,
            game_time=game_time,
        )

    def compare_readiness(
        self, game_time: float,
        blue_alive: Optional[List[str]] = None,
        red_alive: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Compare fight readiness between both teams.

        Claude20: Used by PredictionComponent to adjust teamfight prediction.
        """
        blue = self.assess_fight_readiness(TeamSide.BLUE, game_time, blue_alive)
        red = self.assess_fight_readiness(TeamSide.RED, game_time, red_alive)

        advantage = blue.readiness_score - red.readiness_score
        return {
            "blue": blue.to_dict(),
            "red": red.to_dict(),
            "advantage": round(advantage, 3),
            "advantage_team": "blue" if advantage > 0.1 else ("red" if advantage < -0.1 else "even"),
        }

    def get_upcoming_ready_events(
        self, game_time: float, window_s: float = 30.0,
    ) -> List[Dict[str, Any]]:
        """Get spells coming off cooldown in the next window_s seconds.

        Claude20: Used by planning to predict fight readiness changes.
        """
        upcoming: List[Dict[str, Any]] = []
        for name, pc in self._players.items():
            for spell in (pc.spell_d, pc.spell_f, pc.ultimate):
                if spell.is_known_used and not spell.is_likely_up(game_time):
                    ready_time = spell.estimated_ready_time()
                    if ready_time <= game_time + window_s:
                        upcoming.append({
                            "player": name,
                            "spell": spell.spell_name,
                            "ready_in_s": round(ready_time - game_time, 1),
                            "team": pc.team.value if hasattr(pc.team, 'value') else str(pc.team),
                        })
        upcoming.sort(key=lambda x: x["ready_in_s"])
        return upcoming

    def extended_stats(self) -> Dict[str, Any]:
        return {
            "players_tracked": len(self._players),
            "events_recorded": len(self._events),
            "assessments": self._assessment_count,
            "recent_events": [
                {"time": e.game_time, "player": e.player_name,
                 "spell": e.spell_name, "type": e.event_type}
                for e in self._events[-10:]
            ],
        }

    def reset(self) -> None:
        """Reset for new game."""
        self._players.clear()
        self._events.clear()
        self._assessment_count = 0
