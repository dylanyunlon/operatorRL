"""
Config Loader - Hierarchical configuration management.

Loads settings from YAML files, environment variables, and CLI
arguments with precedence: CLI > ENV > file > defaults.
"""

from __future__ import annotations

import logging
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Optional, TypeVar

import yaml

logger = logging.getLogger(__name__)

T = TypeVar("T")

_DEFAULT_CONFIG_PATHS = [
    "config/settings.yaml",
    "settings.yaml",
    os.path.expanduser("~/.lol-fiddler-agent/settings.yaml"),
]

_ENV_PREFIX = "LOL_AGENT_"


@dataclass
class AppConfig:
    """Root application configuration."""
    # Fiddler
    fiddler_host: str = "localhost"
    fiddler_port: int = 8868
    fiddler_api_key: str = ""
    fiddler_timeout: float = 30.0

    # Agent
    agent_id: str = "lol-strategy-agent"
    poll_interval: float = 2.0
    advice_cooldown: float = 10.0

    # Features
    enable_win_prediction: bool = True
    enable_objective_tracking: bool = True
    enable_teamfight_analysis: bool = True
    enable_map_awareness: bool = True
    enable_wave_management: bool = True
    enable_power_spikes: bool = True
    enable_team_comp: bool = True

    # ML
    model_path: str = ""
    model_type: str = "builtin"

    # Replay
    replay_dir: str = "./replays"
    enable_recording: bool = True

    # Training
    training_data_dir: str = "./training_data"
    replay_buffer_capacity: int = 10000

    # WebSocket
    ws_host: str = "127.0.0.1"
    ws_port: int = 9876
    enable_ws_bridge: bool = False

    # Riot API
    riot_api_key: str = ""
    riot_region: str = "na1"

    # Logging
    log_level: str = "INFO"
    log_format: str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

    # AgentOS
    agentos_policies: list[str] = field(default_factory=lambda: ["read_only", "no_pii"])
    max_advice_per_minute: int = 30


def load_config(
    config_path: Optional[str] = None,
    cli_overrides: Optional[dict[str, Any]] = None,
) -> AppConfig:
    """Load configuration with hierarchical precedence."""
    config = AppConfig()

    # 1. Load from YAML file
    yaml_data = _load_yaml(config_path)
    if yaml_data:
        config = _apply_yaml(config, yaml_data)

    # 2. Override from environment variables
    config = _apply_env(config)

    # 3. Override from CLI arguments
    if cli_overrides:
        config = _apply_dict(config, cli_overrides)

    return config


def _load_yaml(path: Optional[str] = None) -> dict[str, Any]:
    """Load YAML config file."""
    paths_to_try = [path] if path else _DEFAULT_CONFIG_PATHS
    for p in paths_to_try:
        if p and os.path.exists(p):
            try:
                with open(p) as f:
                    data = yaml.safe_load(f) or {}
                logger.info("Loaded config from %s", p)
                return data
            except Exception as e:
                logger.warning("Failed to load %s: %s", p, e)
    return {}


def _apply_yaml(config: AppConfig, data: dict[str, Any]) -> AppConfig:
    """Apply YAML data to config, handling nested sections."""
    flat: dict[str, Any] = {}

    # Flatten nested YAML into flat keys
    for section, values in data.items():
        if isinstance(values, dict):
            for key, val in values.items():
                flat_key = f"{section}_{key}" if section != "agent" else key
                flat[flat_key] = val
        else:
            flat[section] = values

    return _apply_dict(config, flat)


def _apply_env(config: AppConfig) -> AppConfig:
    """Apply environment variables to config."""
    overrides: dict[str, Any] = {}
    for f in fields(config):
        env_key = f"{_ENV_PREFIX}{f.name.upper()}"
        env_val = os.environ.get(env_key)
        if env_val is not None:
            # Type conversion
            if f.type == "bool":
                overrides[f.name] = env_val.lower() in ("true", "1", "yes")
            elif f.type == "int":
                overrides[f.name] = int(env_val)
            elif f.type == "float":
                overrides[f.name] = float(env_val)
            else:
                overrides[f.name] = env_val
    return _apply_dict(config, overrides)


def _apply_dict(config: AppConfig, data: dict[str, Any]) -> AppConfig:
    """Apply a flat dictionary to config fields."""
    config_fields = {f.name for f in fields(config)}
    for key, value in data.items():
        if key in config_fields:
            setattr(config, key, value)
    return config


def save_config(config: AppConfig, path: str) -> None:
    """Save config to YAML file."""
    data = asdict(config)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        yaml.dump(data, f, default_flow_style=False)
    logger.info("Saved config to %s", path)
