"""
Historical Training Exporter — Convert match history to AgentLightning training spans.

Transforms historical match data into state-action-reward tuples suitable
for AgentLightning self-evolution training loops.

Location: integrations/lol-history/src/lol_history/historical_training_exporter.py
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "lol_history.historical_training_exporter.v1"


class HistoricalTrainingExporter:
    """Export historical match data as AgentLightning training spans.

    Converts match-level statistics into state/action/reward tuples
    with reward clipping and metadata tagging.
    """

    def __init__(
        self,
        min_reward: float = -1.0,
        max_reward: float = 1.0,
    ) -> None:
        self.min_reward = min_reward
        self.max_reward = max_reward
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def match_to_training_span(
        self, match: dict[str, Any], format: str = "default"
    ) -> dict[str, Any]:
        """Convert a single match to a training span.

        Args:
            match: Match dict with champion, win, kills, deaths, assists, etc.
            format: Output format — "default" or "agentlightning".

        Returns:
            Training span dict.
        """
        state = {
            "champion": match.get("champion", ""),
            "gold_per_min": match.get("gold_per_min", 0.0),
            "cs_per_min": match.get("cs_per_min", 0.0),
        }
        action = "win" if match.get("win") else "lose"
        reward = self.compute_reward(match)
        game_id = match.get("gameId", match.get("game_id", ""))

        span: dict[str, Any] = {
            "state": state,
            "action": action,
            "reward": reward,
            "metadata": {"game_id": game_id, "champion": match.get("champion", "")},
        }

        if format == "agentlightning":
            span["observation"] = state
            span["discount"] = 0.99

        return span

    def compute_reward(self, match: dict[str, Any]) -> float:
        """Compute clipped reward from match statistics.

        Args:
            match: Match dict.

        Returns:
            Reward value clipped to [min_reward, max_reward].
        """
        win_bonus = 0.5 if match.get("win") else -0.5
        kills = match.get("kills", 0)
        deaths = match.get("deaths", 0)
        assists = match.get("assists", 0)
        kda_component = (kills + assists * 0.5 - deaths * 0.3) / 20.0
        raw = win_bonus + kda_component
        return max(self.min_reward, min(self.max_reward, raw))

    def batch_export(
        self, matches: list[dict[str, Any]], format: str = "default"
    ) -> list[dict[str, Any]]:
        """Export a batch of matches to training spans.

        Args:
            matches: List of match dicts.
            format: Output format.

        Returns:
            List of training span dicts.
        """
        return [self.match_to_training_span(m, format=format) for m in matches]

    def export_to_json(self, spans: list[dict[str, Any]]) -> str:
        """Serialize spans to JSON string.

        Args:
            spans: List of span dicts.

        Returns:
            JSON string.
        """
        return json.dumps(spans, ensure_ascii=False, indent=2)

    def _fire_evolution(self, data: dict[str, Any]) -> None:
        """Fire evolution callback."""
        if self.evolution_callback is None:
            return
        enriched = {
            "module": "historical_training_exporter",
            "timestamp": time.time(),
            **data,
        }
        try:
            self.evolution_callback(enriched)
        except Exception as exc:
            logger.warning("Evolution callback error: %s", exc)
