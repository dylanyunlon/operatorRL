"""
PerceptionComponent — Game state fusion and event detection (10Hz).
====================================================================

Reads raw LCU data from ``/lol/raw_lcu`` (published by canbus) and
assembles it into typed ``GameSnapshot`` objects on ``/lol/game_state``.
Also detects in-game events (kills, objectives, teamfights) and
publishes them on ``/lol/events``.

This is the "eyes" of the system — analogous to Apollo's perception
module which fuses LiDAR + camera into obstacle lists.

Architecture position:
    modules/perception/perception_component.py   ← YOU ARE HERE
    ├─ Reads: /lol/raw_lcu (RawLCUData from canbus)
    ├─ Reads: /lol/raw_fiddler (RawFiddlerData from canbus, optional)
    ├─ Publishes: /lol/game_state (GameSnapshot)
    ├─ Publishes: /lol/events (GameEvent list)
    └─ Delegates to: game_state/state_assembler, events/event_detector

Apollo reference:
    modules/perception/multi_sensor_fusion/ — fuse multiple inputs
    modules/perception/onboard_obstacle_perception_component.cc

Design notes:
    - Incremental event detection: only new events since last tick
    - Champion ID → name resolution from static mapping
    - Team color resolution from active player's team
    - Gold diff computation from per-player gold totals
    - Phase classification from game time
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from cyber.component.timer_component import ComponentConfig, TimerComponent
from cyber.node.node import CyberNode, Reader, Writer
from cyber.logger.cyber_logger import get_logger
from modules.common.status.error_code import ErrorCode, Status, StatusMessage
from modules.common.adapters.game_messages import (
    EventType,
    GameEvent,
    GamePhase,
    GameSnapshot,
    PlayerAbilities,
    PlayerItems,
    PlayerScore,
    PlayerState,
    RawFiddlerData,
    RawLCUData,
    TeamSide,
    TeamState,
)

logger = get_logger("perception")

# ─── Constants ───────────────────────────────────────────────────────────────

_PERCEPTION_INTERVAL_MS = 100.0  # 10Hz, same as canbus
_WARN_THRESHOLD_MS = 150.0


class PerceptionComponent(TimerComponent):
    """Perception component: fuses raw data into structured game state.

    Each ``Proc()`` cycle:
    1. Reads latest RawLCUData from ``/lol/raw_lcu``
    2. Parses allgamedata JSON into typed PlayerState / TeamState objects
    3. Detects new events since last cycle
    4. Publishes complete GameSnapshot on ``/lol/game_state``
    5. Publishes new events on ``/lol/events``
    """

    def __init__(self) -> None:
        super().__init__(
            config=ComponentConfig(
                name="perception",
                interval_ms=_PERCEPTION_INTERVAL_MS,
                warn_threshold_ms=_WARN_THRESHOLD_MS,
            ),
        )
        self._node: Optional[CyberNode] = None

        # Readers
        self._raw_lcu_reader: Optional[Reader[RawLCUData]] = None
        self._raw_fiddler_reader: Optional[Reader[RawFiddlerData]] = None

        # Writers
        self._game_state_writer: Optional[Writer[GameSnapshot]] = None
        self._events_writer: Optional[Writer[List[GameEvent]]] = None
        self._status_writer: Optional[Writer[StatusMessage]] = None

        # State tracking
        self._last_snapshot: Optional[GameSnapshot] = None
        self._seen_event_ids: Set[int] = set()
        self._all_events: List[GameEvent] = []
        self._snapshot_seq: int = 0
        self._active_summoner: str = ""
        self._active_team: TeamSide = TeamSide.UNKNOWN

    def Init(self) -> bool:
        """Set up cyber node, readers, and writers."""
        logger.info("Initializing PerceptionComponent...")

        self._node = CyberNode("perception")

        # Subscribe to canbus outputs
        self._raw_lcu_reader = self._node.CreateReader(
            "/lol/raw_lcu", RawLCUData, pending_queue_size=16,
        )
        self._raw_fiddler_reader = self._node.CreateReader(
            "/lol/raw_fiddler", RawFiddlerData, pending_queue_size=8,
        )

        # Publishers
        self._game_state_writer = self._node.CreateWriter(
            "/lol/game_state", GameSnapshot
        )
        self._events_writer = self._node.CreateWriter(
            "/lol/events", list
        )
        self._status_writer = self._node.CreateWriter(
            "/lol/perception_status", StatusMessage
        )

        logger.info("PerceptionComponent initialized")
        return True

    def Proc(self) -> bool:
        """One perception cycle: raw data → GameSnapshot.

        Returns:
            True if a valid snapshot was produced.
        """
        # ── Read latest raw data ─────────────────────────────────────
        self._raw_lcu_reader.Observe()
        raw: Optional[RawLCUData] = self._raw_lcu_reader.GetLatestObserved()

        if raw is None or not raw.allgamedata:
            return True  # No data yet, not an error

        allgamedata = raw.allgamedata

        # ── Parse into structured types ──────────────────────────────
        try:
            snapshot = self._assemble_snapshot(allgamedata)
        except Exception as exc:
            logger.error("State assembly failed: %s: %s",
                         type(exc).__name__, exc)
            self._publish_status(Status.error(
                ErrorCode.PERCEPTION_STATE_PARSE_ERROR,
                f"Assembly failed: {exc}",
            ))
            return False

        # ── Detect new events ────────────────────────────────────────
        new_events = self._detect_new_events(allgamedata)

        # ── Build final snapshot with events ─────────────────────────
        self._snapshot_seq += 1
        final = GameSnapshot(
            game_time=snapshot.game_time,
            real_timestamp=time.time(),
            sequence=self._snapshot_seq,
            phase=snapshot.phase,
            game_mode=snapshot.game_mode,
            map_number=snapshot.map_number,
            blue_team=snapshot.blue_team,
            red_team=snapshot.red_team,
            active_player=snapshot.active_player,
            active_team=self._active_team,
            all_players=snapshot.all_players,
            new_events=tuple(new_events),
            all_events=tuple(self._all_events),
            gold_diff=snapshot.gold_diff,
        )

        # ── Publish ──────────────────────────────────────────────────
        if self._game_state_writer:
            self._game_state_writer.Write(final)

        if new_events and self._events_writer:
            self._events_writer.Write(new_events)
            for evt in new_events:
                if evt.is_objective:
                    logger.info(
                        "Objective: %s by %s at %.1fs",
                        evt.event_type.value, evt.killer, evt.game_time,
                    )

        self._last_snapshot = final
        self._publish_status(Status.ok())
        return True

    def on_shutdown(self) -> None:
        if self._node:
            self._node.shutdown()

    # ─── State Assembly ──────────────────────────────────────────────

    def _assemble_snapshot(self, data: Dict[str, Any]) -> GameSnapshot:
        """Parse allgamedata into a GameSnapshot.

        Extracts:
            - gameData → phase, mode, time
            - activePlayer → active player stats
            - allPlayers → per-player states, team aggregation
        """
        # Game metadata
        game_data = data.get("gameData", {})
        game_time = game_data.get("gameTime", 0.0)
        game_mode = game_data.get("gameMode", "CLASSIC")
        map_number = game_data.get("mapNumber", 11)
        phase = GamePhase.from_game_time(game_time)

        # Active player
        active_raw = data.get("activePlayer", {})
        active_name = active_raw.get("riotIdGameName",
                      active_raw.get("summonerName", ""))
        self._active_summoner = active_name

        # Parse all players
        all_players_raw = data.get("allPlayers", [])
        players: List[PlayerState] = []
        blue_players: List[PlayerState] = []
        red_players: List[PlayerState] = []

        for p_raw in all_players_raw:
            player = self._parse_player(p_raw, active_raw)
            players.append(player)
            if player.team == TeamSide.BLUE:
                blue_players.append(player)
            elif player.team == TeamSide.RED:
                red_players.append(player)

            if player.is_active_player:
                self._active_team = player.team

        # Build team states
        blue_team = self._build_team_state(TeamSide.BLUE, blue_players)
        red_team = self._build_team_state(TeamSide.RED, red_players)

        # Gold diff
        gold_diff = blue_team.total_gold - red_team.total_gold

        # Find active player state
        active_state = None
        for p in players:
            if p.is_active_player:
                active_state = p
                break

        return GameSnapshot(
            game_time=game_time,
            phase=phase,
            game_mode=game_mode,
            map_number=map_number,
            blue_team=blue_team,
            red_team=red_team,
            active_player=active_state,
            active_team=self._active_team,
            all_players=tuple(players),
            gold_diff=gold_diff,
        )

    def _parse_player(
        self,
        p_raw: Dict[str, Any],
        active_raw: Dict[str, Any],
    ) -> PlayerState:
        """Parse a single player from allPlayers array."""
        name = p_raw.get("riotIdGameName",
               p_raw.get("summonerName", ""))
        is_active = (name == self._active_summoner)

        # Scores
        scores_raw = p_raw.get("scores", {})
        scores = PlayerScore(
            kills=scores_raw.get("kills", 0),
            deaths=scores_raw.get("deaths", 0),
            assists=scores_raw.get("assists", 0),
            creep_score=scores_raw.get("creepScore", 0),
            ward_score=scores_raw.get("wardScore", 0.0),
        )

        # Items
        items_raw = p_raw.get("items", [])
        item_ids = tuple(item.get("itemID", 0) for item in items_raw)
        gold_spent = sum(item.get("price", 0) for item in items_raw)
        items = PlayerItems(item_ids=item_ids, gold_spent=gold_spent)

        # Abilities (only available for active player)
        abilities = PlayerAbilities()
        if is_active and active_raw:
            ab_raw = active_raw.get("abilities", {})
            abilities = PlayerAbilities(
                q_level=ab_raw.get("Q", {}).get("abilityLevel", 0),
                w_level=ab_raw.get("W", {}).get("abilityLevel", 0),
                e_level=ab_raw.get("E", {}).get("abilityLevel", 0),
                r_level=ab_raw.get("R", {}).get("abilityLevel", 0),
            )

        # Champion stats
        stats_raw = active_raw.get("championStats", {}) if is_active else {}

        # Summoner spells
        spells = p_raw.get("summonerSpells", {})
        spell_d = spells.get("summonerSpellOne", {}).get("displayName", "")
        spell_f = spells.get("summonerSpellTwo", {}).get("displayName", "")

        return PlayerState(
            summoner_name=name,
            champion_name=p_raw.get("championName", ""),
            team=TeamSide.from_riot(p_raw.get("team", "")),
            level=p_raw.get("level", 1),
            position=p_raw.get("position", ""),
            is_active_player=is_active,
            is_dead=p_raw.get("isDead", False),
            respawn_timer=p_raw.get("respawnTimer", 0.0),
            current_health=stats_raw.get("currentHealth", 0.0) if is_active
                           else 0.0,
            max_health=stats_raw.get("maxHealth", 0.0) if is_active
                       else 0.0,
            current_mana=stats_raw.get("resourceValue", 0.0) if is_active
                         else 0.0,
            max_mana=stats_raw.get("resourceMax", 0.0) if is_active
                     else 0.0,
            attack_damage=stats_raw.get("attackDamage", 0.0),
            ability_power=stats_raw.get("abilityPower", 0.0),
            armor=stats_raw.get("armor", 0.0),
            magic_resist=stats_raw.get("magicResist", 0.0),
            move_speed=stats_raw.get("moveSpeed", 0.0),
            current_gold=active_raw.get("currentGold", 0.0) if is_active
                         else 0.0,
            scores=scores,
            items=items,
            abilities=abilities,
            spell_d=spell_d,
            spell_f=spell_f,
        )

    def _build_team_state(
        self, side: TeamSide, players: List[PlayerState]
    ) -> TeamState:
        """Aggregate player states into a team state."""
        total_kills = sum(p.scores.kills for p in players)
        total_deaths = sum(p.scores.deaths for p in players)
        total_gold = sum(p.current_gold for p in players)

        return TeamState(
            side=side,
            players=tuple(players),
            total_kills=total_kills,
            total_deaths=total_deaths,
            total_gold=total_gold,
        )

    # ─── Event Detection ─────────────────────────────────────────────

    def _detect_new_events(self, data: Dict[str, Any]) -> List[GameEvent]:
        """Detect events that haven't been seen in previous ticks.

        Uses event ID for deduplication.
        """
        events_wrapper = data.get("events", {})
        raw_events = events_wrapper.get("Events", [])
        new_events: List[GameEvent] = []

        for evt_raw in raw_events:
            evt_id = evt_raw.get("EventID", 0)
            if evt_id in self._seen_event_ids:
                continue
            self._seen_event_ids.add(evt_id)

            # Map event name to our enum
            evt_name = evt_raw.get("EventName", "")
            try:
                evt_type = EventType(evt_name)
            except ValueError:
                evt_type = EventType.GAME_START  # unknown, default

            event = GameEvent(
                event_id=evt_id,
                event_type=evt_type,
                game_time=evt_raw.get("EventTime", 0.0),
                killer=evt_raw.get("KillerName", ""),
                victim=evt_raw.get("VictimName", ""),
                assisters=tuple(evt_raw.get("Assisters", [])),
            )
            new_events.append(event)
            self._all_events.append(event)

        return new_events

    # ─── Status ──────────────────────────────────────────────────────

    def _publish_status(self, status: Status) -> None:
        if self._status_writer:
            self._status_writer.Write(StatusMessage(
                status=status,
                sequence=self._snapshot_seq,
                source_component="perception",
                game_time=self._last_snapshot.game_time if self._last_snapshot else 0.0,
            ))

    def perception_status(self) -> Dict[str, Any]:
        base = self.status()
        base.update({
            "snapshot_count": self._snapshot_seq,
            "events_detected": len(self._all_events),
            "active_summoner": self._active_summoner,
            "active_team": self._active_team.name,
            "last_game_time": self._last_snapshot.game_time if self._last_snapshot else 0.0,
        })
        return base
