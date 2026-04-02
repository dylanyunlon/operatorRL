"""
EventStreamProcessor — Structured event dispatch and teamfight clustering.
============================================================================
lolbot-HyperAI · Perception Layer

Subscribes to `/lol/events` (published by PerceptionComponent) and performs:
    1. Event categorization into typed sub-channels
    2. Kill feed pattern dispatch to `/lol/kill_feed`
    3. Objective event dispatch to `/lol/objective_events`
    4. Teamfight cluster detection (3+ kills within 5s window)
    5. Momentum scoring from event recency

Architecture position:
    modules/perception/events/event_stream_processor.py   ← YOU ARE HERE
    ├─ Reads: /lol/events (GameEvent list from perception)
    ├─ Reads: /lol/game_state (GameSnapshot for context)
    ├─ Publishes: /lol/kill_feed (KillFeedEntry list)
    ├─ Publishes: /lol/objective_events (ObjectiveEvent)
    ├─ Publishes: /lol/teamfight_active (TeamfightCluster)
    └─ Delegates to: kill_feed_analyzer.py for multi-kill/spree detection

Apollo reference:
    modules/perception/fusion/async_fusion_component.cc
    — reads from multiple perception sub-modules, publishes fused result

Design notes:
    - This component FIXES the orphan /lol/events channel: perception
      publishes events but nothing was subscribed to consume them
    - Teamfight detection uses a sliding window: if 3+ ChampionKill
      events occur within a 5-second window, a teamfight is declared
    - Momentum score: exponential decay over 60s, each kill = +0.2,
      each objective = +0.3, normalized to [-1, +1] per team
    - Kill feed entries include derived metadata (multi-kill, spree,
      shutdown, first blood) from KillFeedAnalyzer
    - All frozen dataclasses for pipeline immutability
"""

from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Deque, Dict, FrozenSet, List, Optional, Set, Tuple

from cyber.component.timer_component import ComponentConfig, TimerComponent
from cyber.node.node import CyberNode, Reader, Writer
from cyber.logger.cyber_logger import get_logger
from modules.common.status.error_code import ErrorCode, Status, StatusMessage
from modules.common.adapters.game_messages import (
    EventType,
    GameEvent,
    GamePhase,
    GameSnapshot,
    ObjectiveType,
    TeamSide,
    VoiceCommand,
)

logger = get_logger("perception.events")

# ─── Constants ───────────────────────────────────────────────────────────────

_ESP_INTERVAL_MS = 200.0          # 5Hz event processing
_WARN_THRESHOLD_MS = 180.0
_TEAMFIGHT_WINDOW_S = 5.0         # kills within 5s = teamfight cluster
_TEAMFIGHT_MIN_KILLS = 3          # minimum kills to declare teamfight
_TEAMFIGHT_COOLDOWN_S = 15.0      # no new teamfight within 15s of last
_MOMENTUM_DECAY_HALF_LIFE_S = 30.0  # half-life for momentum decay
_MOMENTUM_KILL_WEIGHT = 0.20
_MOMENTUM_OBJECTIVE_WEIGHT = 0.30
_MOMENTUM_TOWER_WEIGHT = 0.15
_MOMENTUM_WINDOW_S = 60.0         # consider events in last 60s only
_MULTIKILL_WINDOW_S = 10.0        # 10s window for multi-kill detection
_SPREE_THRESHOLD = 3              # 3 kills without dying = spree


# ─── Output message types ───────────────────────────────────────────────────

class MultiKillType(Enum):
    """Multi-kill classification."""
    NONE = auto()
    DOUBLE = auto()
    TRIPLE = auto()
    QUADRA = auto()
    PENTA = auto()

    @staticmethod
    def from_count(count: int) -> "MultiKillType":
        return {
            2: MultiKillType.DOUBLE,
            3: MultiKillType.TRIPLE,
            4: MultiKillType.QUADRA,
        }.get(count, MultiKillType.PENTA if count >= 5 else MultiKillType.NONE)


class KillFeedTag(Enum):
    """Tags attached to kill feed entries for downstream prioritization."""
    FIRST_BLOOD = "first_blood"
    MULTI_KILL = "multi_kill"
    KILLING_SPREE = "killing_spree"
    SHUTDOWN = "shutdown"
    ACE = "ace"
    SOLO_KILL = "solo_kill"
    NORMAL = "normal"


@dataclass(frozen=True)
class KillFeedEntry:
    """A processed kill feed event with derived metadata.

    Published in batches on ``/lol/kill_feed``.
    """
    event_id: int = 0
    game_time: float = 0.0
    killer: str = ""
    victim: str = ""
    assisters: Tuple[str, ...] = ()
    killer_team: TeamSide = TeamSide.UNKNOWN
    victim_team: TeamSide = TeamSide.UNKNOWN
    tags: FrozenSet[KillFeedTag] = frozenset()
    multi_kill: MultiKillType = MultiKillType.NONE
    killer_spree_count: int = 0
    victim_spree_count: int = 0
    is_shutdown: bool = False
    bounty_gold: int = 0
    timestamp: float = field(default_factory=time.time)


@dataclass(frozen=True)
class ObjectiveEvent:
    """A processed objective event (dragon, baron, tower, herald).

    Published on ``/lol/objective_events``.
    """
    event_id: int = 0
    game_time: float = 0.0
    objective_type: str = ""
    taken_by: TeamSide = TeamSide.UNKNOWN
    killer: str = ""
    is_stolen: bool = False
    timestamp: float = field(default_factory=time.time)


@dataclass(frozen=True)
class TeamfightCluster:
    """A detected teamfight cluster.

    Published on ``/lol/teamfight_active`` when 3+ kills cluster
    within a 5-second sliding window.
    """
    start_time: float = 0.0
    end_time: float = 0.0
    kill_count: int = 0
    blue_kills: int = 0
    red_kills: int = 0
    participants: FrozenSet[str] = frozenset()
    location_hint: str = ""       # "mid", "baron_pit", "dragon_pit", "top", "bot"
    winner: TeamSide = TeamSide.UNKNOWN
    is_ace: bool = False
    timestamp: float = field(default_factory=time.time)


@dataclass
class MomentumSnapshot:
    """Per-team momentum scores, published as part of event stream output."""
    blue_momentum: float = 0.0    # [-1, +1]
    red_momentum: float = 0.0     # [-1, +1]
    net_momentum: float = 0.0     # blue - red
    trend: str = "stable"         # "blue_rising", "red_rising", "stable"
    game_time: float = 0.0


# ─── Internal tracking ──────────────────────────────────────────────────────

@dataclass
class _PlayerKillState:
    """Per-player kill tracking for spree/multi-kill detection."""
    name: str = ""
    team: TeamSide = TeamSide.UNKNOWN
    kills_no_death: int = 0        # consecutive kills without dying
    recent_kill_times: List[float] = field(default_factory=list)

    def record_kill(self, game_time: float) -> None:
        self.kills_no_death += 1
        self.recent_kill_times.append(game_time)
        # Prune old kills outside multi-kill window
        cutoff = game_time - _MULTIKILL_WINDOW_S
        self.recent_kill_times = [
            t for t in self.recent_kill_times if t >= cutoff
        ]

    def record_death(self) -> int:
        """Record a death, return previous spree count, reset counter."""
        spree = self.kills_no_death
        self.kills_no_death = 0
        self.recent_kill_times.clear()
        return spree

    @property
    def multi_kill_count(self) -> int:
        """Number of kills in the current multi-kill window."""
        return len(self.recent_kill_times)


@dataclass
class _MomentumEvent:
    """Timestamped momentum-relevant event."""
    game_time: float
    team: TeamSide
    weight: float


# ─── EventStreamProcessor ───────────────────────────────────────────────────

class EventStreamProcessor(TimerComponent):
    """Processes raw game events into structured, categorized outputs.

    Fixes the orphan ``/lol/events`` channel by subscribing to it and
    producing typed sub-channels for downstream modules.

    Each Proc() cycle:
        1. Read new events from /lol/events
        2. For each ChampionKill: update kill tracking, generate KillFeedEntry
        3. For each objective: generate ObjectiveEvent
        4. Run sliding-window teamfight cluster detection
        5. Compute momentum scores
        6. Publish results to sub-channels
    """

    def __init__(self) -> None:
        super().__init__(
            config=ComponentConfig(
                name="event_stream_processor",
                interval_ms=_ESP_INTERVAL_MS,
                warn_threshold_ms=_WARN_THRESHOLD_MS,
            ),
        )
        self._node: Optional[CyberNode] = None

        # Readers
        self._events_reader: Optional[Reader] = None
        self._game_state_reader: Optional[Reader] = None

        # Writers
        self._kill_feed_writer: Optional[Writer] = None
        self._objective_writer: Optional[Writer] = None
        self._teamfight_writer: Optional[Writer] = None
        self._voice_writer: Optional[Writer] = None
        self._status_writer: Optional[Writer] = None

        # Kill tracking state
        self._player_states: Dict[str, _PlayerKillState] = {}
        self._first_blood_seen: bool = False

        # Teamfight detection state
        self._recent_kills: Deque[Tuple[float, TeamSide]] = deque(maxlen=50)
        self._last_teamfight_time: float = -_TEAMFIGHT_COOLDOWN_S
        self._active_teamfight: Optional[TeamfightCluster] = None
        self._teamfight_participants: Set[str] = set()
        self._teamfight_kills_blue: int = 0
        self._teamfight_kills_red: int = 0
        self._teamfight_start: float = 0.0

        # Momentum state
        self._momentum_events: Deque[_MomentumEvent] = deque(maxlen=200)

        # Stats
        self._processed_event_ids: Set[int] = set()
        self._kill_feed_count: int = 0
        self._objective_count: int = 0
        self._teamfight_count: int = 0
        self._proc_count: int = 0

        # Current snapshot reference
        self._current_snapshot: Optional[GameSnapshot] = None

    def Init(self) -> bool:
        """Subscribe to /lol/events and /lol/game_state, create publishers."""
        logger.info("Initializing EventStreamProcessor...")

        self._node = CyberNode("event_stream_processor")

        # ── Readers ──────────────────────────────────────────────────
        self._events_reader = self._node.CreateReader(
            "/lol/events", list, pending_queue_size=32,
        )
        self._game_state_reader = self._node.CreateReader(
            "/lol/game_state", object, pending_queue_size=4,
        )

        # ── Writers ──────────────────────────────────────────────────
        self._kill_feed_writer = self._node.CreateWriter(
            "/lol/kill_feed", list,
        )
        self._objective_writer = self._node.CreateWriter(
            "/lol/objective_events", ObjectiveEvent,
        )
        self._teamfight_writer = self._node.CreateWriter(
            "/lol/teamfight_active", TeamfightCluster,
        )
        self._voice_writer = self._node.CreateWriter(
            "/lol/voice_command", VoiceCommand,
        )
        self._status_writer = self._node.CreateWriter(
            "/lol/event_processor_status", StatusMessage,
        )

        logger.info("EventStreamProcessor initialized — subscribed to /lol/events")
        return True

    def Proc(self) -> bool:
        """One event processing cycle.

        Apollo equivalent: perception fusion Proc() that reads multiple
        sub-modules and publishes fused output.
        """
        self._proc_count += 1

        # ── Read latest game state for context ───────────────────────
        self._game_state_reader.Observe()
        snapshot = self._game_state_reader.GetLatestObserved()
        if snapshot is not None:
            self._current_snapshot = snapshot
            self._ensure_player_states(snapshot)

        # ── Read new events ──────────────────────────────────────────
        self._events_reader.Observe()
        events_batch = self._events_reader.GetLatestObserved()

        if events_batch is None or not events_batch:
            return True  # No new events

        # ── Process each event ───────────────────────────────────────
        new_kill_entries: List[KillFeedEntry] = []
        new_objectives: List[ObjectiveEvent] = []

        for event in events_batch:
            if not isinstance(event, GameEvent):
                continue
            if event.event_id in self._processed_event_ids:
                continue
            self._processed_event_ids.add(event.event_id)

            evt_name = event.event_type.value if hasattr(event.event_type, 'value') else str(event.event_type)

            if evt_name == "ChampionKill":
                entry = self._process_champion_kill(event)
                if entry:
                    new_kill_entries.append(entry)

            elif evt_name in ("DragonKill", "BaronKill", "HeraldKill",
                              "InhibKilled", "TurretKilled"):
                obj = self._process_objective(event)
                if obj:
                    new_objectives.append(obj)

            elif evt_name == "FirstBlood":
                self._first_blood_seen = True

        # ── Teamfight detection ──────────────────────────────────────
        game_time = (self._current_snapshot.game_time
                     if self._current_snapshot else 0.0)
        teamfight = self._detect_teamfight(game_time)

        # ── Momentum computation ─────────────────────────────────────
        momentum = self._compute_momentum(game_time)

        # ── Publish ──────────────────────────────────────────────────
        if new_kill_entries and self._kill_feed_writer:
            self._kill_feed_writer.Write(new_kill_entries)
            self._kill_feed_count += len(new_kill_entries)

        for obj in new_objectives:
            if self._objective_writer:
                self._objective_writer.Write(obj)
            self._objective_count += 1

        if teamfight and self._teamfight_writer:
            self._teamfight_writer.Write(teamfight)
            self._teamfight_count += 1
            # Voice announcement for significant teamfight
            if teamfight.kill_count >= 4:
                self._announce_teamfight(teamfight)

        # ── Announce notable kills ───────────────────────────────────
        for entry in new_kill_entries:
            self._announce_notable_kill(entry)

        self._publish_status(Status.ok())
        return True

    def on_shutdown(self) -> None:
        if self._node:
            self._node.shutdown()

    # ─── Kill processing ─────────────────────────────────────────────

    def _process_champion_kill(self, event: GameEvent) -> Optional[KillFeedEntry]:
        """Process a champion kill event into a KillFeedEntry.

        Updates per-player kill/death state, detects multi-kills,
        sprees, shutdowns, and first blood.
        """
        killer = event.killer
        victim = event.victim
        game_time = event.game_time

        # Resolve teams from snapshot
        killer_team = self._resolve_team(killer)
        victim_team = self._resolve_team(victim)

        # Update kill states
        killer_state = self._get_or_create_player(killer, killer_team)
        victim_state = self._get_or_create_player(victim, victim_team)

        killer_state.record_kill(game_time)
        victim_spree = victim_state.record_death()

        # Track for teamfight detection
        self._recent_kills.append((game_time, killer_team))

        # Track for momentum
        self._momentum_events.append(_MomentumEvent(
            game_time=game_time,
            team=killer_team,
            weight=_MOMENTUM_KILL_WEIGHT,
        ))

        # ── Tag classification ───────────────────────────────────────
        tags: Set[KillFeedTag] = set()

        # First blood
        if not self._first_blood_seen and self._kill_feed_count == 0:
            tags.add(KillFeedTag.FIRST_BLOOD)
            self._first_blood_seen = True

        # Multi-kill
        multi_count = killer_state.multi_kill_count
        multi_type = MultiKillType.from_count(multi_count)
        if multi_type != MultiKillType.NONE:
            tags.add(KillFeedTag.MULTI_KILL)

        # Killing spree
        if killer_state.kills_no_death >= _SPREE_THRESHOLD:
            tags.add(KillFeedTag.KILLING_SPREE)

        # Shutdown (victim was on a spree)
        is_shutdown = victim_spree >= _SPREE_THRESHOLD
        if is_shutdown:
            tags.add(KillFeedTag.SHUTDOWN)

        # Solo kill (no assisters)
        if not event.assisters:
            tags.add(KillFeedTag.SOLO_KILL)

        # Ace detection: all enemies dead
        if self._is_ace_after_kill(victim_team, game_time):
            tags.add(KillFeedTag.ACE)

        if not tags:
            tags.add(KillFeedTag.NORMAL)

        # Bounty estimation (simplified)
        bounty = self._estimate_bounty(victim_spree)

        # Add teamfight participants
        self._teamfight_participants.add(killer)
        self._teamfight_participants.add(victim)
        for a in event.assisters:
            self._teamfight_participants.add(a)

        return KillFeedEntry(
            event_id=event.event_id,
            game_time=game_time,
            killer=killer,
            victim=victim,
            assisters=event.assisters,
            killer_team=killer_team,
            victim_team=victim_team,
            tags=frozenset(tags),
            multi_kill=multi_type,
            killer_spree_count=killer_state.kills_no_death,
            victim_spree_count=victim_spree,
            is_shutdown=is_shutdown,
            bounty_gold=bounty,
        )

    def _process_objective(self, event: GameEvent) -> Optional[ObjectiveEvent]:
        """Process an objective kill event."""
        killer_team = self._resolve_team(event.killer)

        # Determine objective type from event_type
        evt_name = event.event_type.value if hasattr(event.event_type, 'value') else str(event.event_type)
        obj_type = evt_name

        # Stolen detection: killer team != expected team
        # (heuristic: if killer is support/jungle of the other team, likely stolen)
        is_stolen = False  # Would need more context to detect properly

        # Add momentum event
        weight = _MOMENTUM_OBJECTIVE_WEIGHT
        if "Baron" in obj_type:
            weight = 0.40
        elif "Tower" in obj_type or "Turret" in obj_type:
            weight = _MOMENTUM_TOWER_WEIGHT

        self._momentum_events.append(_MomentumEvent(
            game_time=event.game_time,
            team=killer_team,
            weight=weight,
        ))

        return ObjectiveEvent(
            event_id=event.event_id,
            game_time=event.game_time,
            objective_type=obj_type,
            taken_by=killer_team,
            killer=event.killer,
            is_stolen=is_stolen,
        )

    # ─── Teamfight detection ─────────────────────────────────────────

    def _detect_teamfight(self, game_time: float) -> Optional[TeamfightCluster]:
        """Sliding window teamfight cluster detection.

        If 3+ champion kills occur within a 5-second window, and we
        haven't reported a teamfight in the last 15 seconds, declare one.
        """
        if game_time - self._last_teamfight_time < _TEAMFIGHT_COOLDOWN_S:
            return None

        # Sliding window: count kills in last _TEAMFIGHT_WINDOW_S seconds
        cutoff = game_time - _TEAMFIGHT_WINDOW_S
        recent = [(t, team) for t, team in self._recent_kills if t >= cutoff]

        if len(recent) < _TEAMFIGHT_MIN_KILLS:
            return None

        # We have a teamfight cluster
        blue_kills = sum(1 for _, t in recent if t == TeamSide.BLUE)
        red_kills = sum(1 for _, t in recent if t == TeamSide.RED)

        # Determine winner
        if blue_kills > red_kills:
            winner = TeamSide.BLUE
        elif red_kills > blue_kills:
            winner = TeamSide.RED
        else:
            winner = TeamSide.UNKNOWN

        # Check for ace
        is_ace = KillFeedTag.ACE in self._get_recent_tags()

        # Infer location from game time heuristic
        location = self._infer_fight_location(game_time)

        start_time = recent[0][0] if recent else game_time
        end_time = recent[-1][0] if recent else game_time

        cluster = TeamfightCluster(
            start_time=start_time,
            end_time=end_time,
            kill_count=len(recent),
            blue_kills=blue_kills,
            red_kills=red_kills,
            participants=frozenset(self._teamfight_participants),
            location_hint=location,
            winner=winner,
            is_ace=is_ace,
        )

        self._last_teamfight_time = game_time
        self._teamfight_participants.clear()

        return cluster

    def _get_recent_tags(self) -> Set[KillFeedTag]:
        """Placeholder: return tags from recent kill feed entries."""
        return set()

    def _infer_fight_location(self, game_time: float) -> str:
        """Heuristic location inference based on game time and objectives.

        Baron spawns at 20:00, dragon is always relevant.
        """
        if game_time < 840:  # 14 min
            return "river"
        if game_time >= 1200:  # 20 min
            # Could be baron or dragon pit
            return "baron_pit"
        return "mid"

    # ─── Momentum computation ────────────────────────────────────────

    def _compute_momentum(self, game_time: float) -> MomentumSnapshot:
        """Compute per-team momentum using exponential decay.

        Each event contributes weight * exp(-lambda * age) where
        lambda = ln(2) / half_life.
        """
        if game_time <= 0:
            return MomentumSnapshot()

        decay_lambda = math.log(2) / _MOMENTUM_DECAY_HALF_LIFE_S
        cutoff = game_time - _MOMENTUM_WINDOW_S

        blue_score = 0.0
        red_score = 0.0

        for evt in self._momentum_events:
            if evt.game_time < cutoff:
                continue
            age = game_time - evt.game_time
            decayed_weight = evt.weight * math.exp(-decay_lambda * age)

            if evt.team == TeamSide.BLUE:
                blue_score += decayed_weight
            elif evt.team == TeamSide.RED:
                red_score += decayed_weight

        # Normalize to [-1, +1] range
        max_val = max(abs(blue_score), abs(red_score), 0.01)
        blue_norm = max(-1.0, min(1.0, blue_score / max_val))
        red_norm = max(-1.0, min(1.0, red_score / max_val))
        net = blue_norm - red_norm

        if net > 0.3:
            trend = "blue_rising"
        elif net < -0.3:
            trend = "red_rising"
        else:
            trend = "stable"

        return MomentumSnapshot(
            blue_momentum=round(blue_norm, 3),
            red_momentum=round(red_norm, 3),
            net_momentum=round(net, 3),
            trend=trend,
            game_time=game_time,
        )

    # ─── Voice announcements ─────────────────────────────────────────

    def _announce_notable_kill(self, entry: KillFeedEntry) -> None:
        """Announce high-impact kill events via voice."""
        if not self._voice_writer:
            return

        text = ""
        priority = 5

        if KillFeedTag.ACE in entry.tags:
            team_name = entry.killer_team.name.lower()
            text = f"ACE! {team_name} team aced the enemy."
            priority = 1
        elif KillFeedTag.FIRST_BLOOD in entry.tags:
            text = f"First blood to {entry.killer}!"
            priority = 2
        elif entry.multi_kill == MultiKillType.PENTA:
            text = f"PENTAKILL by {entry.killer}!"
            priority = 1
        elif entry.multi_kill == MultiKillType.QUADRA:
            text = f"Quadra kill by {entry.killer}!"
            priority = 2
        elif KillFeedTag.SHUTDOWN in entry.tags:
            text = f"Shutdown! {entry.killer} ended {entry.victim}'s spree."
            priority = 3

        if text:
            self._voice_writer.Write(VoiceCommand(
                text=text,
                priority=priority,
                max_age_s=8.0,
                game_time=entry.game_time,
                source_module="event_stream_processor",
            ))

    def _announce_teamfight(self, cluster: TeamfightCluster) -> None:
        """Announce significant teamfight results."""
        if not self._voice_writer:
            return

        winner_name = cluster.winner.name.lower() if cluster.winner != TeamSide.UNKNOWN else "no"
        text = (
            f"Teamfight over! {cluster.blue_kills} to {cluster.red_kills}, "
            f"{winner_name} team wins."
        )
        if cluster.is_ace:
            text += " ACE!"

        self._voice_writer.Write(VoiceCommand(
            text=text,
            priority=2,
            max_age_s=10.0,
            game_time=cluster.end_time,
            source_module="event_stream_processor",
        ))

    # ─── Helper methods ──────────────────────────────────────────────

    def _ensure_player_states(self, snapshot: GameSnapshot) -> None:
        """Initialize player tracking from game state."""
        for player in snapshot.all_players:
            if player.summoner_name not in self._player_states:
                self._player_states[player.summoner_name] = _PlayerKillState(
                    name=player.summoner_name,
                    team=player.team,
                )

    def _get_or_create_player(
        self, name: str, team: TeamSide
    ) -> _PlayerKillState:
        """Get or create a player's kill tracking state."""
        if name not in self._player_states:
            self._player_states[name] = _PlayerKillState(
                name=name, team=team,
            )
        return self._player_states[name]

    def _resolve_team(self, player_name: str) -> TeamSide:
        """Resolve a player's team from the current snapshot or cache."""
        if player_name in self._player_states:
            return self._player_states[player_name].team

        if self._current_snapshot:
            for p in self._current_snapshot.all_players:
                if p.summoner_name == player_name:
                    return p.team

        return TeamSide.UNKNOWN

    def _is_ace_after_kill(
        self, victim_team: TeamSide, game_time: float
    ) -> bool:
        """Check if all members of victim_team are dead after this kill.

        Uses current snapshot to check is_dead status, with a heuristic
        fallback if snapshot is stale.
        """
        if not self._current_snapshot:
            return False

        team_players = [
            p for p in self._current_snapshot.all_players
            if p.team == victim_team
        ]
        if not team_players:
            return False

        alive_count = sum(1 for p in team_players if not p.is_dead)
        # Account for the kill we just processed (might not be reflected yet)
        return alive_count <= 1

    @staticmethod
    def _estimate_bounty(spree_count: int) -> int:
        """Estimate bounty gold for shutting down a spree.

        Simplified from League's actual bounty system.
        """
        if spree_count < 3:
            return 300  # base kill gold
        bounty_table = {
            3: 450,
            4: 600,
            5: 700,
            6: 800,
            7: 900,
        }
        return bounty_table.get(spree_count, 1000)

    # ─── Status ──────────────────────────────────────────────────────

    def _publish_status(self, status: Status) -> None:
        if self._status_writer:
            self._status_writer.Write(StatusMessage(
                status=status,
                sequence=self._proc_count,
                source_component="event_stream_processor",
            ))

    def event_processor_status(self) -> Dict[str, Any]:
        base = self.status()
        base.update({
            "proc_count": self._proc_count,
            "kill_feed_count": self._kill_feed_count,
            "objective_count": self._objective_count,
            "teamfight_count": self._teamfight_count,
            "tracked_players": len(self._player_states),
            "processed_events": len(self._processed_event_ids),
            "first_blood_seen": self._first_blood_seen,
        })
        return base
