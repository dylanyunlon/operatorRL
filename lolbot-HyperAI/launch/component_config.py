"""
ComponentConfig Loader — YAML/JSON to ComponentConfig mapping.
===============================================================

Loads component configuration from YAML or JSON files and creates
ComponentConfig instances for each component. Supports environment
variable interpolation and config inheritance.

Architecture position:
    launch/component_config.py   ← YOU ARE HERE
    ├─ Reads: configs/pipeline.yaml or JSON config files
    ├─ Creates: ComponentConfig instances
    └─ Used by: launch/dag_launcher.py

Apollo reference:
    cyber/conf/cyber_conf.cc — configuration loading
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from cyber.component.timer_component import ComponentConfig

logger = logging.getLogger(__name__)

_ENV_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::([^}]*))?\}")

# Default configurations for known components
_COMPONENT_DEFAULTS: Dict[str, Dict[str, Any]] = {
    "canbus": {
        "interval_ms": 100.0,
        "warn_threshold_ms": 200.0,
        "max_consecutive_failures": 10,
    },
    "perception": {
        "interval_ms": 100.0,
        "warn_threshold_ms": 150.0,
        "max_consecutive_failures": 5,
    },
    "prediction": {
        "interval_ms": 200.0,
        "warn_threshold_ms": 300.0,
        "max_consecutive_failures": 5,
    },
    "planning": {
        "interval_ms": 200.0,
        "warn_threshold_ms": 300.0,
        "max_consecutive_failures": 5,
    },
    "monitor": {
        "interval_ms": 1000.0,
        "warn_threshold_ms": 500.0,
        "max_consecutive_failures": 3,
    },
    "storytelling": {
        "interval_ms": 500.0,
        "warn_threshold_ms": 400.0,
        "max_consecutive_failures": 5,
    },
}


def interpolate_env_vars(value: str) -> str:
    """Replace ${VAR} or ${VAR:default} with environment variable values."""
    def _replace(match):
        var_name = match.group(1)
        default = match.group(2)
        return os.environ.get(var_name, default if default is not None else "")
    return _ENV_VAR_RE.sub(_replace, value)


def _deep_merge(base: Dict, override: Dict) -> Dict:
    """Deep merge override into base dict."""
    result = dict(base)
    for key, value in override.items():
        if (key in result and isinstance(result[key], dict)
                and isinstance(value, dict)):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _interpolate_recursive(obj: Any) -> Any:
    """Recursively interpolate env vars in strings."""
    if isinstance(obj, str):
        return interpolate_env_vars(obj)
    elif isinstance(obj, dict):
        return {k: _interpolate_recursive(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_interpolate_recursive(v) for v in obj]
    return obj


def load_component_configs(
    config_path: str | Path,
) -> Dict[str, ComponentConfig]:
    """Load component configurations from a JSON or YAML file.

    The file should have a top-level 'components' key mapping
    component names to their config overrides.

    Args:
        config_path: Path to JSON/YAML config file.

    Returns:
        Dict mapping component name → ComponentConfig.
    """
    path = Path(config_path)
    if not path.exists():
        logger.warning("Config file not found: %s, using defaults", path)
        return _defaults_only()

    raw_text = path.read_text(encoding="utf-8")

    # Parse based on extension
    if path.suffix in (".yaml", ".yml"):
        data = _parse_yaml_subset(raw_text)
    else:
        data = json.loads(raw_text)

    data = _interpolate_recursive(data)

    components_data = data.get("components", data)
    results: Dict[str, ComponentConfig] = {}

    for name, overrides in components_data.items():
        if not isinstance(overrides, dict):
            continue

        # Merge with defaults
        defaults = _COMPONENT_DEFAULTS.get(name, {})
        merged = _deep_merge(defaults, overrides)

        config = ComponentConfig(
            name=name,
            interval_ms=float(merged.get("interval_ms", 100.0)),
            warn_threshold_ms=float(merged.get("warn_threshold_ms", 200.0)),
            max_consecutive_failures=int(
                merged.get("max_consecutive_failures", 5)
            ),
            cooldown_s=float(merged.get("cooldown_s", 2.0)),
            enable_latency_stats=bool(
                merged.get("enable_latency_stats", True)
            ),
        )
        results[name] = config

    # Add defaults for components not in config
    for name, defaults in _COMPONENT_DEFAULTS.items():
        if name not in results:
            results[name] = ComponentConfig(
                name=name,
                interval_ms=defaults.get("interval_ms", 100.0),
                warn_threshold_ms=defaults.get("warn_threshold_ms", 200.0),
                max_consecutive_failures=defaults.get(
                    "max_consecutive_failures", 5
                ),
            )

    logger.info(
        "Loaded %d component configs from %s", len(results), path
    )
    return results


def _defaults_only() -> Dict[str, ComponentConfig]:
    """Generate configs from built-in defaults."""
    return {
        name: ComponentConfig(
            name=name,
            interval_ms=d.get("interval_ms", 100.0),
            warn_threshold_ms=d.get("warn_threshold_ms", 200.0),
            max_consecutive_failures=d.get("max_consecutive_failures", 5),
        )
        for name, d in _COMPONENT_DEFAULTS.items()
    }


def _parse_yaml_subset(text: str) -> Dict[str, Any]:
    """Minimal YAML parser for simple key-value configs.

    Handles: top-level keys, nested dicts (indented), string/int/float
    values. Does NOT handle: lists, multi-line strings, anchors, etc.
    For production, replace with PyYAML.
    """
    result: Dict[str, Any] = {}
    stack: List[Tuple[Dict, int]] = [(result, -1)]

    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        indent = len(line) - len(line.lstrip())

        # Pop stack to correct level
        while len(stack) > 1 and indent <= stack[-1][1]:
            stack.pop()

        if ":" not in stripped:
            continue

        key, _, value = stripped.partition(":")
        key = key.strip()
        value = value.strip()

        current_dict = stack[-1][0]

        if not value or value == "":
            new_dict: Dict[str, Any] = {}
            current_dict[key] = new_dict
            stack.append((new_dict, indent))
        else:
            # Strip quotes
            if value.startswith(("'", '"')) and value.endswith(("'", '"')):
                value = value[1:-1]

            # Type coercion
            if value.lower() == "true":
                current_dict[key] = True
            elif value.lower() == "false":
                current_dict[key] = False
            else:
                try:
                    current_dict[key] = int(value)
                except ValueError:
                    try:
                        current_dict[key] = float(value)
                    except ValueError:
                        current_dict[key] = value

    return result


def save_component_configs(
    configs: Dict[str, ComponentConfig],
    output_path: str | Path,
) -> None:
    """Save component configs to JSON."""
    data = {
        "components": {
            name: {
                "interval_ms": cfg.interval_ms,
                "warn_threshold_ms": cfg.warn_threshold_ms,
                "max_consecutive_failures": cfg.max_consecutive_failures,
                "cooldown_s": cfg.cooldown_s,
                "enable_latency_stats": cfg.enable_latency_stats,
            }
            for name, cfg in configs.items()
        }
    }
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    logger.info("Saved %d component configs to %s", len(configs), path)
