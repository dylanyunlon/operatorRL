"""
GlobalData — Singleton holding runtime configuration and process info.
=======================================================================

Apollo reference: ``cyber/common/global_data.cc``

GlobalData is the single source of truth for process-wide runtime
config: host name, process ID, component list, module topology, etc.
All components read from GlobalData during Init().

Claude27: New file.
Location: lolbot-HyperAI/cyber/common/global_data.py
"""

from __future__ import annotations

import logging
import os
import platform
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ProcessInfo:
    """Current process information."""
    pid: int = 0
    hostname: str = ""
    start_time: float = 0.0
    python_version: str = ""
    platform: str = ""


class GlobalData:
    """Singleton holding runtime configuration and process info.

    Apollo equivalent: ``cyber::common::GlobalData``

    Usage::

        gd = GlobalData.instance()
        gd.set_config("canbus.interval_ms", 100.0)
        val = gd.get_config("canbus.interval_ms", default=100.0)
    """

    _instance: Optional["GlobalData"] = None
    _init_lock = threading.Lock()

    @classmethod
    def instance(cls) -> "GlobalData":
        if cls._instance is None:
            with cls._init_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Reset singleton (for testing)."""
        with cls._init_lock:
            cls._instance = None

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._config: Dict[str, Any] = {}
        self._components: List[str] = []
        self._process_info = ProcessInfo(
            pid=os.getpid(),
            hostname=platform.node(),
            start_time=time.time(),
            python_version=platform.python_version(),
            platform=platform.platform(),
        )
        self._initialized = False

    @property
    def process_info(self) -> ProcessInfo:
        return self._process_info

    @property
    def is_initialized(self) -> bool:
        return self._initialized

    def init(self, config: Optional[Dict[str, Any]] = None) -> None:
        """Initialize GlobalData with optional config dict.

        Apollo equivalent: ``GlobalData::Init()``

        Called once at startup. Subsequent calls merge config.
        """
        with self._lock:
            if config:
                self._config.update(config)
            self._initialized = True
        logger.info(
            "GlobalData initialized (pid=%d, host=%s)",
            self._process_info.pid,
            self._process_info.hostname,
        )

    # ── Config access ────────────────────────────────────────────────────

    def set_config(self, key: str, value: Any) -> None:
        """Set a config value by dotted key."""
        with self._lock:
            self._config[key] = value

    def get_config(self, key: str, default: Any = None) -> Any:
        """Get a config value by dotted key."""
        with self._lock:
            return self._config.get(key, default)

    def has_config(self, key: str) -> bool:
        with self._lock:
            return key in self._config

    def all_config(self) -> Dict[str, Any]:
        """Return a copy of all config."""
        with self._lock:
            return dict(self._config)

    # ── Component registry ───────────────────────────────────────────────

    def register_component(self, name: str) -> None:
        """Register a component name with GlobalData."""
        with self._lock:
            if name not in self._components:
                self._components.append(name)

    def unregister_component(self, name: str) -> None:
        with self._lock:
            if name in self._components:
                self._components.remove(name)

    def component_names(self) -> List[str]:
        with self._lock:
            return list(self._components)

    # ── Env variable bridging ────────────────────────────────────────────

    def get_env(self, key: str, default: str = "") -> str:
        """Get environment variable with fallback.

        Apollo pattern: many flags come from env (e.g., CYBER_IP).
        """
        return os.environ.get(key, default)

    def set_env(self, key: str, value: str) -> None:
        """Set environment variable."""
        os.environ[key] = value

    # ── Snapshot ─────────────────────────────────────────────────────────

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "initialized": self._initialized,
                "process": {
                    "pid": self._process_info.pid,
                    "hostname": self._process_info.hostname,
                    "start_time": self._process_info.start_time,
                    "uptime_s": round(
                        time.time() - self._process_info.start_time, 1
                    ),
                    "python_version": self._process_info.python_version,
                },
                "config_keys": list(self._config.keys()),
                "component_count": len(self._components),
                "components": list(self._components),
            }

    def __repr__(self) -> str:
        return (
            f"<GlobalData pid={self._process_info.pid} "
            f"components={len(self._components)} "
            f"config_keys={len(self._config)}>"
        )
