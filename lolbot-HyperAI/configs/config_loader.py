"""
ConfigLoader — YAML configuration with environment overrides and hot-reload.
==============================================================================
lolbot-HyperAI · Config Layer

Loads pipeline configuration from YAML files, supports environment
variable overrides, provides type-safe config access, and optionally
watches for file changes to hot-reload configuration.

Architecture position:
    configs/config_loader.py   ← YOU ARE HERE
    ├─ Loads: configs/pipeline.yaml (main config)
    ├─ Used by: launch/mainboard.py (startup)
    ├─ Used by: all components via get_config()
    └─ Replaces: conf/default_config.py for YAML-based configs

Apollo reference:
    modules/common/configs/config_gflags.cc — flag-based config
    cyber/conf/ — CyberRT configuration files

Design notes:
    - YAML parsing via stdlib-compatible recursive descent (no PyYAML required)
    - Simple YAML subset: key:value, nested dicts, lists, scalars
    - Environment variable override: LOLBOT_SECTION_KEY=value
    - Type coercion: strings auto-converted to int/float/bool where expected
    - Config validation against schema
    - Thread-safe read access
    - Hot-reload: poll file mtime for changes
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union

from cyber.logger.cyber_logger import get_logger

logger = get_logger("configs.loader")

# ─── Constants ───────────────────────────────────────────────────────────────

_ENV_PREFIX = "LOLBOT_"
_HOT_RELOAD_INTERVAL_S = 5.0
_MAX_YAML_SIZE = 1024 * 1024  # 1 MB max config file size


# ─── Simple YAML Parser (stdlib only) ───────────────────────────────────────

class SimpleYAMLParser:
    """Minimal YAML parser supporting the subset we need.

    Supports:
    - Key-value pairs (scalars: str, int, float, bool, null)
    - Nested mappings (indentation-based)
    - Lists (- item syntax)
    - Comments (# ...)
    - Quoted strings ("..." and '...')

    Does NOT support:
    - Multi-line strings, anchors, aliases, tags, flow syntax
    """

    BOOL_TRUE = {"true", "yes", "on", "True", "TRUE", "Yes", "YES"}
    BOOL_FALSE = {"false", "no", "off", "False", "FALSE", "No", "NO"}
    NULL_VALUES = {"null", "~", "Null", "NULL", "None"}

    def parse(self, text: str) -> Dict[str, Any]:
        """Parse a YAML string into a dict."""
        lines = text.splitlines()
        return self._parse_mapping(lines, 0, 0)[0]

    def _parse_mapping(
        self,
        lines: List[str],
        start: int,
        base_indent: int,
    ) -> Tuple[Dict[str, Any], int]:
        """Parse a YAML mapping (dict) at the given indentation level."""
        result: Dict[str, Any] = {}
        i = start

        while i < len(lines):
            line = lines[i]
            stripped = line.lstrip()

            # Skip empty lines and comments
            if not stripped or stripped.startswith("#"):
                i += 1
                continue

            # Calculate indentation
            indent = len(line) - len(stripped)

            # If indent is less than base, we've left this mapping
            if indent < base_indent:
                break

            # If indent is greater, skip (handled by parent)
            if indent > base_indent:
                i += 1
                continue

            # List item
            if stripped.startswith("- "):
                break  # Lists are handled by the parent

            # Key: value pair
            if ":" in stripped:
                colon_idx = stripped.index(":")
                key = stripped[:colon_idx].strip()
                value_str = stripped[colon_idx + 1:].strip()

                # Remove inline comments
                if " #" in value_str:
                    value_str = value_str[:value_str.index(" #")].strip()

                if not value_str:
                    # Check next line for nested mapping or list
                    next_indent = self._peek_indent(lines, i + 1)
                    if next_indent > indent:
                        next_stripped = self._peek_stripped(lines, i + 1)
                        if next_stripped.startswith("- "):
                            lst, i = self._parse_list(lines, i + 1, next_indent)
                            result[key] = lst
                        else:
                            mapping, i = self._parse_mapping(lines, i + 1, next_indent)
                            result[key] = mapping
                    else:
                        result[key] = None
                        i += 1
                else:
                    result[key] = self._parse_scalar(value_str)
                    i += 1
            else:
                i += 1

        return result, i

    def _parse_list(
        self,
        lines: List[str],
        start: int,
        base_indent: int,
    ) -> Tuple[List[Any], int]:
        """Parse a YAML list at the given indentation level."""
        result: List[Any] = []
        i = start

        while i < len(lines):
            line = lines[i]
            stripped = line.lstrip()

            if not stripped or stripped.startswith("#"):
                i += 1
                continue

            indent = len(line) - len(stripped)

            if indent < base_indent:
                break

            if indent == base_indent and stripped.startswith("- "):
                item_str = stripped[2:].strip()
                if item_str:
                    result.append(self._parse_scalar(item_str))
                else:
                    # Nested structure after list item
                    next_indent = self._peek_indent(lines, i + 1)
                    if next_indent > indent:
                        mapping, i = self._parse_mapping(lines, i + 1, next_indent)
                        result.append(mapping)
                        continue
                    else:
                        result.append(None)
                i += 1
            else:
                i += 1

        return result, i

    def _parse_scalar(self, value: str) -> Any:
        """Parse a scalar value with type inference."""
        if not value:
            return None

        # Quoted strings
        if (value.startswith('"') and value.endswith('"')) or \
           (value.startswith("'") and value.endswith("'")):
            return value[1:-1]

        # Null
        if value in self.NULL_VALUES:
            return None

        # Boolean
        if value in self.BOOL_TRUE:
            return True
        if value in self.BOOL_FALSE:
            return False

        # Integer
        try:
            return int(value)
        except ValueError:
            pass

        # Float
        try:
            return float(value)
        except ValueError:
            pass

        return value

    def _peek_indent(self, lines: List[str], idx: int) -> int:
        """Peek at the indentation of the next non-empty line."""
        while idx < len(lines):
            stripped = lines[idx].lstrip()
            if stripped and not stripped.startswith("#"):
                return len(lines[idx]) - len(stripped)
            idx += 1
        return 0

    def _peek_stripped(self, lines: List[str], idx: int) -> str:
        while idx < len(lines):
            stripped = lines[idx].lstrip()
            if stripped and not stripped.startswith("#"):
                return stripped
            idx += 1
        return ""


# ─── Config Value Access ─────────────────────────────────────────────────────

class ConfigView:
    """Provides dot-notation and bracket access to nested config dicts.

    Example::

        cfg = ConfigView({"server": {"port": 8080}})
        assert cfg["server.port"] == 8080
        assert cfg.get("server.host", "localhost") == "localhost"
    """

    def __init__(self, data: Dict[str, Any]) -> None:
        self._data = data

    def __getitem__(self, dotted_key: str) -> Any:
        """Access a value by dotted key (e.g. 'server.port')."""
        parts = dotted_key.split(".")
        current = self._data
        for part in parts:
            if isinstance(current, dict):
                if part not in current:
                    raise KeyError(f"Config key not found: {dotted_key}")
                current = current[part]
            else:
                raise KeyError(f"Config key not found: {dotted_key}")
        return current

    def get(self, dotted_key: str, default: Any = None) -> Any:
        """Access with a default value."""
        try:
            return self[dotted_key]
        except (KeyError, TypeError):
            return default

    def get_int(self, key: str, default: int = 0) -> int:
        val = self.get(key, default)
        return int(val) if val is not None else default

    def get_float(self, key: str, default: float = 0.0) -> float:
        val = self.get(key, default)
        return float(val) if val is not None else default

    def get_bool(self, key: str, default: bool = False) -> bool:
        val = self.get(key, default)
        if isinstance(val, bool):
            return val
        if isinstance(val, str):
            return val.lower() in SimpleYAMLParser.BOOL_TRUE
        return bool(val)

    def get_list(self, key: str, default: Optional[List] = None) -> List:
        val = self.get(key, default or [])
        return val if isinstance(val, list) else [val]

    def section(self, key: str) -> "ConfigView":
        """Return a sub-view for a section."""
        val = self.get(key, {})
        return ConfigView(val if isinstance(val, dict) else {})

    @property
    def raw(self) -> Dict[str, Any]:
        return self._data

    def keys(self) -> List[str]:
        return list(self._data.keys())

    def to_flat_dict(self, prefix: str = "") -> Dict[str, Any]:
        """Flatten to dotted-key dict."""
        result = {}
        for k, v in self._data.items():
            full_key = f"{prefix}.{k}" if prefix else k
            if isinstance(v, dict):
                result.update(ConfigView(v).to_flat_dict(full_key))
            else:
                result[full_key] = v
        return result


# ─── ConfigLoader ────────────────────────────────────────────────────────────

class ConfigLoader:
    """Loads and manages pipeline configuration.

    Features:
    - Load from YAML file
    - Environment variable overrides (LOLBOT_SECTION_KEY=value)
    - Hot-reload on file change
    - Thread-safe access
    - Change notification callbacks

    Usage::

        loader = ConfigLoader("configs/pipeline.yaml")
        loader.load()

        cfg = loader.config
        port = cfg.get_int("server.port", 8080)
        debug = cfg.get_bool("system.debug", False)

        # Hot reload
        loader.start_watching()
    """

    def __init__(self, config_path: str = "configs/pipeline.yaml") -> None:
        self._path = Path(config_path)
        self._parser = SimpleYAMLParser()
        self._config: ConfigView = ConfigView({})
        self._raw_data: Dict[str, Any] = {}
        self._lock = threading.RLock()
        self._file_mtime: float = 0.0
        self._load_count: int = 0
        self._watch_thread: Optional[threading.Thread] = None
        self._watching: bool = False
        self._callbacks: List[Callable[[ConfigView], None]] = []

    @property
    def config(self) -> ConfigView:
        with self._lock:
            return self._config

    def load(self) -> ConfigView:
        """Load configuration from file and apply env overrides.

        Returns:
            The loaded ConfigView.
        """
        data = {}

        # Load from file if it exists
        if self._path.exists():
            size = self._path.stat().st_size
            if size > _MAX_YAML_SIZE:
                logger.error("Config file too large: %d bytes", size)
                return self._config

            text = self._path.read_text(encoding="utf-8")
            data = self._parser.parse(text)
            self._file_mtime = self._path.stat().st_mtime
            logger.info("Loaded config from %s (%d keys)", self._path, len(data))
        else:
            logger.warning("Config file not found: %s (using defaults)", self._path)
            data = self._default_config()

        # Apply environment variable overrides
        data = self._apply_env_overrides(data)

        with self._lock:
            self._raw_data = data
            self._config = ConfigView(data)
            self._load_count += 1

        # Notify callbacks
        for cb in self._callbacks:
            try:
                cb(self._config)
            except Exception:
                logger.exception("Config change callback failed")

        return self._config

    def _apply_env_overrides(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Apply LOLBOT_* environment variables as overrides.

        Convention: LOLBOT_SECTION_KEY → section.key
        Double underscore → nested: LOLBOT_SERVER__PORT → server.port
        """
        for key, value in os.environ.items():
            if not key.startswith(_ENV_PREFIX):
                continue

            # Convert env key to config path
            config_key = key[len(_ENV_PREFIX):].lower()
            parts = config_key.split("__") if "__" in config_key else config_key.split("_", 1)

            # Navigate/create nested dict
            current = data
            for part in parts[:-1]:
                if part not in current or not isinstance(current[part], dict):
                    current[part] = {}
                current = current[part]

            # Set value with type inference
            current[parts[-1]] = self._parser._parse_scalar(value)
            logger.debug("Env override: %s = %s", key, value)

        return data

    def _default_config(self) -> Dict[str, Any]:
        """Return default configuration."""
        return {
            "system": {
                "debug": False,
                "log_level": "INFO",
            },
            "server": {
                "host": "localhost",
                "port": 8080,
            },
            "canbus": {
                "interval_ms": 100,
                "lcu_url": "https://127.0.0.1:2999",
            },
            "perception": {
                "interval_ms": 100,
            },
            "prediction": {
                "interval_ms": 500,
                "smoothing_alpha": 0.3,
                "model_dir": "data/models",
            },
            "planning": {
                "interval_ms": 1000,
                "macro_cooldown_s": 5.0,
                "advice_cooldown_s": 15.0,
            },
            "voice": {
                "enabled": True,
                "rate_wpm": 180,
                "volume": 0.8,
                "cooldown_s": 5.0,
            },
            "evolution": {
                "enabled": True,
                "auto_evolve": True,
                "data_dir": "data/generations",
            },
            "training": {
                "enabled": True,
                "db_path": "data/training_data.db",
                "sample_interval_s": 30.0,
            },
        }

    # ── Hot Reload ───────────────────────────────────────────────────────

    def start_watching(self) -> None:
        """Start watching the config file for changes."""
        if self._watching:
            return
        self._watching = True
        self._watch_thread = threading.Thread(
            target=self._watch_loop,
            daemon=True,
            name="config-watcher",
        )
        self._watch_thread.start()
        logger.info("Config hot-reload watching: %s", self._path)

    def stop_watching(self) -> None:
        """Stop watching for changes."""
        self._watching = False

    def _watch_loop(self) -> None:
        """Poll file mtime for changes."""
        while self._watching:
            try:
                if self._path.exists():
                    mtime = self._path.stat().st_mtime
                    if mtime > self._file_mtime:
                        logger.info("Config file changed, reloading...")
                        self.load()
            except OSError:
                pass
            time.sleep(_HOT_RELOAD_INTERVAL_S)

    # ── Callbacks ────────────────────────────────────────────────────────

    def on_change(self, callback: Callable[[ConfigView], None]) -> None:
        """Register a callback for config changes."""
        self._callbacks.append(callback)

    # ── Stats ────────────────────────────────────────────────────────────

    def stats(self) -> Dict[str, Any]:
        return {
            "path": str(self._path),
            "exists": self._path.exists(),
            "load_count": self._load_count,
            "watching": self._watching,
            "file_mtime": self._file_mtime,
            "keys": self._config.keys(),
        }
