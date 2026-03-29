"""
Config Loader — YAML/JSON configuration loading with env var overrides.

Loads configuration from files and dicts, supports environment variable
overrides, nested key access, merge, and validation.

Location: agentos/cli/config_loader.py

Reference (拿来主义):
  - Akagi/settings/settings.py: settings loading pattern
  - DI-star/distar/ctools/utils/config_helper.py: deep_merge_dicts, read_config
  - PARL config patterns: environment variable override
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)


class ConfigLoader:
    """Configuration loader with env override and merge support.

    Mirrors DI-star's deep_merge_dicts and Akagi's settings pattern.
    """

    def load_dict(self, data: dict[str, Any]) -> dict[str, Any]:
        """Load config from a dict.

        Args:
            data: Configuration dictionary.

        Returns:
            Copy of the configuration dict.
        """
        return dict(data)

    def load_json_string(self, raw: str) -> dict[str, Any]:
        """Load config from a JSON string.

        Args:
            raw: JSON string.

        Returns:
            Parsed configuration dict.
        """
        return json.loads(raw)

    def load_file(self, path: str) -> dict[str, Any]:
        """Load config from a JSON or YAML file.

        Args:
            path: File path.

        Returns:
            Parsed configuration dict.
        """
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()

        if path.endswith(".json"):
            return json.loads(content)

        # YAML fallback — try import, else treat as JSON
        try:
            import yaml
            return yaml.safe_load(content)
        except ImportError:
            return json.loads(content)

    def apply_env_overrides(
        self, cfg: dict[str, Any], prefix: str = "AGENTOS_"
    ) -> dict[str, Any]:
        """Override config values from environment variables.

        Env var AGENTOS_GAME overrides cfg["game"], etc.

        Args:
            cfg: Configuration dict to override.
            prefix: Environment variable prefix.

        Returns:
            Updated configuration dict.
        """
        result = dict(cfg)
        for key in list(result.keys()):
            env_key = f"{prefix}{key.upper()}"
            env_val = os.environ.get(env_key)
            if env_val is not None:
                # Try to preserve type
                if isinstance(result[key], bool):
                    result[key] = env_val.lower() in ("true", "1", "yes")
                elif isinstance(result[key], int):
                    try:
                        result[key] = int(env_val)
                    except ValueError:
                        result[key] = env_val
                elif isinstance(result[key], float):
                    try:
                        result[key] = float(env_val)
                    except ValueError:
                        result[key] = env_val
                else:
                    result[key] = env_val
        return result

    def get_nested(
        self, cfg: dict[str, Any], path: str, default: Any = None
    ) -> Any:
        """Get a nested config value by dot-separated path.

        Args:
            cfg: Configuration dict.
            path: Dot-separated key path (e.g., "a.b.c").
            default: Default value if path not found.

        Returns:
            Value at path, or default.
        """
        current = cfg
        for key in path.split("."):
            if isinstance(current, dict) and key in current:
                current = current[key]
            else:
                return default
        return current

    def merge(
        self, base: dict[str, Any], override: dict[str, Any]
    ) -> dict[str, Any]:
        """Deep merge two config dicts (override wins).

        Mirrors DI-star's deep_merge_dicts pattern.

        Args:
            base: Base configuration.
            override: Override configuration.

        Returns:
            Merged configuration dict.
        """
        result = dict(base)
        for key, value in override.items():
            if (
                key in result
                and isinstance(result[key], dict)
                and isinstance(value, dict)
            ):
                result[key] = self.merge(result[key], value)
            else:
                result[key] = value
        return result

    def validate(
        self, cfg: dict[str, Any], required: list[str]
    ) -> bool:
        """Validate that required keys are present.

        Args:
            cfg: Configuration dict.
            required: List of required key names.

        Returns:
            True if all required keys present.
        """
        for key in required:
            if key not in cfg:
                return False
        return True
