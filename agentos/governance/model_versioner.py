"""
Model Versioner — Model weight version tracking and management.

Provides save/load/rollback/diff for model weights, with version
history and evolution callback hooks.

Location: agentos/governance/model_versioner.py

Reference (拿来主义):
  - DI-star: checkpoint save/load patterns
  - agentlightning/trainer/multi_game_trainer.py: checkpoint management
  - operatorRL: model versioning design from plan.md
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "agentos.governance.model_versioner.v1"


class ModelVersioner:
    """Model weight version manager.

    Stores named model versions with weights, supports rollback
    to previous versions and diff between versions.

    Attributes:
        evolution_callback: Optional callback for evolution events.
    """

    def __init__(self) -> None:
        # model_name → list of {version, weights, saved_at}
        self._store: dict[str, list[dict[str, Any]]] = {}
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def save(
        self, model_name: str, version: str, weights: dict[str, Any]
    ) -> None:
        """Save a model version.

        Args:
            model_name: Model identifier.
            version: Version string.
            weights: Weight dict to store.
        """
        if model_name not in self._store:
            self._store[model_name] = []

        self._store[model_name].append({
            "version": version,
            "weights": weights,
            "saved_at": time.time(),
        })

        self._fire_evolution("version_saved", {
            "model": model_name,
            "version": version,
        })

    def load(self, model_name: str, version: str) -> dict[str, Any]:
        """Load a specific model version.

        Args:
            model_name: Model identifier.
            version: Version string.

        Returns:
            Weight dict.

        Raises:
            KeyError: If model or version not found.
        """
        if model_name not in self._store:
            raise KeyError(f"Model '{model_name}' not found")
        for entry in self._store[model_name]:
            if entry["version"] == version:
                return entry["weights"]
        raise KeyError(f"Version '{version}' not found for '{model_name}'")

    def load_latest(self, model_name: str) -> dict[str, Any]:
        """Load the latest version of a model.

        Args:
            model_name: Model identifier.

        Returns:
            Weight dict of latest version.

        Raises:
            KeyError: If model not found or no versions.
        """
        if model_name not in self._store or not self._store[model_name]:
            raise KeyError(f"Model '{model_name}' not found or empty")
        return self._store[model_name][-1]["weights"]

    def rollback(self, model_name: str) -> None:
        """Remove the latest version, rolling back to previous.

        Args:
            model_name: Model identifier.

        Raises:
            KeyError: If model not found or only one version.
        """
        if model_name not in self._store or len(self._store[model_name]) < 2:
            raise KeyError(f"Cannot rollback '{model_name}': insufficient versions")
        self._store[model_name].pop()

    def list_versions(self, model_name: str) -> list[str]:
        """List all version strings for a model.

        Args:
            model_name: Model identifier.

        Returns:
            List of version strings.
        """
        if model_name not in self._store:
            return []
        return [e["version"] for e in self._store[model_name]]

    def version_count(self, model_name: str) -> int:
        """Number of stored versions for a model."""
        return len(self._store.get(model_name, []))

    def diff(
        self, model_name: str, version_a: str, version_b: str
    ) -> dict[str, Any]:
        """Compute diff between two versions.

        Args:
            model_name: Model identifier.
            version_a: First version.
            version_b: Second version.

        Returns:
            Dict with changed keys and their old/new values.
        """
        weights_a = self.load(model_name, version_a)
        weights_b = self.load(model_name, version_b)

        changes = {}
        all_keys = set(weights_a.keys()) | set(weights_b.keys())
        for key in all_keys:
            val_a = weights_a.get(key)
            val_b = weights_b.get(key)
            if val_a != val_b:
                changes[key] = {"old": val_a, "new": val_b}

        return changes

    def _fire_evolution(self, event_type: str, payload: dict) -> None:
        if self.evolution_callback is not None:
            self.evolution_callback({
                "source": _EVOLUTION_KEY,
                "type": event_type,
                "timestamp": time.time(),
                "payload": payload,
            })
