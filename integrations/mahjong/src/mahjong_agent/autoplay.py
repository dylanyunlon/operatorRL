"""
Autoplay Controller — Automated mahjong game session management.

Manages automated game sessions: queueing, playing, collecting data,
and feeding results to the evolution loop.

Location: integrations/mahjong/src/mahjong_agent/autoplay.py
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "mahjong_agent.autoplay.v1"


@dataclass
class AutoplayConfig:
    """Configuration for autoplay controller."""
    max_games: int = 100
    platform: str = "majsoul"
    delay_between_games: float = 5.0
    auto_collect_training: bool = True
    target_rank: str = ""


@dataclass
class GameResult:
    """Result of a single automated game."""
    game_id: str = ""
    win: bool = False
    placement: int = 4  # 1-4
    score_delta: int = 0
    duration: float = 0.0
    decisions: int = 0
    timestamp: float = field(default_factory=time.time)


class AutoplayController:
    """Automated game session controller.

    Manages the lifecycle of automated game sessions:
    - Start/stop session
    - Queue and play games
    - Track statistics
    - Feed data to evolution loop
    """

    def __init__(self, config: AutoplayConfig | None = None) -> None:
        self.config = config or AutoplayConfig()
        self._state: str = "idle"
        self._games_played: int = 0
        self._results: list[GameResult] = []
        self._session_start: Optional[float] = None

    @property
    def state(self) -> str:
        return self._state

    @property
    def games_played(self) -> int:
        return self._games_played

    def start(self) -> None:
        """Start autoplay session."""
        self._state = "running"
        self._session_start = time.time()
        logger.info("Autoplay started: platform=%s, max_games=%d",
                     self.config.platform, self.config.max_games)

    def stop(self) -> None:
        """Stop autoplay session."""
        self._state = "idle"
        elapsed = time.time() - self._session_start if self._session_start else 0
        logger.info("Autoplay stopped: games=%d, elapsed=%.1fs",
                     self._games_played, elapsed)

    def run_one_game(self) -> GameResult:
        """Run one automated game.

        In production, this would:
        1. Connect to game server via bridge
        2. Play using MahjongAgent
        3. Collect trajectory data
        4. Return result

        Here we simulate a minimal game.
        """
        game_start = time.time()

        result = GameResult(
            game_id=f"auto-{self._games_played + 1}",
            win=False,
            placement=4,
            score_delta=0,
            duration=time.time() - game_start,
            decisions=0,
        )

        self._results.append(result)
        self._games_played += 1

        # Auto-stop if max games reached
        if self._games_played >= self.config.max_games:
            self.stop()

        return result

    def get_stats(self) -> dict[str, Any]:
        """Get autoplay statistics."""
        wins = sum(1 for r in self._results if r.win)
        total = len(self._results)
        return {
            "games_played": self._games_played,
            "win_rate": wins / max(total, 1),
            "avg_placement": sum(r.placement for r in self._results) / max(total, 1) if total else 0,
            "total_score_delta": sum(r.score_delta for r in self._results),
            "platform": self.config.platform,
            "state": self._state,
            "session_duration": time.time() - self._session_start if self._session_start else 0,
        }

    def get_results(self) -> list[dict[str, Any]]:
        """Get all game results."""
        return [
            {
                "game_id": r.game_id,
                "win": r.win,
                "placement": r.placement,
                "score_delta": r.score_delta,
                "duration": r.duration,
            }
            for r in self._results
        ]
