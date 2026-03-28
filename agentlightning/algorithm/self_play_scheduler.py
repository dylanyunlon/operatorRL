"""
Self-Play Scheduler — Multi-agent self-play management.

Provides agent pool management, Elo-based matchmaking, and
result tracking. Adapted from DI-star's self-play loop and
open_spiel's self-play examples.

Location: agentlightning/algorithm/self_play_scheduler.py

Reference: DI-star self-play, open_spiel self-play examples.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "agentlightning.algorithm.self_play_scheduler.v1"

_K_FACTOR = 32  # Elo K-factor


class SelfPlayScheduler:
    """Self-play matchmaking scheduler with Elo ratings.

    Manages a pool of agent versions, schedules matches between
    them based on Elo proximity, and updates ratings after results.
    """

    def __init__(self) -> None:
        self._agents: dict[str, float] = {}  # agent_id -> elo
        self.match_history: list[dict[str, Any]] = []
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    @property
    def pool_size(self) -> int:
        return len(self._agents)

    def add_agent(self, agent_id: str, elo: float = 1000) -> None:
        """Add an agent to the pool.

        Args:
            agent_id: Unique agent identifier.
            elo: Initial Elo rating.
        """
        self._agents[agent_id] = elo

    def schedule_match(self) -> Optional[dict[str, Any]]:
        """Schedule a match between two agents.

        Selects the two agents with closest Elo ratings.

        Returns:
            Match dict with player_a, player_b, or None if < 2 agents.
        """
        if len(self._agents) < 2:
            return None

        sorted_agents = sorted(self._agents.items(), key=lambda x: x[1])
        # Pick pair with smallest Elo gap
        best_pair = None
        best_gap = float("inf")
        for i in range(len(sorted_agents) - 1):
            gap = abs(sorted_agents[i + 1][1] - sorted_agents[i][1])
            if gap < best_gap:
                best_gap = gap
                best_pair = (sorted_agents[i][0], sorted_agents[i + 1][0])

        if best_pair is None:
            return None

        return {"player_a": best_pair[0], "player_b": best_pair[1]}

    def record_result(
        self, player_a: str, player_b: str, winner: str
    ) -> None:
        """Record match result and update Elo ratings.

        Args:
            player_a: First player ID.
            player_b: Second player ID.
            winner: Winner's player ID.
        """
        elo_a = self._agents.get(player_a, 1000)
        elo_b = self._agents.get(player_b, 1000)

        expected_a = 1.0 / (1.0 + 10 ** ((elo_b - elo_a) / 400.0))
        expected_b = 1.0 - expected_a

        score_a = 1.0 if winner == player_a else 0.0
        score_b = 1.0 - score_a

        self._agents[player_a] = elo_a + _K_FACTOR * (score_a - expected_a)
        self._agents[player_b] = elo_b + _K_FACTOR * (score_b - expected_b)

        self.match_history.append({
            "player_a": player_a,
            "player_b": player_b,
            "winner": winner,
            "elo_a_after": self._agents[player_a],
            "elo_b_after": self._agents[player_b],
        })

    def get_elo(self, agent_id: str) -> float:
        """Get Elo rating for an agent.

        Args:
            agent_id: Agent identifier.

        Returns:
            Elo rating.
        """
        return self._agents.get(agent_id, 1000)

    def get_leaderboard(self) -> list[dict[str, Any]]:
        """Get sorted leaderboard.

        Returns:
            List of dicts sorted by Elo descending.
        """
        return sorted(
            [{"agent_id": k, "elo": v} for k, v in self._agents.items()],
            key=lambda x: x["elo"],
            reverse=True,
        )

    def _fire_evolution(self, payload: dict[str, Any]) -> None:
        if self.evolution_callback is not None:
            try:
                self.evolution_callback(payload)
            except Exception as exc:
                logger.warning("Evolution callback error: %s", exc)
