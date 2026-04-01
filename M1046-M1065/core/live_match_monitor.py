#!/usr/bin/env python3
"""
M1055: Live Match Monitor — Real-time In-Game Event Tracking
=============================================================
OperatorRL M1046-M1065 · 自部署 自环境反馈 自演化

Monitors live game state during InProgress phase. Tracks:
- Game timer and phase (laning, mid-game, late-game)
- Objective timers (Dragon, Baron, Rift Herald)
- Kill feed events (from network capture)
- Gold/XP differential estimation
- Win probability tracking

Drives the Strategy Engine's periodic recommendations.
"""

import asyncio
import sys
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, auto
from pathlib import Path
from typing import Any, Callable, Deque, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).parent.parent))
try:
    from evo_logging.evolution_logger import get_logger, LogCategory
except ImportError:
    pass


class GameSubPhase(Enum):
    EARLY_LANING = "early_laning"       # 0-5 min
    MID_LANING = "mid_laning"           # 5-14 min
    TRANSITION = "transition"           # 14-20 min
    MID_GAME = "mid_game"              # 20-30 min
    LATE_GAME = "late_game"            # 30+ min


class ObjectiveType(Enum):
    DRAGON = "dragon"
    RIFT_HERALD = "rift_herald"
    BARON = "baron"
    ELDER_DRAGON = "elder_dragon"
    TOWER = "tower"
    INHIBITOR = "inhibitor"


@dataclass
class ObjectiveTimer:
    """Tracks respawn timer for a major objective."""
    objective_type: str
    spawn_time_sec: float          # Game time when objective spawns
    respawn_duration_sec: float    # Time between kills and respawn
    last_killed_at: Optional[float] = None
    kill_count: int = 0
    team_secured: Optional[str] = None  # "ally" or "enemy" or None

    @property
    def next_spawn_sec(self) -> Optional[float]:
        if self.last_killed_at is None:
            return self.spawn_time_sec
        return self.last_killed_at + self.respawn_duration_sec

    @property
    def is_alive(self) -> bool:
        if self.last_killed_at is None:
            return True  # Never killed = still alive (or not yet spawned)
        return False

    def to_dict(self) -> Dict[str, Any]:
        return {
            'type': self.objective_type,
            'next_spawn': self.next_spawn_sec,
            'kill_count': self.kill_count,
            'last_secured_by': self.team_secured,
        }


@dataclass
class KillEvent:
    """Recorded kill event during live match."""
    game_time_sec: float
    killer: str
    victim: str
    assistants: List[str] = field(default_factory=list)
    is_first_blood: bool = False
    is_shutdown: bool = False
    gold_value: int = 300

    def to_dict(self) -> Dict[str, Any]:
        return self.__dict__


@dataclass
class LiveMatchState:
    """Complete live match state snapshot."""
    game_time_sec: float = 0.0
    sub_phase: str = GameSubPhase.EARLY_LANING.value
    ally_kills: int = 0
    enemy_kills: int = 0
    ally_towers: int = 0
    enemy_towers: int = 0
    ally_dragons: int = 0
    enemy_dragons: int = 0
    baron_active: bool = False
    elder_active: bool = False
    estimated_gold_diff: int = 0
    win_probability: float = 0.5
    kill_events: List[Dict] = field(default_factory=list)
    objective_timers: Dict[str, Dict] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return self.__dict__


class LiveMatchMonitor:
    """
    Real-time match state tracker.

    Subscribes to network capture events and game state tracker.
    Maintains a running model of the current game state.

    Tick rate: Every 30 seconds, computes updated state and
    triggers Strategy Engine for periodic recommendations.
    """
    TICK_INTERVAL_SEC = 30.0

    # Standard objective timers (Season 14+)
    DRAGON_SPAWN_SEC = 300       # 5:00
    DRAGON_RESPAWN_SEC = 300     # 5:00
    HERALD_SPAWN_SEC = 480       # 8:00
    HERALD_DESPAWN_SEC = 1195    # 19:55
    BARON_SPAWN_SEC = 1200       # 20:00
    BARON_RESPAWN_SEC = 360      # 6:00
    ELDER_SPAWN_SEC = 2100       # 35:00 (after soul point)
    ELDER_RESPAWN_SEC = 360      # 6:00

    def __init__(self):
        self._logger = get_logger()
        self._state = LiveMatchState()
        self._kill_events: Deque[KillEvent] = deque(maxlen=200)
        self._objectives = self._init_objectives()
        self._running = False
        self._tick_callbacks: List[Callable[[LiveMatchState], None]] = []
        self._game_start_real_time = 0.0

    def _init_objectives(self) -> Dict[str, ObjectiveTimer]:
        return {
            'dragon': ObjectiveTimer(
                'dragon', self.DRAGON_SPAWN_SEC, self.DRAGON_RESPAWN_SEC),
            'rift_herald': ObjectiveTimer(
                'rift_herald', self.HERALD_SPAWN_SEC, 0),
            'baron': ObjectiveTimer(
                'baron', self.BARON_SPAWN_SEC, self.BARON_RESPAWN_SEC),
            'elder': ObjectiveTimer(
                'elder', self.ELDER_SPAWN_SEC, self.ELDER_RESPAWN_SEC),
        }

    def add_tick_callback(
        self, callback: Callable[[LiveMatchState], None]
    ) -> None:
        self._tick_callbacks.append(callback)

    async def start_monitoring(self) -> None:
        """Start the periodic monitoring loop."""
        self._running = True
        self._game_start_real_time = time.monotonic()
        self._logger.info(LogCategory.GAME_STATE, "Live match monitoring started")
        while self._running:
            self._tick()
            await asyncio.sleep(self.TICK_INTERVAL_SEC)

    def stop_monitoring(self) -> None:
        self._running = False
        self._logger.info(
            LogCategory.GAME_STATE,
            "Live match monitoring stopped",
            data=self._state.to_dict())

    def on_network_event(self, event_data: Dict) -> None:
        """Process an extracted game event from network capture."""
        event_type = event_data.get('event_type', '')
        if event_type == 'kill':
            self._process_kill(event_data)
        elif event_type == 'objective_taken':
            self._process_objective(event_data)
        elif event_type == 'tower_destroyed':
            self._process_tower(event_data)

    def _tick(self) -> None:
        """Periodic state update."""
        elapsed = time.monotonic() - self._game_start_real_time
        self._state.game_time_sec = elapsed
        self._state.sub_phase = self._determine_sub_phase(elapsed)
        self._state.win_probability = self._estimate_win_probability()
        self._state.objective_timers = {
            k: v.to_dict() for k, v in self._objectives.items()}
        self._state.kill_events = [
            k.to_dict() for k in list(self._kill_events)[-10:]]
        # Notify subscribers
        for cb in self._tick_callbacks:
            try:
                cb(self._state)
            except Exception as e:
                self._logger.error(
                    LogCategory.GAME_STATE, f"Tick callback error: {e}")

    def _determine_sub_phase(self, game_time_sec: float) -> str:
        if game_time_sec < 300:
            return GameSubPhase.EARLY_LANING.value
        elif game_time_sec < 840:
            return GameSubPhase.MID_LANING.value
        elif game_time_sec < 1200:
            return GameSubPhase.TRANSITION.value
        elif game_time_sec < 1800:
            return GameSubPhase.MID_GAME.value
        return GameSubPhase.LATE_GAME.value

    def _process_kill(self, data: Dict) -> None:
        event = KillEvent(
            game_time_sec=data.get('game_time', 0),
            killer=data.get('killer', ''),
            victim=data.get('victim', ''),
            assistants=data.get('assistants', []),
            is_first_blood=data.get('is_first_blood', False),
        )
        self._kill_events.append(event)
        if data.get('team') == 'ally':
            self._state.ally_kills += 1
            self._state.estimated_gold_diff += event.gold_value
        else:
            self._state.enemy_kills += 1
            self._state.estimated_gold_diff -= event.gold_value

    def _process_objective(self, data: Dict) -> None:
        obj_type = data.get('objective_type', '')
        team = data.get('team', '')
        game_time = data.get('game_time', 0)
        if obj_type in self._objectives:
            obj = self._objectives[obj_type]
            obj.last_killed_at = game_time
            obj.kill_count += 1
            obj.team_secured = team
        if obj_type == 'dragon':
            if team == 'ally':
                self._state.ally_dragons += 1
            else:
                self._state.enemy_dragons += 1

    def _process_tower(self, data: Dict) -> None:
        team = data.get('team', '')
        if team == 'ally':
            self._state.ally_towers += 1
            self._state.estimated_gold_diff += 550  # First tower gold
        else:
            self._state.enemy_towers += 1
            self._state.estimated_gold_diff -= 550

    def _estimate_win_probability(self) -> float:
        """
        Simple win probability model based on observable factors.

        Full model would use logistic regression on:
        - Gold diff at current game time
        - Tower diff
        - Dragon diff
        - Kill diff
        - Baron control
        """
        score = 0.5  # Start at 50/50
        # Gold diff factor (max ±15%)
        gold_factor = self._state.estimated_gold_diff / 10000
        score += max(-0.15, min(0.15, gold_factor))
        # Kill diff factor (max ±10%)
        kill_diff = self._state.ally_kills - self._state.enemy_kills
        score += max(-0.10, min(0.10, kill_diff * 0.01))
        # Tower diff (max ±10%)
        tower_diff = self._state.ally_towers - self._state.enemy_towers
        score += max(-0.10, min(0.10, tower_diff * 0.02))
        # Dragon diff (max ±5%)
        dragon_diff = self._state.ally_dragons - self._state.enemy_dragons
        score += max(-0.05, min(0.05, dragon_diff * 0.02))
        return round(max(0.05, min(0.95, score)), 3)

    def get_state(self) -> Dict[str, Any]:
        return self._state.to_dict()


# ---------------------------------------------------------------------------
# Extended: Event Timeline and Kill Feed Parser
# ---------------------------------------------------------------------------

class KillFeedParser:
    """
    Parses kill/assist/death events from network data.

    In Fiddler capture mode, we intercept the game client's event stream.
    In LCU mode, we poll the end-of-game stats for post-mortem analysis.

    Production critique:
        1. User: Kill feed is reconstructed with ~500ms delay from
           actual game events. Strategy advice based on kills is
           therefore reactive, not predictive.
        2. System: Event deduplication uses (killer, victim, timestamp)
           tuple as unique key. Clock skew between client and server
           is normalized to monotonic game time.
    """
    def __init__(self):
        self._events: List[Dict[str, Any]] = []
        self._kill_count_by_team: Dict[int, int] = {100: 0, 200: 0}
        self._death_timers: Dict[str, float] = {}
        self._seen_event_keys: Set[str] = set()

    def record_kill_event(
        self, killer: str, victim: str, assistants: List[str],
        game_time_sec: float, killer_team: int,
        kill_type: str = "champion"
    ) -> bool:
        """Record a kill event. Returns False if duplicate."""
        event_key = f"{killer}:{victim}:{int(game_time_sec)}"
        if event_key in self._seen_event_keys:
            return False
        self._seen_event_keys.add(event_key)
        event = {
            'type': 'kill',
            'killer': killer,
            'victim': victim,
            'assistants': assistants,
            'game_time_sec': game_time_sec,
            'killer_team': killer_team,
            'kill_type': kill_type,
            'timestamp': time.monotonic(),
        }
        self._events.append(event)
        self._kill_count_by_team[killer_team] = (
            self._kill_count_by_team.get(killer_team, 0) + 1)
        # Estimate death timer (simplified formula)
        # Real formula: BRW * (1 + (TIF% * ceil(BRW)))
        base_timer = min(10 + game_time_sec / 60 * 2, 60)
        self._death_timers[victim] = game_time_sec + base_timer
        return True

    def get_recent_kills(
        self, window_sec: float = 30.0
    ) -> List[Dict]:
        """Get kills in the last N seconds."""
        cutoff = time.monotonic() - window_sec
        return [e for e in self._events
                if e['timestamp'] >= cutoff and e['type'] == 'kill']

    def detect_teamfight(
        self, window_sec: float = 15.0, min_kills: int = 3
    ) -> Optional[Dict]:
        """Detect if a teamfight is happening or just happened."""
        recent = self.get_recent_kills(window_sec)
        if len(recent) >= min_kills:
            team_100_kills = sum(
                1 for e in recent if e['killer_team'] == 100)
            team_200_kills = sum(
                1 for e in recent if e['killer_team'] == 200)
            return {
                'is_teamfight': True,
                'total_kills': len(recent),
                'team_100_kills': team_100_kills,
                'team_200_kills': team_200_kills,
                'winning_team': 100 if team_100_kills > team_200_kills else 200,
                'fight_start_time': recent[0]['game_time_sec'],
                'fight_end_time': recent[-1]['game_time_sec'],
            }
        return None

    def get_power_play(self, game_time_sec: float) -> Dict[str, Any]:
        """Check if either team has a numbers advantage (dead enemies)."""
        alive_by_team: Dict[int, int] = {100: 5, 200: 5}
        for victim, respawn_time in self._death_timers.items():
            if respawn_time > game_time_sec:
                # Find which team the victim is on
                for event in reversed(self._events):
                    if event.get('victim') == victim:
                        victim_team = 300 - event['killer_team']
                        alive_by_team[victim_team] = max(
                            0, alive_by_team.get(victim_team, 5) - 1)
                        break
        advantage = alive_by_team.get(100, 5) - alive_by_team.get(200, 5)
        return {
            'team_100_alive': alive_by_team.get(100, 5),
            'team_200_alive': alive_by_team.get(200, 5),
            'advantage_team': 100 if advantage > 0 else (
                200 if advantage < 0 else 0),
            'advantage_count': abs(advantage),
            'is_power_play': abs(advantage) >= 2,
        }

    def get_kill_diff(self) -> int:
        """Team 100 kills minus Team 200 kills."""
        return (self._kill_count_by_team.get(100, 0)
                - self._kill_count_by_team.get(200, 0))

    def get_events_timeline(self) -> List[Dict]:
        return list(self._events)


class GoldDifferentialTracker:
    """
    Tracks gold differential between teams over time.

    Gold advantage is the most reliable predictor of game outcome
    after 15 minutes. We extract gold data from network events
    (scoreboard updates, end-of-game stats).

    Production critique:
        1. User: Gold diff is displayed as a trend line that the
           strategy engine uses to calibrate aggression level.
        2. System: We interpolate between known data points since
           gold updates come at irregular intervals (scoreboard
           press events, end-of-round updates).
    """
    def __init__(self):
        self._data_points: List[Tuple[float, int]] = []
        self._team_gold: Dict[int, int] = {100: 0, 200: 0}

    def record_gold_snapshot(
        self, game_time_sec: float,
        team_100_gold: int, team_200_gold: int
    ) -> None:
        diff = team_100_gold - team_200_gold
        self._data_points.append((game_time_sec, diff))
        self._team_gold[100] = team_100_gold
        self._team_gold[200] = team_200_gold

    def get_current_diff(self) -> int:
        if self._data_points:
            return self._data_points[-1][1]
        return 0

    def get_gold_trend(self, last_n: int = 5) -> str:
        """Is gold diff trending toward team 100 or team 200?"""
        if len(self._data_points) < 2:
            return 'even'
        recent = self._data_points[-last_n:]
        if len(recent) < 2:
            return 'even'
        first_diff = recent[0][1]
        last_diff = recent[-1][1]
        change = last_diff - first_diff
        if change > 500:
            return 'team_100_gaining'
        elif change < -500:
            return 'team_200_gaining'
        return 'stable'

    def estimate_win_probability(self, game_time_sec: float) -> float:
        """
        Estimate team 100 win probability from gold differential.

        Based on empirical data: at 15 min, every 1000g lead
        corresponds to ~5% win probability above 50%.
        Scale factor decreases as game progresses (comebacks harder).
        """
        diff = self.get_current_diff()
        if game_time_sec < 300:  # Before 5 min
            return 0.5
        scale = max(0.01, 0.05 - (game_time_sec - 900) * 0.00005)
        prob = 0.5 + (diff / 1000.0) * scale
        return max(0.05, min(0.95, prob))

    def to_dict(self) -> Dict[str, Any]:
        return {
            'data_points': len(self._data_points),
            'current_diff': self.get_current_diff(),
            'trend': self.get_gold_trend(),
            'team_100_gold': self._team_gold.get(100, 0),
            'team_200_gold': self._team_gold.get(200, 0),
        }
