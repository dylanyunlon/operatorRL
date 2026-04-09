#!/usr/bin/env python3
"""
conf/default_config.py — System Configuration
================================================
lolbot-HyperAI · Configuration Layer

Central configuration registry. All tunable parameters live here,
organized by module. Values can be overridden by:
    1. Environment variables (LOLBOT_ prefix)
    2. Config file (data/config.json)
    3. Generation snapshot (evolution controller)
    4. Runtime API (for testing)

Priority: runtime > generation > config file > env > defaults

This is the "launch parameters" equivalent of Apollo's
dreamview/conf/ configuration files.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional


# ---------------------------------------------------------------------------
# Module-level config sections
# ---------------------------------------------------------------------------
@dataclass
class TransportConfig:
    """CAN bus transport configuration."""
    history_size: int = 1000
    default_rate_limit: float = 100.0       # Messages per second per channel
    recording_enabled: bool = True
    recording_dir: str = "logs/recordings"
    recording_compress: bool = True


@dataclass
class PerceptionConfig:
    """Perception layer configuration."""
    poll_interval_ms: int = 50              # NetworkListener cycle
    lcu_poll_gameflow_ms: int = 500
    lcu_poll_champselect_ms: int = 1000
    live_client_poll_ms: int = 500
    live_client_events_poll_ms: int = 200
    fusion_publish_interval_ms: int = 500   # GameStateParser rate


@dataclass
class PredictionConfig:
    """Prediction layer configuration."""
    feature_extraction_interval_ms: int = 2000
    prediction_interval_ms: int = 2000
    model_type: str = "logistic"            # "logistic" or "ensemble"
    model_dir: str = "data/models"
    trend_short_alpha: float = 0.3
    trend_long_alpha: float = 0.05
    trend_threshold: float = 0.02
    confidence_min: float = 0.1
    confidence_max: float = 0.95


@dataclass
class PlanningConfig:
    """Planning layer configuration."""
    recommendation_interval_ms: int = 3000
    min_confidence_threshold: float = 0.4
    max_recommendations_per_tick: int = 2
    # Default cooldowns (can be overridden by evolution)
    cooldown_objective_call_sec: float = 15.0
    cooldown_danger_alert_sec: float = 8.0
    cooldown_macro_advice_sec: float = 20.0
    cooldown_lane_advice_sec: float = 25.0
    cooldown_general_tip_sec: float = 60.0


@dataclass
class OutputConfig:
    """Output (voice/notification) configuration."""
    tts_backend: str = "auto"               # "auto", "pyttsx3", "system", "none"
    tts_rate_wpm: int = 175
    tts_volume: float = 0.8
    min_announce_interval_sec: float = 5.0
    win_update_interval_sec: float = 60.0
    dedup_window_sec: float = 30.0
    max_queue_size: int = 20
    mute_on_start: bool = False


@dataclass
class EvolutionConfig:
    """Evolution layer configuration."""
    enabled: bool = True
    data_dir: str = "data"
    max_generations: int = 50
    mutation_max_weight_delta: float = 0.3
    mutation_max_proposals: int = 5
    mutation_exploration_prob: float = 0.15
    fitness_commit_threshold: float = 0.01  # Min improvement to commit
    auto_evolve_after_game: bool = True     # Auto-evolve after each game


@dataclass
class IntegrationConfig:
    """Integration layer configuration."""
    riot_api_key: str = ""                  # Override via RIOT_API_KEY env
    riot_region: str = "na1"
    agent_os_mode: str = "ungoverned"       # "ungoverned", "governed", "dry_run"


@dataclass
class SystemConfig:
    """System-wide configuration."""
    log_level: str = "INFO"
    log_dir: str = "logs"
    heartbeat_interval_ms: int = 5000
    shutdown_timeout_sec: float = 10.0
    debug_mode: bool = False


@dataclass
class ReplayConfig:
    """Replay/simulation configuration (Claude13).

    Enables offline testing of the full pipeline without a live LoL
    client. When enabled, CanbusComponent reads from recorded JSONL
    files instead of polling the LCU API.

    Apollo reference: modules/drivers/replay_driver
    """
    enabled: bool = False
    recording_path: str = ""         # Path to JSONL recording file
    speed_factor: float = 1.0        # 1.0 = realtime, 0 = max speed
    loop: bool = False               # Loop at end of recording
    start_game_time: float = 0.0     # Seek to this game time on start
    auto_shutdown_on_finish: bool = True  # Shutdown when replay ends


# ---------------------------------------------------------------------------
# Root configuration
# ---------------------------------------------------------------------------
@dataclass
class LolBotConfig:
    """
    Root configuration for lolbot-HyperAI.

    Aggregates all module configs into a single object.
    """
    transport: TransportConfig = field(default_factory=TransportConfig)
    perception: PerceptionConfig = field(default_factory=PerceptionConfig)
    prediction: PredictionConfig = field(default_factory=PredictionConfig)
    planning: PlanningConfig = field(default_factory=PlanningConfig)
    output: OutputConfig = field(default_factory=OutputConfig)
    evolution: EvolutionConfig = field(default_factory=EvolutionConfig)
    integration: IntegrationConfig = field(default_factory=IntegrationConfig)
    system: SystemConfig = field(default_factory=SystemConfig)
    replay: ReplayConfig = field(default_factory=ReplayConfig)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, default=str)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "LolBotConfig":
        """Build config from a dict (e.g. loaded from JSON)."""
        config = cls()
        for section_name, section_data in data.items():
            if hasattr(config, section_name) and isinstance(section_data, dict):
                section = getattr(config, section_name)
                for key, value in section_data.items():
                    if hasattr(section, key):
                        setattr(section, key, value)
        return config


# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------
def load_config(
    config_path: Optional[Path] = None,
    env_prefix: str = "LOLBOT_",
) -> LolBotConfig:
    """
    Load configuration with override chain:
        defaults → config file → environment variables

    Environment variable format:
        LOLBOT_PREDICTION_MODEL_TYPE=ensemble
        LOLBOT_OUTPUT_TTS_RATE_WPM=200
        LOLBOT_SYSTEM_DEBUG_MODE=true
    """
    config = LolBotConfig()

    # Load from file
    if config_path and config_path.exists():
        try:
            data = json.loads(config_path.read_text())
            config = LolBotConfig.from_dict(data)
        except (json.JSONDecodeError, OSError):
            pass  # Use defaults

    # Override from environment
    _apply_env_overrides(config, env_prefix)

    # Special: Riot API key from environment
    riot_key = os.environ.get("RIOT_API_KEY", "")
    if riot_key:
        config.integration.riot_api_key = riot_key

    return config


def _apply_env_overrides(config: LolBotConfig, prefix: str) -> None:
    """Apply environment variable overrides to config."""
    for env_key, env_value in os.environ.items():
        if not env_key.startswith(prefix):
            continue

        # Parse: LOLBOT_SECTION_KEY → section.key
        parts = env_key[len(prefix):].lower().split("_", 1)
        if len(parts) != 2:
            continue

        section_name, key = parts

        if not hasattr(config, section_name):
            continue

        section = getattr(config, section_name)
        if not hasattr(section, key):
            continue

        # Type-aware conversion
        current = getattr(section, key)
        try:
            if isinstance(current, bool):
                setattr(section, key, env_value.lower() in ("true", "1", "yes"))
            elif isinstance(current, int):
                setattr(section, key, int(env_value))
            elif isinstance(current, float):
                setattr(section, key, float(env_value))
            else:
                setattr(section, key, env_value)
        except (ValueError, TypeError):
            pass  # Skip invalid env values


def save_config(config: LolBotConfig, path: Path) -> None:
    """Save configuration to a JSON file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(config.to_json())


# ---------------------------------------------------------------------------
# Default instance
# ---------------------------------------------------------------------------
_default_config: Optional[LolBotConfig] = None


def get_config() -> LolBotConfig:
    """Get or create the default config singleton."""
    global _default_config
    if _default_config is None:
        # Try to load from standard location
        _default_config = load_config(
            Path("data/config.json"),
        )
    return _default_config


def set_config(config: LolBotConfig) -> None:
    """Override the default config (for testing)."""
    global _default_config
    _default_config = config


# ─── Apollo-style FLAGS for timing and deadlines (Claude23) ──────────────────
#
# Apollo uses gflags (FLAGS_chassis_freq, FLAGS_control_period, etc.)
# for all timing constants. We centralize them here for consistency.
#
# These match Apollo's pattern where every timing constant is configurable
# via flags, not hardcoded in component code.

class TimingFlags:
    """Centralized timing constants (Apollo gflags equivalent).

    Apollo reference:
        modules/canbus/common/canbus_gflags.cc
        modules/common/adapters/adapter_gflags.cc

    All intervals in milliseconds, periods in seconds.
    """
    # Component Proc() intervals (ms)
    CANBUS_INTERVAL_MS: float = 100.0       # 10Hz
    PERCEPTION_INTERVAL_MS: float = 100.0   # 10Hz
    PREDICTION_INTERVAL_MS: float = 500.0   # 2Hz
    PLANNING_INTERVAL_MS: float = 500.0     # 2Hz
    CONTROL_INTERVAL_MS: float = 200.0      # 5Hz
    MONITOR_INTERVAL_MS: float = 2000.0     # 0.5Hz

    # Data freshness thresholds (seconds)
    CANBUS_STALE_THRESHOLD_S: float = 5.0
    PERCEPTION_STALE_THRESHOLD_S: float = 3.0
    PREDICTION_STALE_THRESHOLD_S: float = 10.0
    PLANNING_STALE_THRESHOLD_S: float = 15.0

    # Communication fault thresholds
    COMM_FAULT_THRESHOLD_S: float = 10.0
    MAX_CONSECUTIVE_FAILURES: int = 10
    CIRCUIT_BREAKER_COOLDOWN_S: float = 2.0

    # Apollo-style command check
    MAX_CONTROL_MISS_NUM: int = 10
    CONTROL_PERIOD_S: float = 0.2   # 5Hz
    MIN_CMD_INTERVAL_MS: float = 5.0

    # Heartbeat
    HEARTBEAT_INTERVAL_S: float = 5.0

    # Safe mode
    SAFE_MODE_ACTIVATION_THRESHOLD: int = 10
    SAFE_MODE_RECOVERY_THRESHOLD: int = 5

    # Supervisor
    SUPERVISOR_INTERVAL_S: float = 1.0
    HEALTH_CHECK_INTERVAL_S: float = 5.0

    @classmethod
    def to_dict(cls) -> Dict[str, Any]:
        """Export all flags as a dict for serialization."""
        return {
            k: v for k, v in cls.__dict__.items()
            if not k.startswith("_") and k.isupper()
        }
