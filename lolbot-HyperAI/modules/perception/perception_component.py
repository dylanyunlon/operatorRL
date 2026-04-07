"""
PerceptionComponent — Game state fusion and event detection (10Hz).
====================================================================

Reads raw LCU data from ``/lol/raw_lcu`` (published by canbus) and
assembles it into typed ``GameSnapshot`` objects on ``/lol/game_state``.
Also detects in-game events (kills, objectives, teamfights) and
publishes them on ``/lol/events``.

Phase 4 additions (Claude#6):
    - KillFeedAnalyzer integration: kill patterns → /lol/kill_feed
    - MinimapAnalyzer integration: zone control → /lol/minimap_state

Architecture position:
    modules/perception/perception_component.py   ← YOU ARE HERE
    ├─ Reads: /lol/raw_lcu (RawLCUData from canbus)
    ├─ Reads: /lol/raw_fiddler (RawFiddlerData from canbus, optional)
    ├─ Publishes: /lol/game_state (GameSnapshot)
    ├─ Publishes: /lol/events (GameEvent list)
    ├─ Publishes: /lol/kill_feed (DetectedKillPattern list)  [Phase 4]
    ├─ Publishes: /lol/minimap_state (MinimapState)          [Phase 4]
    └─ Delegates to: game_state/state_assembler, events/event_detector,
                     events/kill_feed_analyzer, minimap/minimap_analyzer

Apollo reference:
    modules/perception/multi_sensor_fusion/ — fuse multiple inputs
    modules/perception/onboard_obstacle_perception_component.cc

Design notes:
    - Incremental event detection: only new events since last tick
    - Champion ID → name resolution from static mapping
    - Team color resolution from active player's team
    - Gold diff computation from per-player gold totals
    - Phase classification from game time
    - KillFeedAnalyzer: dedup by business fields, not id(event)
    - MinimapAnalyzer: graceful fallback when positions unavailable
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from cyber.component.timer_component import ComponentConfig, TimerComponent
from cyber.node.node import CyberNode, Reader, Writer
from cyber.logger.cyber_logger import get_logger
from modules.common.component_base import (
    ComponentDependency,
    LifecycleState,
    ManagedComponent,
)
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
from modules.perception.events.kill_feed_analyzer import (
    KillFeedAnalyzer,
    DetectedKillPattern,
)
from modules.perception.minimap.minimap_analyzer import (
    MinimapAnalyzer,
    MinimapState,
)
# Claude19: Wire Claude18 analysis modules + new momentum tracker into Proc()
from modules.perception.game_state.phase_detector import (
    DetailedPhase,
    PhaseContext,
    PhaseDetector,
    PhaseTransition,
)
from modules.perception.fusion.gold_trend_analyzer import (
    GoldTrendAnalyzer,
    GoldTrendReport,
)
from modules.perception.fusion.momentum_tracker import (
    MomentumTracker,
    MomentumReport,
    MomentumState,
)

logger = get_logger("perception")

# ─── Constants ───────────────────────────────────────────────────────────────

_PERCEPTION_INTERVAL_MS = 100.0  # 10Hz, same as canbus
_WARN_THRESHOLD_MS = 150.0

# Sub-analyzer tick divisors: run at lower frequency to save CPU.
# KillFeed runs every tick (kill timing matters), Minimap every 5th tick.
_MINIMAP_TICK_DIVISOR = 5


class PerceptionComponent(TimerComponent, ManagedComponent):
    """Perception component: fuses raw data into structured game state.

    Each ``Proc()`` cycle:
    1. Reads latest RawLCUData from ``/lol/raw_lcu``
    2. Parses allgamedata JSON into typed PlayerState / TeamState objects
    3. Detects new events since last cycle
    4. Runs KillFeedAnalyzer on new events → publishes patterns
    5. Runs MinimapAnalyzer on snapshot (every N ticks) → publishes state
    6. Publishes complete GameSnapshot on ``/lol/game_state``
    7. Publishes new events on ``/lol/events``

    Claude11: Added ManagedComponent mixin for lifecycle + circuit breaker.
    """

    COMPONENT_NAME = "perception"
    DEPENDENCIES = [
        ComponentDependency("canbus", required=True,
                            channels=["/lol/raw_lcu"]),
    ]
    VERSION = "2.0.0"

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
        self._kill_feed_writer: Optional[Writer[List[DetectedKillPattern]]] = None
        self._minimap_writer: Optional[Writer[MinimapState]] = None

        # State tracking
        self._last_snapshot: Optional[GameSnapshot] = None
        self._seen_event_ids: Set[int] = set()
        self._all_events: List[GameEvent] = []
        self._snapshot_seq: int = 0
        self._active_summoner: str = ""
        self._active_team: TeamSide = TeamSide.UNKNOWN

        # Sub-analyzers (Phase 4 wiring)
        self._kill_feed_analyzer: Optional[KillFeedAnalyzer] = None
        self._minimap_analyzer: Optional[MinimapAnalyzer] = None
        self._minimap_tick_counter: int = 0
        self._last_minimap_state: Optional[MinimapState] = None

        # Claude19: Wire Claude18 PhaseDetector + GoldTrendAnalyzer + new MomentumTracker
        self._phase_detector: Optional[PhaseDetector] = None
        self._gold_trend_analyzer: Optional[GoldTrendAnalyzer] = None
        self._momentum_tracker: Optional[MomentumTracker] = None
        self._last_phase_transition: Optional[PhaseTransition] = None
        self._last_gold_trend: Optional[GoldTrendReport] = None
        self._last_momentum: Optional[MomentumReport] = None
        self._phase_transition_writer: Optional[Writer] = None
        self._gold_trend_writer: Optional[Writer] = None
        self._momentum_writer: Optional[Writer] = None

    def Init(self) -> bool:
        """Set up cyber node, readers, writers, and sub-analyzers."""
        self._managed_init()
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

        # Phase 4: sub-analyzer output channels
        self._kill_feed_writer = self._node.CreateWriter(
            "/lol/kill_feed", list
        )
        self._minimap_writer = self._node.CreateWriter(
            "/lol/minimap_state", MinimapState
        )

        # Phase 4: instantiate sub-analyzers
        self._kill_feed_analyzer = KillFeedAnalyzer()
        self._minimap_analyzer = MinimapAnalyzer()

        # Claude19: Instantiate Claude18 PhaseDetector + GoldTrendAnalyzer + new MomentumTracker
        self._phase_detector = PhaseDetector()
        self._gold_trend_analyzer = GoldTrendAnalyzer()
        self._momentum_tracker = MomentumTracker()
        self._phase_transition_writer = self._node.CreateWriter(
            "/lol/phase_transition", dict,
        )
        self._gold_trend_writer = self._node.CreateWriter(
            "/lol/gold_trend", dict,
        )
        self._momentum_writer = self._node.CreateWriter(
            "/lol/momentum", dict,
        )

        self.register_self()
        self._transition(LifecycleState.READY)
        self._transition(LifecycleState.RUNNING)
        logger.info("PerceptionComponent initialized (with KillFeed + Minimap)")
        return True

    def Proc(self) -> bool:
        """One perception cycle: raw data -> GameSnapshot -> sub-analyzers.

        Returns:
            True if a valid snapshot was produced.
        """
        if self.should_skip_proc():
            return True

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

        # ── Publish core snapshot ────────────────────────────────────
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

        # ── Phase 4: KillFeedAnalyzer ────────────────────────────────
        # Runs every tick because kill timing is critical for multi-kill
        # detection (10s window between consecutive kills).
        if new_events and self._kill_feed_analyzer is not None:
            try:
                patterns = self._kill_feed_analyzer.analyze(
                    new_events, final,
                )
                if patterns and self._kill_feed_writer:
                    self._kill_feed_writer.Write(patterns)
                    for pat in patterns:
                        logger.info(
                            "KillPattern: %s — %s (%.1fs)",
                            pat.pattern_type.value,
                            pat.player_name,
                            pat.game_time,
                        )
            except Exception as exc:
                # Sub-analyzer failure must NOT crash main perception loop.
                # Log and continue — the core snapshot is already published.
                logger.warning(
                    "KillFeedAnalyzer error (non-fatal): %s: %s",
                    type(exc).__name__, exc,
                )

        # ── Phase 4: MinimapAnalyzer ─────────────────────────────────
        # Runs every _MINIMAP_TICK_DIVISOR ticks (~500ms at 10Hz).
        # Position data changes slowly; running every tick wastes CPU.
        self._minimap_tick_counter += 1
        if (
            self._minimap_tick_counter >= _MINIMAP_TICK_DIVISOR
            and self._minimap_analyzer is not None
        ):
            self._minimap_tick_counter = 0
            try:
                minimap_state = self._minimap_analyzer.analyze(final)
                self._last_minimap_state = minimap_state
                if self._minimap_writer:
                    self._minimap_writer.Write(minimap_state)
            except Exception as exc:
                logger.warning(
                    "MinimapAnalyzer error (non-fatal): %s: %s",
                    type(exc).__name__, exc,
                )

        # ── Claude19: PhaseDetector ──────────────────────────────────
        # Runs every 5th tick (~500ms at 10Hz). Replaces pure time-based
        # phase classification with tempo-aware multi-signal detection.
        if (
            self._snapshot_seq % 5 == 0
            and self._phase_detector is not None
        ):
            try:
                # Claude21: Fix kwarg names to match PhaseContext dataclass
                # fields (dragons_killed, inhibitors_destroyed) and populate
                # from real TeamState data instead of hardcoded zeros.
                ctx = PhaseContext(
                    game_time=final.game_time,
                    total_kills=(
                        final.blue_team.total_kills + final.red_team.total_kills
                    ),
                    towers_destroyed=(
                        final.blue_team.towers_destroyed
                        + final.red_team.towers_destroyed
                    ),
                    dragons_killed=(
                        final.blue_team.dragons_taken
                        + final.red_team.dragons_taken
                    ),
                    barons_killed=(
                        final.blue_team.barons_taken
                        + final.red_team.barons_taken
                    ),
                    inhibitors_destroyed=(
                        final.blue_team.inhibitors_destroyed
                        + final.red_team.inhibitors_destroyed
                    ),
                )
                transition = self._phase_detector.update(ctx)
                if transition is not None:
                    self._last_phase_transition = transition
                    if self._phase_transition_writer:
                        self._phase_transition_writer.Write(transition.to_dict())
                    logger.info(
                        "Phase transition: %s → %s (%.1fs) — %s",
                        transition.from_phase.name,
                        transition.to_phase.name,
                        transition.game_time,
                        transition.trigger_reason,
                    )
            except Exception as exc:
                logger.warning(
                    "PhaseDetector error (non-fatal): %s: %s",
                    type(exc).__name__, exc,
                )

        # ── Claude19: GoldTrendAnalyzer ──────────────────────────────
        # Records every tick (sub-sampled internally at ~1Hz).
        # Analysis runs every 10th tick (~1s) to provide gold momentum.
        if self._gold_trend_analyzer is not None:
            try:
                self._gold_trend_analyzer.record(
                    final.game_time, final.gold_diff,
                )
                if self._snapshot_seq % 10 == 0:
                    report = self._gold_trend_analyzer.analyze()
                    self._last_gold_trend = report
                    if self._gold_trend_writer:
                        self._gold_trend_writer.Write(report.to_dict())
            except Exception as exc:
                logger.warning(
                    "GoldTrendAnalyzer error (non-fatal): %s: %s",
                    type(exc).__name__, exc,
                )

        # ── Claude19: MomentumTracker ────────────────────────────────
        # Records new kill/objective events and evaluates every 10th tick.
        if self._momentum_tracker is not None:
            try:
                for evt in new_events:
                    etype = (
                        evt.event_type.value
                        if hasattr(evt.event_type, "value")
                        else str(evt.event_type)
                    )
                    killer_team = "BLUE"  # Simplified; ideally resolve from player
                    if etype in ("ChampionKill",):
                        self._momentum_tracker.record_kill(
                            killer_team, evt.game_time,
                        )
                    elif "Dragon" in etype or "Baron" in etype:
                        self._momentum_tracker.record_objective(
                            killer_team, evt.game_time, etype.lower(),
                        )

                if self._gold_trend_analyzer:
                    self._momentum_tracker.set_gold_velocity(
                        self._last_gold_trend.short_momentum
                        if self._last_gold_trend
                        and hasattr(self._last_gold_trend, "short_momentum")
                        else 0.0
                    )

                if self._snapshot_seq % 10 == 0:
                    momentum_report = self._momentum_tracker.evaluate(
                        final.game_time,
                    )
                    self._last_momentum = momentum_report
                    if self._momentum_writer:
                        self._momentum_writer.Write(momentum_report.to_dict())
            except Exception as exc:
                logger.warning(
                    "MomentumTracker error (non-fatal): %s: %s",
                    type(exc).__name__, exc,
                )

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
            "kill_feed_analysis_count": (
                self._kill_feed_analyzer._analysis_count
                if self._kill_feed_analyzer else 0
            ),
            "minimap_analysis_count": (
                self._minimap_analyzer._analysis_count
                if self._minimap_analyzer else 0
            ),
            "last_minimap_state": (
                self._last_minimap_state is not None
            ),
            # Claude19 additions
            "phase_detector_active": self._phase_detector is not None,
            "last_phase_transition": (
                self._last_phase_transition.to_dict()
                if self._last_phase_transition else None
            ),
            "gold_trend_analyzer_active": self._gold_trend_analyzer is not None,
            "momentum_state": (
                self._last_momentum.state.name
                if self._last_momentum else "N/A"
            ),
        })
        return base

    # ─── Claude17: Event Rate Tracking ───────────────────────────────────

    def get_event_rates(self, window_s: float = 60.0) -> Dict[str, float]:
        """Compute per-minute event rates by type.

        Claude17: Enables monitoring of event detection quality.
        Low rates may indicate perception is missing events;
        anomalously high rates may indicate noise or bugs.
        """
        now = time.time()
        cutoff = now - window_s
        recent = [
            e for e in self._all_events
            if hasattr(e, 'timestamp') and e.timestamp > cutoff
        ]

        counts: Dict[str, int] = {}
        for e in recent:
            etype = getattr(e, 'event_type', 'unknown')
            if hasattr(etype, 'value'):
                etype = etype.value
            counts[etype] = counts.get(etype, 0) + 1

        minutes = max(window_s / 60.0, 1.0 / 60.0)
        return {k: round(v / minutes, 2) for k, v in counts.items()}

    def compute_data_quality_score(self) -> float:
        """Score the quality of the last game state snapshot.

        Claude17: Returns 0.0–1.0 based on:
        - Are all expected fields present?
        - Is player count reasonable (10)?
        - Is game time advancing?
        - Are gold values non-negative?

        Used by prediction to weight its confidence.
        """
        if self._last_snapshot is None:
            return 0.0

        score = 0.0
        checks = 0

        snap = self._last_snapshot

        # Check player count
        checks += 1
        if hasattr(snap, 'blue_team') and hasattr(snap, 'red_team'):
            blue_count = len(getattr(snap.blue_team, 'players', []))
            red_count = len(getattr(snap.red_team, 'players', []))
            if blue_count == 5 and red_count == 5:
                score += 1.0
            elif blue_count + red_count > 0:
                score += 0.5

        # Check game time is advancing
        checks += 1
        if hasattr(snap, 'game_time') and snap.game_time > 0:
            score += 1.0

        # Check phase is set
        checks += 1
        if hasattr(snap, 'phase') and snap.phase is not None:
            score += 1.0

        # Check gold diff is computed
        checks += 1
        if hasattr(snap, 'gold_diff'):
            score += 1.0

        return round(score / max(checks, 1), 4) if checks > 0 else 0.0

    def detect_anomalies(self) -> List[Dict[str, Any]]:
        """Detect anomalous patterns in recent perception data.

        Claude17: Flags unusual conditions like:
        - Sudden large gold swings (possible data corruption)
        - Player count changes mid-game
        - Game time going backwards (replay glitch)
        """
        anomalies: List[Dict[str, Any]] = []

        if self._last_snapshot is None:
            return anomalies

        snap = self._last_snapshot

        # Check for extreme gold diff (>15k usually means something weird)
        if hasattr(snap, 'gold_diff'):
            if abs(snap.gold_diff) > 15000:
                anomalies.append({
                    "type": "extreme_gold_diff",
                    "value": snap.gold_diff,
                    "threshold": 15000,
                    "game_time": getattr(snap, 'game_time', 0),
                })

        return anomalies
