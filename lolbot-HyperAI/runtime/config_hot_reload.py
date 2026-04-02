"""
ConfigHotReload — File-watch config reload with change broadcast.
===================================================================
lolbot-HyperAI · Runtime

Watches config file mtime, detects changes, broadcasts diffs on
``/lol/config_update`` for live parameter tuning without restart.

Architecture position:
    runtime/config_hot_reload.py   ← YOU ARE HERE
    ├─ Reads: data/config.json (or configured path)
    ├─ Publishes: /lol/config_update (ConfigDelta)
    └─ Consumed by: any component with dynamic parameters

Design notes:
    - Polls mtime at 1Hz (not inotify — portable across OS)
    - Publishes only changed keys as ConfigDelta
    - Validates JSON before applying (rejects malformed files)
    - Rate-limited: max 1 reload per 5s to prevent thrashing
"""

from __future__ import annotations
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional
from cyber.component.timer_component import ComponentConfig, TimerComponent
from cyber.node.node import CyberNode, Writer
from cyber.logger.cyber_logger import get_logger

logger = get_logger("runtime.config")

_CHECK_INTERVAL_MS = 1000.0
_MIN_RELOAD_INTERVAL_S = 5.0


@dataclass(frozen=True)
class ConfigDelta:
    """Published on /lol/config_update when config changes."""
    changed_keys: Dict[str, Any] = field(default_factory=dict)
    removed_keys: tuple = ()
    config_path: str = ""
    reload_count: int = 0
    timestamp: float = field(default_factory=time.time)


class ConfigHotReload(TimerComponent):
    """Watches config file and broadcasts changes."""

    def __init__(self, config_path: str = "data/config.json") -> None:
        super().__init__(
            config=ComponentConfig(
                name="config_hot_reload",
                interval_ms=_CHECK_INTERVAL_MS,
                warn_threshold_ms=800.0,
            ),
        )
        self._config_path = Path(config_path)
        self._node: Optional[CyberNode] = None
        self._writer: Optional[Writer] = None
        self._last_mtime: float = 0.0
        self._last_reload_time: float = 0.0
        self._current_config: Dict[str, Any] = {}
        self._reload_count: int = 0

    def Init(self) -> bool:
        logger.info("Initializing ConfigHotReload: %s", self._config_path)
        self._node = CyberNode("config_hot_reload")
        self._writer = self._node.CreateWriter(
            "/lol/config_update", ConfigDelta,
        )
        # Load initial config
        self._current_config = self._load_config()
        if self._config_path.exists():
            self._last_mtime = os.path.getmtime(self._config_path)
        logger.info("ConfigHotReload initialized (%d keys)",
                     len(self._current_config))
        return True

    def Proc(self) -> bool:
        if not self._config_path.exists():
            return True

        current_mtime = os.path.getmtime(self._config_path)
        if current_mtime <= self._last_mtime:
            return True  # No change

        now = time.time()
        if now - self._last_reload_time < _MIN_RELOAD_INTERVAL_S:
            return True  # Rate limited

        # Attempt reload
        new_config = self._load_config()
        if new_config is None:
            logger.warning("Config reload failed (invalid JSON), skipping")
            return True

        # Compute diff
        changed = {}
        removed = []
        for key, val in new_config.items():
            if key not in self._current_config or self._current_config[key] != val:
                changed[key] = val
        for key in self._current_config:
            if key not in new_config:
                removed.append(key)

        if not changed and not removed:
            self._last_mtime = current_mtime
            return True

        # Broadcast
        self._reload_count += 1
        delta = ConfigDelta(
            changed_keys=changed,
            removed_keys=tuple(removed),
            config_path=str(self._config_path),
            reload_count=self._reload_count,
        )
        if self._writer:
            self._writer.Write(delta)

        self._current_config = new_config
        self._last_mtime = current_mtime
        self._last_reload_time = now

        logger.info(
            "Config reloaded (#%d): %d changed, %d removed",
            self._reload_count, len(changed), len(removed),
        )
        return True

    def on_shutdown(self) -> None:
        if self._node:
            self._node.shutdown()

    def _load_config(self) -> Optional[Dict[str, Any]]:
        try:
            with open(self._config_path) as f:
                return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError, PermissionError) as exc:
            logger.error("Config load error: %s", exc)
            return None if self._current_config else {}
