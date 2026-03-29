"""
Plugin Loader — Dynamic discovery and management of game integration plugins.

Discovers plugins from manifests, supports enable/disable, and
provides plugin info queries.

Location: agentos/cli/plugin_loader.py

Reference (拿来主义):
  - Akagi/mjai_bot/controller.py: list_available_bots() dynamic discovery
  - agentos/governance/game_registry.py: register/unregister/query pattern
  - DI-star: plugin-style model registration
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


class PluginLoader:
    """Dynamic plugin loader for game integrations.

    Mirrors Akagi's Controller.list_available_bots() pattern
    with enable/disable and manifest discovery.
    """

    def __init__(self) -> None:
        self._plugins: dict[str, dict[str, Any]] = {}
        self._enabled: dict[str, bool] = {}

    def register(self, name: str, info: dict[str, Any]) -> None:
        """Register a plugin.

        Args:
            name: Plugin name.
            info: Plugin info dict (game, version, entry, etc.).
        """
        self._plugins[name] = info
        self._enabled[name] = True

    def unregister(self, name: str) -> None:
        """Remove a registered plugin."""
        self._plugins.pop(name, None)
        self._enabled.pop(name, None)

    def list_plugins(self) -> list[str]:
        """List all registered plugin names."""
        return list(self._plugins.keys())

    def list_enabled(self) -> list[str]:
        """List only enabled plugin names."""
        return [n for n, e in self._enabled.items() if e]

    def get_info(self, name: str) -> Optional[dict[str, Any]]:
        """Get plugin info.

        Args:
            name: Plugin name.

        Returns:
            Plugin info dict or None if not found.
        """
        return self._plugins.get(name)

    def is_enabled(self, name: str) -> bool:
        """Check if a plugin is enabled."""
        return self._enabled.get(name, False)

    def enable(self, name: str) -> None:
        """Enable a plugin."""
        if name in self._plugins:
            self._enabled[name] = True

    def disable(self, name: str) -> None:
        """Disable a plugin."""
        if name in self._plugins:
            self._enabled[name] = False

    def discover_from_manifest(self, manifest: dict[str, Any]) -> None:
        """Discover and register plugins from a manifest dict.

        Mirrors Akagi's dynamic bot discovery from directory structure.

        Args:
            manifest: Dict with 'plugins' list of plugin dicts.
        """
        plugins = manifest.get("plugins", [])
        for p in plugins:
            name = p.get("name", "")
            if name:
                self.register(name, p)
