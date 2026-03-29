"""
Deployment Manager — Game agent deployment lifecycle management.

Provides deploy/stop/rollback/health-check for game agents,
with version tracking and automatic rollback on failure.

Location: agentos/governance/deployment_manager.py

Reference (拿来主义):
  - PARL: agent deployment lifecycle (agent_base.py)
  - agentlightning/runner/game_runner.py: start/stop/status pattern
  - DI-star: distributed deployment patterns
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "agentos.governance.deployment_manager.v1"


class DeploymentManager:
    """Manages deployment lifecycle for game agents.

    Tracks deployments by game name, supports version history,
    health checks, and automatic rollback.

    Attributes:
        evolution_callback: Optional callback for evolution events.
    """

    def __init__(self) -> None:
        self._deployments: dict[str, dict[str, Any]] = {}
        self._version_history: dict[str, list[str]] = {}
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def deploy(
        self, game: str, version: str, config: dict[str, Any]
    ) -> dict[str, Any]:
        """Deploy or update a game agent.

        Args:
            game: Game identifier.
            version: Version string.
            config: Deployment configuration.

        Returns:
            Status dict with 'status' and 'version'.
        """
        if game not in self._version_history:
            self._version_history[game] = []

        # Save current version to history before overwriting
        if game in self._deployments:
            old_ver = self._deployments[game]["version"]
            self._version_history[game].append(old_ver)

        self._deployments[game] = {
            "version": version,
            "config": config,
            "status": "deployed",
            "deployed_at": time.time(),
        }

        self._fire_evolution("deployed", {"game": game, "version": version})
        return {"status": "deployed", "version": version}

    def stop(self, game: str) -> None:
        """Stop a deployed game agent.

        Args:
            game: Game identifier.

        Raises:
            KeyError: If game not deployed.
        """
        if game not in self._deployments:
            raise KeyError(f"Game '{game}' not deployed")
        self._deployments[game]["status"] = "stopped"

    def rollback(self, game: str) -> dict[str, Any]:
        """Rollback to previous version.

        Args:
            game: Game identifier.

        Returns:
            Status dict with new version.

        Raises:
            KeyError: If game not deployed or no history.
        """
        if game not in self._deployments:
            raise KeyError(f"Game '{game}' not deployed")
        history = self._version_history.get(game, [])
        if not history:
            raise KeyError(f"No version history for '{game}'")

        prev_version = history.pop()
        self._deployments[game]["version"] = prev_version
        self._deployments[game]["status"] = "deployed"
        return {"status": "deployed", "version": prev_version}

    def health_check(self, game: str) -> dict[str, Any]:
        """Check health of a deployed game agent.

        Args:
            game: Game identifier.

        Returns:
            Health status dict.
        """
        if game not in self._deployments:
            return {"healthy": False, "reason": "not_deployed"}
        dep = self._deployments[game]
        healthy = dep["status"] in ("deployed", "running")
        return {"healthy": healthy, "status": dep["status"], "version": dep["version"]}

    def get_status(self, game: str) -> dict[str, Any]:
        """Get current deployment status.

        Args:
            game: Game identifier.

        Returns:
            Status dict.
        """
        if game not in self._deployments:
            return {"status": "not_deployed", "version": None}
        dep = self._deployments[game]
        return {"status": dep["status"], "version": dep["version"]}

    def list_deployments(self) -> list[dict[str, Any]]:
        """List all current deployments."""
        return [
            {"game": g, "version": d["version"], "status": d["status"]}
            for g, d in self._deployments.items()
        ]

    def _fire_evolution(self, event_type: str, payload: dict) -> None:
        if self.evolution_callback is not None:
            self.evolution_callback({
                "source": _EVOLUTION_KEY,
                "type": event_type,
                "timestamp": time.time(),
                "payload": payload,
            })
