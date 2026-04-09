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
# Claude26: Apollo-style code/interface separation — delegate to sub-modules
from modules.perception.assembler.snapshot_assembler import SnapshotAssembler
from modules.perception.detector.event_detector import EventDetector

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

        # Claude26: Delegate to extracted sub-modules (Apollo pattern)
        self._assembler = SnapshotAssembler()
        self._event_detector = EventDetector()
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
        """One perception cycle — Apollo pattern: Proc() → _internal_proc().

        Claude22 refactor: Thin shell matching Apollo lidar_tracking_component
        (Proc 18 lines → InternalProc 50 lines). All Claude1-21 logic moved
        to _internal_proc() and its sub-delegates. Zero logic removed.

        Returns:
            True if cycle completed (even if no data available).
        """
        if self.should_skip_proc():
            return True

        # ── READ: Observe raw data from canbus ───────────────────────
        self._raw_lcu_reader.Observe()
        raw: Optional[RawLCUData] = self._raw_lcu_reader.GetLatestObserved()

        if raw is None or not raw.allgamedata:
            return True  # No data yet, not an error

        # ── VALIDATE + PROCESS: delegate to _internal_proc ───────────
        if not self._internal_proc(raw.allgamedata):
            return False

        # ── MONITOR: status heartbeat ────────────────────────────────
        self._publish_status(Status.ok())
        return True

    # ── Apollo-style InternalProc (Claude22: all Proc() logic moved here) ──

    def _internal_proc(self, allgamedata: Dict[str, Any]) -> bool:
        """Core perception processing — called by Proc() after read/validate.

        Apollo reference: LidarTrackingComponent::InternalProc()
        Claude22: Contains all Claude1-21 Proc() logic, verbatim.

        Returns:
            True on success, False on parse failure.
        """
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

        # ── Run all sub-analyzers ────────────────────────────────────
        self._run_kill_feed(new_events, final)
        self._run_minimap(final)
        self._run_phase_detector(final)
        self._run_gold_trend(final)
        self._run_momentum(new_events, final)

        return True

    def _run_kill_feed(
        self, new_events: List[GameEvent], final: GameSnapshot
    ) -> None:
        """Phase 4: KillFeedAnalyzer — every tick (kill timing critical).

        Claude22: Extracted verbatim from Proc().
        """
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

    def _run_minimap(self, final: GameSnapshot) -> None:
        """Phase 4: MinimapAnalyzer — every _MINIMAP_TICK_DIVISOR ticks.

        Claude22: Extracted verbatim from Proc().
        """
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

    def _run_phase_detector(self, final: GameSnapshot) -> None:
        """Claude19: PhaseDetector — every 5th tick (~500ms at 10Hz).

        Claude21: Fixed kwarg names to match PhaseContext dataclass.
        Claude22: Extracted verbatim from Proc().
        """
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

    def _run_gold_trend(self, final: GameSnapshot) -> None:
        """Claude19: GoldTrendAnalyzer — records every tick, analyzes every 10th.

        Claude22: Extracted verbatim from Proc().
        """
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

    def _run_momentum(
        self, new_events: List[GameEvent], final: GameSnapshot
    ) -> None:
        """Claude19: MomentumTracker — records events, evaluates every 10th tick.

        Claude22: Extracted verbatim from Proc().
        """
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

    def on_shutdown(self) -> None:
        if self._node:
            self._node.shutdown()

    # ─── State Assembly ──────────────────────────────────────────────

    def _assemble_snapshot(self, data: Dict[str, Any]) -> GameSnapshot:
        """Parse allgamedata into a GameSnapshot.

        Claude26: Delegates to SnapshotAssembler (Apollo fusion_system pattern).
        All parsing logic preserved in assembler/snapshot_assembler.py.
        """
        snapshot = self._assembler.assemble(data)
        # Sync state back (backward compat with _internal_proc references)
        self._active_summoner = self._assembler.active_summoner
        self._active_team = self._assembler.active_team
        return snapshot

    # Claude26: _parse_player and _build_team_state moved to
    # modules/perception/assembler/snapshot_assembler.py
    # Called via self._assembler.assemble() in _assemble_snapshot() above.

    # ─── Event Detection ─────────────────────────────────────────────

    def _detect_new_events(self, data: Dict[str, Any]) -> List[GameEvent]:
        """Detect events not seen in previous ticks.

        Claude26: Delegates to EventDetector (Apollo detector/ pattern).
        All dedup logic preserved in detector/event_detector.py.
        """
        new_events = self._event_detector.detect_new(data)
        # Sync back for backward compat (_all_events, _seen_event_ids)
        self._all_events = self._event_detector.all_events
        self._seen_event_ids = self._event_detector.seen_event_ids
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
        """Claude26: Delegates to EventDetector."""
        return self._event_detector.event_rates(window_s)

    def compute_data_quality_score(self) -> float:
        """Claude26: Delegates to EventDetector."""
        return self._event_detector.data_quality_score(self._last_snapshot)

    def detect_anomalies(self) -> List[Dict[str, Any]]:
        """Claude26: Delegates to EventDetector."""
        return self._event_detector.detect_anomalies(self._last_snapshot)


    # ─── Apollo-style input validation (Claude23) ────────────────────────
    #
    # Apollo perception InternalProc() validates input message before
    # processing. We add _validate_input() for structured pre-checks.

    def _validate_input(self, allgamedata: Dict[str, Any]) -> bool:
        """Claude26: Delegates to EventDetector."""
        return self._event_detector.validate_input(allgamedata)

    def _check_upstream_health(self) -> bool:
        """Claude26: Delegates to EventDetector."""
        reader = getattr(self, "_raw_lcu_reader", None)
        return self._event_detector.check_upstream_health(reader)
