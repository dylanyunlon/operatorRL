"""
modules/prediction/timing/death_timer_analyzer.py — Death timer window analysis.
==================================================================================
Claude19 · Wires into PredictionComponent.Proc()

Tracks champion death timers and computes windows of numerical advantage.
When 2+ enemies are dead simultaneously, this is a prime window for
objectives. Published on /lol/death_windows for planning consumption.

Inspired by Apollo's prediction/evaluator/evaluator_manager.cc which
runs multiple evaluation sub-models and merges their outputs.

File location: lolbot-HyperAI/modules/prediction/timing/death_timer_analyzer.py
"""

from __future__ import annotations

import logging
import math
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ─── Constants ───────────────────────────────────────────────────────────────

# Base death timers by level (approximate LoL formula)
# BRW = Level × 2.5 + 7.5 (levels 1-6)
# BRW = Level × 2.5 + 7.5 + growth factor (levels 7-18)
_BASE_RESPAWN_FACTOR_A = 2.5
_BASE_RESPAWN_OFFSET = 7.5
_LEVEL_7_GROWTH_START = 7
_GROWTH_FACTOR_PER_LEVEL = 0.75

# Window significance thresholds
_MIN_WINDOW_DURATION_S = 5.0      # At least 5s of advantage
_SIGNIFICANT_DEAD_COUNT = 2        # 2+ dead = significant window
_DOMINANT_DEAD_COUNT = 3           # 3+ dead = dominant window

# Objective timing constants (seconds to take each)
_DRAGON_TAKE_TIME = 20.0
_BARON_TAKE_TIME = 25.0
_TOWER_TAKE_TIME = 12.0
_HERALD_TAKE_TIME = 15.0


class WindowQuality(Enum):
    """Quality classification of a death-timer window."""
    MARGINAL = auto()     # 1 dead, short window
    SIGNIFICANT = auto()  # 2 dead, medium window
    DOMINANT = auto()     # 3+ dead, long window
    ACE_WINDOW = auto()   # All 5 dead


@dataclass
class DeathRecord:
    """Record of a champion death."""
    summoner_name: str
    champion_name: str
    team: str           # BLUE / RED
    death_game_time: float
    respawn_game_time: float
    level_at_death: int

    @property
    def timer_duration(self) -> float:
        return self.respawn_game_time - self.death_game_time

    def is_dead_at(self, game_time: float) -> bool:
        return self.death_game_time <= game_time < self.respawn_game_time


@dataclass
class DeathWindow:
    """A window of numerical advantage created by enemy deaths."""
    start_time: float
    end_time: float
    dead_champions: List[str]
    dead_count: int
    quality: WindowQuality
    our_alive: int
    enemy_alive: int
    feasible_objectives: List[str]
    window_duration: float = 0.0

    def __post_init__(self):
        self.window_duration = self.end_time - self.start_time

    def to_dict(self) -> Dict[str, Any]:
        return {
            "start_time": round(self.start_time, 1),
            "end_time": round(self.end_time, 1),
            "duration": round(self.window_duration, 1),
            "dead_champions": self.dead_champions,
            "dead_count": self.dead_count,
            "quality": self.quality.name,
            "alive_advantage": f"{self.our_alive}v{self.enemy_alive}",
            "feasible_objectives": self.feasible_objectives,
        }


@dataclass
class DeathTimerReport:
    """Full death timer analysis for a given game moment."""
    game_time: float = 0.0
    blue_dead: List[str] = field(default_factory=list)
    red_dead: List[str] = field(default_factory=list)
    blue_dead_count: int = 0
    red_dead_count: int = 0
    current_window: Optional[DeathWindow] = None
    upcoming_respawns: List[Tuple[str, float]] = field(default_factory=list)
    advantage_team: str = "NONE"
    advantage_score: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "game_time": round(self.game_time, 1),
            "blue_dead": self.blue_dead,
            "red_dead": self.red_dead,
            "advantage_team": self.advantage_team,
            "advantage_score": round(self.advantage_score, 2),
            "current_window": (
                self.current_window.to_dict() if self.current_window else None
            ),
            "upcoming_respawns": [
                (name, round(t, 1)) for name, t in self.upcoming_respawns
            ],
        }


class DeathTimerAnalyzer:
    """Analyzes death timers to identify objective windows.

    Usage::
        analyzer = DeathTimerAnalyzer()
        # Each perception tick:
        analyzer.update_deaths(all_players, game_time)
        report = analyzer.analyze(game_time, active_team="BLUE")
        if report.current_window and report.current_window.quality >= WindowQuality.SIGNIFICANT:
            planning.notify_window(report.current_window)
    """

    def __init__(self) -> None:
        self._active_deaths: Dict[str, DeathRecord] = {}
        self._death_history: List[DeathRecord] = []
        self._analysis_count: int = 0
        self._windows_detected: int = 0
        self._max_history = 200

    def update_deaths(
        self,
        players: List[Any],
        game_time: float,
    ) -> None:
        """Update death records from current player states.

        Scans all players: if is_dead=True and not already tracked,
        creates a new DeathRecord. If was dead and now alive, removes.
        """
        current_dead_names = set()

        for player in players:
            name = getattr(player, "summoner_name", "")
            if not name:
                continue

            is_dead = getattr(player, "is_dead", False)
            respawn_timer = getattr(player, "respawn_timer", 0.0)
            level = getattr(player, "level", 1)
            team_raw = getattr(player, "team", None)
            champion = getattr(player, "champion_name", "")

            # Resolve team string
            if hasattr(team_raw, "value"):
                team_str = "BLUE" if "ORDER" in str(team_raw.value).upper() else "RED"
            elif hasattr(team_raw, "name"):
                team_str = "BLUE" if "BLUE" in team_raw.name.upper() else "RED"
            else:
                team_str = str(team_raw).upper()
                if "ORDER" in team_str or "BLUE" in team_str:
                    team_str = "BLUE"
                else:
                    team_str = "RED"

            if is_dead:
                current_dead_names.add(name)
                if name not in self._active_deaths:
                    # Estimate respawn time
                    if respawn_timer > 0:
                        respawn_at = game_time + respawn_timer
                    else:
                        respawn_at = game_time + self._estimate_respawn(level, game_time)

                    record = DeathRecord(
                        summoner_name=name,
                        champion_name=champion,
                        team=team_str,
                        death_game_time=game_time,
                        respawn_game_time=respawn_at,
                        level_at_death=level,
                    )
                    self._active_deaths[name] = record
                    self._death_history.append(record)
                    if len(self._death_history) > self._max_history:
                        self._death_history = self._death_history[-self._max_history:]
                else:
                    # Update respawn timer if LCU provides better data
                    if respawn_timer > 0:
                        existing = self._active_deaths[name]
                        new_respawn = game_time + respawn_timer
                        if abs(new_respawn - existing.respawn_game_time) > 1.0:
                            existing.respawn_game_time = new_respawn

        # Clean up players who respawned
        expired = [
            name for name in self._active_deaths
            if name not in current_dead_names
        ]
        for name in expired:
            del self._active_deaths[name]

    def analyze(
        self,
        game_time: float,
        active_team: str = "BLUE",
    ) -> DeathTimerReport:
        """Analyze current death state and identify windows.

        Args:
            game_time: Current game time.
            active_team: Our team (BLUE or RED).

        Returns:
            DeathTimerReport with current window analysis.
        """
        self._analysis_count += 1
        enemy_team = "RED" if active_team == "BLUE" else "BLUE"

        # Partition deaths by team
        blue_dead = []
        red_dead = []
        for name, record in self._active_deaths.items():
            if record.is_dead_at(game_time):
                if record.team == "BLUE":
                    blue_dead.append(record)
                else:
                    red_dead.append(record)

        # Our dead vs enemy dead
        our_dead = blue_dead if active_team == "BLUE" else red_dead
        enemy_dead = red_dead if active_team == "BLUE" else blue_dead

        our_alive = 5 - len(our_dead)
        enemy_alive = 5 - len(enemy_dead)

        # Compute advantage score
        advantage_score = 0.0
        for record in enemy_dead:
            remaining = record.respawn_game_time - game_time
            advantage_score += max(0, remaining / 10.0)
        for record in our_dead:
            remaining = record.respawn_game_time - game_time
            advantage_score -= max(0, remaining / 10.0)

        advantage_team = "NONE"
        if advantage_score > 0.5:
            advantage_team = active_team
        elif advantage_score < -0.5:
            advantage_team = enemy_team

        # Compute current window if we have numerical advantage
        current_window = None
        if len(enemy_dead) >= 1 and len(enemy_dead) > len(our_dead):
            # Window ends when the first enemy respawns
            respawn_times = sorted(
                r.respawn_game_time for r in enemy_dead
            )
            # The window where we have at least this many dead enemies
            # ends when the first one respawns
            window_end = respawn_times[0]
            window_duration = window_end - game_time

            if window_duration >= _MIN_WINDOW_DURATION_S:
                # Quality classification
                if len(enemy_dead) >= 5:
                    quality = WindowQuality.ACE_WINDOW
                elif len(enemy_dead) >= _DOMINANT_DEAD_COUNT:
                    quality = WindowQuality.DOMINANT
                elif len(enemy_dead) >= _SIGNIFICANT_DEAD_COUNT:
                    quality = WindowQuality.SIGNIFICANT
                else:
                    quality = WindowQuality.MARGINAL

                feasible = self._compute_feasible_objectives(
                    window_duration, game_time,
                )

                current_window = DeathWindow(
                    start_time=game_time,
                    end_time=window_end,
                    dead_champions=[r.champion_name for r in enemy_dead],
                    dead_count=len(enemy_dead),
                    quality=quality,
                    our_alive=our_alive,
                    enemy_alive=enemy_alive,
                    feasible_objectives=feasible,
                )
                self._windows_detected += 1

        # Upcoming respawns (sorted by time)
        all_dead = list(self._active_deaths.values())
        upcoming = sorted(
            [(r.summoner_name, r.respawn_game_time) for r in all_dead
             if r.respawn_game_time > game_time],
            key=lambda x: x[1],
        )[:5]

        return DeathTimerReport(
            game_time=game_time,
            blue_dead=[r.champion_name for r in blue_dead],
            red_dead=[r.champion_name for r in red_dead],
            blue_dead_count=len(blue_dead),
            red_dead_count=len(red_dead),
            current_window=current_window,
            upcoming_respawns=upcoming,
            advantage_team=advantage_team,
            advantage_score=advantage_score,
        )

    def _estimate_respawn(self, level: int, game_time: float) -> float:
        """Estimate respawn time from champion level.

        Uses LoL's respawn formula:
            BRW = Level × 2.5 + 7.5  (levels 1-6)
            BRW += (Level - 6) × growth  (levels 7+)
            Late-game multiplier after 15min
        """
        base = level * _BASE_RESPAWN_FACTOR_A + _BASE_RESPAWN_OFFSET
        if level >= _LEVEL_7_GROWTH_START:
            extra_levels = level - 6
            base += extra_levels * _GROWTH_FACTOR_PER_LEVEL * extra_levels

        # Late game multiplier (after 15 min)
        if game_time > 900.0:
            time_factor = min(1.5, 1.0 + (game_time - 900.0) / 3600.0)
            base *= time_factor

        return max(6.0, base)

    def _compute_feasible_objectives(
        self, window_duration: float, game_time: float,
    ) -> List[str]:
        """Determine which objectives can be taken in the window."""
        feasible = []

        if window_duration >= _TOWER_TAKE_TIME:
            feasible.append("tower")
        if window_duration >= _HERALD_TAKE_TIME and game_time < 1200.0:
            feasible.append("herald")
        if window_duration >= _DRAGON_TAKE_TIME:
            feasible.append("dragon")
        if window_duration >= _BARON_TAKE_TIME and game_time >= 1200.0:
            feasible.append("baron")
        if window_duration >= _TOWER_TAKE_TIME * 2:
            feasible.append("inhibitor")

        return feasible

    def stats(self) -> Dict[str, Any]:
        return {
            "analysis_count": self._analysis_count,
            "windows_detected": self._windows_detected,
            "active_deaths": len(self._active_deaths),
            "death_history_size": len(self._death_history),
        }

    def reset(self) -> None:
        self._active_deaths.clear()
        self._death_history.clear()
        self._analysis_count = 0
        self._windows_detected = 0
