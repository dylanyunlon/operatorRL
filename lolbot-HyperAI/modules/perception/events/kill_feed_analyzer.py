"""
KillFeedAnalyzer — Kill feed pattern detection (multi-kill, spree, shutdown).
===============================================================================
lolbot-HyperAI · Perception Layer

Analyzes the kill event stream to detect high-level combat patterns:
    - Multi-kills: double, triple, quadra, penta
    - Killing sprees: 3+ consecutive kills without dying
    - Shutdowns: ending an enemy's spree (bounty)
    - First blood
    - Ace (entire enemy team dead)

Architecture position:
    modules/perception/events/kill_feed_analyzer.py   ← YOU ARE HERE
    ├─ Input: GameEvent stream from perception_component
    ├─ Output: DetectedKillPattern objects
    ├─ Consumed by: prediction (momentum), planning (urgency), voice
    └─ Sibling: event_detector.py (teamfight/objective patterns)

Apollo reference:
    modules/perception/traffic_light_detection/  — pattern classification
    modules/perception/onboard/lidar_process.cc  — sequential processing

Design notes:
    - Time window for multi-kill: 10s between consecutive kills
    - Spree tracking: per-player kill count since last death
    - Shutdown value: maps Riot's bounty tiers
    - Ace detection: all 5 enemies dead simultaneously
    - Thread-safe: all state is mutated in Proc() thread only
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Deque, Dict, List, Optional, Set, Tuple

from cyber.logger.cyber_logger import get_logger
from modules.common.adapters.game_messages import (
    EventType,
    GameEvent,
    GameSnapshot,
    PlayerState,
    TeamSide,
)

logger = get_logger("perception.killfeed")

# ─── Constants ───────────────────────────────────────────────────────────────

_MULTI_KILL_WINDOW_S = 10.0     # Max time between kills for multi-kill
_SPREE_KILL_THRESHOLD = 3       # Kills without dying = spree
_ACE_CHECK_WINDOW_S = 30.0      # Window to check if all 5 dead
_MAX_PATTERN_HISTORY = 200
_SHUTDOWN_BOUNTY_TIERS = {      # Kill streak → bounty gold
    3: 150,
    4: 200,
    5: 250,
    6: 300,
    7: 350,
    8: 400,
}


# ─── Data Types ──────────────────────────────────────────────────────────────

class KillPatternType(Enum):
    """Types of detected kill patterns."""
    FIRST_BLOOD = "first_blood"
    DOUBLE_KILL = "double_kill"
    TRIPLE_KILL = "triple_kill"
    QUADRA_KILL = "quadra_kill"
    PENTA_KILL = "penta_kill"
    KILLING_SPREE = "killing_spree"       # 3 kills
    RAMPAGE = "rampage"                     # 4 kills
    UNSTOPPABLE = "unstoppable"             # 5 kills
    DOMINATING = "dominating"               # 6 kills
    GODLIKE = "godlike"                     # 7 kills
    LEGENDARY = "legendary"                 # 8+ kills
    SHUTDOWN = "shutdown"                   # Ended enemy spree
    ACE = "ace"                             # All 5 enemies dead


MULTI_KILL_NAMES = {
    2: KillPatternType.DOUBLE_KILL,
    3: KillPatternType.TRIPLE_KILL,
    4: KillPatternType.QUADRA_KILL,
    5: KillPatternType.PENTA_KILL,
}

SPREE_NAMES = {
    3: KillPatternType.KILLING_SPREE,
    4: KillPatternType.RAMPAGE,
    5: KillPatternType.UNSTOPPABLE,
    6: KillPatternType.DOMINATING,
    7: KillPatternType.GODLIKE,
}


@dataclass
class DetectedKillPattern:
    """A detected kill pattern with context."""
    pattern_type: KillPatternType
    player_name: str              # The player who achieved it
    team: TeamSide
    game_time: float
    kill_count: int = 0           # For sprees/multi-kills
    victim_name: str = ""         # For shutdowns
    bounty_gold: int = 0          # For shutdowns
    confidence: float = 1.0       # Detection confidence
    participants: List[str] = field(default_factory=list)
    timestamp: float = field(default_factory=time.monotonic)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.pattern_type.value,
            "player": self.player_name,
            "team": self.team.value,
            "game_time": round(self.game_time, 1),
            "kill_count": self.kill_count,
            "victim": self.victim_name,
            "bounty": self.bounty_gold,
            "confidence": round(self.confidence, 2),
        }

    @property
    def is_our_team(self) -> bool:
        """Check if this pattern is from our team (requires context)."""
        return True  # Resolved by caller with active player team info


# ─── Player Kill Tracker ────────────────────────────────────────────────────

class _PlayerKillState:
    """Tracks kill/death state for a single player."""

    def __init__(self, name: str, team: TeamSide) -> None:
        self.name = name
        self.team = team
        self.total_kills: int = 0
        self.total_deaths: int = 0
        self.current_spree: int = 0      # Kills since last death
        self.multi_kill_count: int = 0    # Kills in current multi-kill window
        self.last_kill_time: float = 0.0
        self.kill_times: Deque[float] = deque(maxlen=10)

    def record_kill(self, game_time: float) -> Optional[int]:
        """Record a kill by this player.

        Returns:
            Multi-kill count if within window, None otherwise.
        """
        self.total_kills += 1
        self.current_spree += 1

        # Multi-kill detection
        if game_time - self.last_kill_time <= _MULTI_KILL_WINDOW_S:
            self.multi_kill_count += 1
        else:
            self.multi_kill_count = 1

        self.last_kill_time = game_time
        self.kill_times.append(game_time)

        return self.multi_kill_count if self.multi_kill_count >= 2 else None

    def record_death(self, game_time: float) -> int:
        """Record a death of this player.

        Returns:
            The spree count that was ended (0 if no spree).
        """
        self.total_deaths += 1
        ended_spree = self.current_spree
        self.current_spree = 0
        self.multi_kill_count = 0
        return ended_spree


# ─── KillFeedAnalyzer ───────────────────────────────────────────────────────

class KillFeedAnalyzer:
    """Analyzes kill events to detect combat patterns.

    Maintains per-player kill state and detects multi-kills, sprees,
    shutdowns, first blood, and aces from the event stream.

    Usage::

        analyzer = KillFeedAnalyzer()
        # Feed events from perception
        patterns = analyzer.analyze(events, snapshot)
        for p in patterns:
            print(f"{p.pattern_type.value}: {p.player_name}")
    """

    def __init__(self) -> None:
        self._players: Dict[str, _PlayerKillState] = {}
        self._first_blood_detected: bool = False
        self._seen_event_ids: Set[int] = set()
        self._pattern_history: Deque[DetectedKillPattern] = deque(
            maxlen=_MAX_PATTERN_HISTORY,
        )
        self._analysis_count: int = 0

    def analyze(
        self,
        events: List[GameEvent],
        snapshot: Optional[GameSnapshot] = None,
    ) -> List[DetectedKillPattern]:
        """Analyze a batch of events for kill patterns.

        Args:
            events: New game events since last call.
            snapshot: Current game state (for ace detection).

        Returns:
            List of newly detected patterns.
        """
        self._analysis_count += 1
        patterns: List[DetectedKillPattern] = []

        # Filter to kill events we haven't seen
        kill_events = []
        for event in events:
            if event.event_type != EventType.CHAMPION_KILL:
                continue
            eid = id(event)
            if eid in self._seen_event_ids:
                continue
            self._seen_event_ids.add(eid)
            kill_events.append(event)

        # Process each kill event
        for event in kill_events:
            killer_name = event.killer or "unknown"
            victim_name = event.victim or "unknown"
            game_time = event.game_time

            # Ensure player states exist
            if killer_name not in self._players:
                self._players[killer_name] = _PlayerKillState(
                    killer_name, TeamSide.UNKNOWN,
                )
            if victim_name not in self._players:
                self._players[victim_name] = _PlayerKillState(
                    victim_name, TeamSide.UNKNOWN,
                )

            killer_state = self._players[killer_name]
            victim_state = self._players[victim_name]

            # First blood
            if not self._first_blood_detected:
                self._first_blood_detected = True
                patterns.append(DetectedKillPattern(
                    pattern_type=KillPatternType.FIRST_BLOOD,
                    player_name=killer_name,
                    team=killer_state.team,
                    game_time=game_time,
                    kill_count=1,
                    victim_name=victim_name,
                ))

            # Record kill for killer
            multi_count = killer_state.record_kill(game_time)

            # Multi-kill detection
            if multi_count is not None and multi_count in MULTI_KILL_NAMES:
                patterns.append(DetectedKillPattern(
                    pattern_type=MULTI_KILL_NAMES[multi_count],
                    player_name=killer_name,
                    team=killer_state.team,
                    game_time=game_time,
                    kill_count=multi_count,
                ))

            # Spree detection
            spree = killer_state.current_spree
            if spree in SPREE_NAMES:
                patterns.append(DetectedKillPattern(
                    pattern_type=SPREE_NAMES[spree],
                    player_name=killer_name,
                    team=killer_state.team,
                    game_time=game_time,
                    kill_count=spree,
                ))
            elif spree >= 8:
                patterns.append(DetectedKillPattern(
                    pattern_type=KillPatternType.LEGENDARY,
                    player_name=killer_name,
                    team=killer_state.team,
                    game_time=game_time,
                    kill_count=spree,
                ))

            # Record death for victim
            ended_spree = victim_state.record_death(game_time)

            # Shutdown detection
            if ended_spree >= _SPREE_KILL_THRESHOLD:
                bounty = _SHUTDOWN_BOUNTY_TIERS.get(
                    min(ended_spree, 8), 400,
                )
                patterns.append(DetectedKillPattern(
                    pattern_type=KillPatternType.SHUTDOWN,
                    player_name=killer_name,
                    team=killer_state.team,
                    game_time=game_time,
                    kill_count=ended_spree,
                    victim_name=victim_name,
                    bounty_gold=bounty,
                ))

        # Ace detection (from snapshot)
        if snapshot is not None:
            ace_pattern = self._check_ace(snapshot)
            if ace_pattern is not None:
                patterns.append(ace_pattern)

        # Record to history
        self._pattern_history.extend(patterns)

        return patterns

    def _check_ace(self, snapshot: GameSnapshot) -> Optional[DetectedKillPattern]:
        """Check if an entire team is dead (ace)."""
        for team_side, team in [
            (TeamSide.BLUE, snapshot.blue_team),
            (TeamSide.RED, snapshot.red_team),
        ]:
            alive = sum(1 for p in team.players if not p.is_dead)
            if alive == 0 and len(team.players) == 5:
                # The OTHER team achieved the ace
                ace_team = (
                    TeamSide.RED if team_side == TeamSide.BLUE
                    else TeamSide.BLUE
                )
                return DetectedKillPattern(
                    pattern_type=KillPatternType.ACE,
                    player_name="team",
                    team=ace_team,
                    game_time=snapshot.game_time,
                    kill_count=5,
                )
        return None

    # ── Query ────────────────────────────────────────────────────────────

    def get_player_state(self, name: str) -> Optional[_PlayerKillState]:
        """Get kill state for a specific player."""
        return self._players.get(name)

    def get_active_sprees(self) -> List[Tuple[str, int]]:
        """Return all players currently on a killing spree."""
        return [
            (p.name, p.current_spree)
            for p in self._players.values()
            if p.current_spree >= _SPREE_KILL_THRESHOLD
        ]

    def recent_patterns(self, n: int = 10) -> List[DetectedKillPattern]:
        """Return the last n detected patterns."""
        items = list(self._pattern_history)
        return items[-n:]

    # ── Stats & Reset ────────────────────────────────────────────────────

    def stats(self) -> Dict[str, Any]:
        """Return analyzer statistics."""
        type_counts: Dict[str, int] = {}
        for p in self._pattern_history:
            key = p.pattern_type.value
            type_counts[key] = type_counts.get(key, 0) + 1

        return {
            "analysis_count": self._analysis_count,
            "players_tracked": len(self._players),
            "patterns_detected": len(self._pattern_history),
            "first_blood": self._first_blood_detected,
            "pattern_types": type_counts,
            "active_sprees": len(self.get_active_sprees()),
        }

    def reset(self) -> None:
        """Reset all state between games."""
        self._players.clear()
        self._first_blood_detected = False
        self._seen_event_ids.clear()
        self._pattern_history.clear()
        self._analysis_count = 0
