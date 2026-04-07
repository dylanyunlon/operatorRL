"""
Prediction layer configuration.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict

@dataclass
class WinModelConfig:
    model_version: str = "heuristic-v1"
    smoothing_alpha: float = 0.3
    confidence_ramp_time_s: float = 600.0
    min_game_time_s: float = 120.0

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "WinModelConfig":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

@dataclass
class TeamfightConfig:
    tick_divisor: int = 2
    engage_threshold: float = 0.60
    disengage_threshold: float = 0.40

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "TeamfightConfig":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

@dataclass
class PredictionLayerConfig:
    tick_interval_ms: float = 500.0
    warn_threshold_ms: float = 400.0
    history_max: int = 200
    win_model: WinModelConfig = field(default_factory=WinModelConfig)
    teamfight: TeamfightConfig = field(default_factory=TeamfightConfig)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "PredictionLayerConfig":
        return cls(
            tick_interval_ms=d.get("tick_interval_ms", 500.0),
            warn_threshold_ms=d.get("warn_threshold_ms", 400.0),
            history_max=d.get("history_max", 200),
            win_model=WinModelConfig.from_dict(d.get("win_model", {})),
            teamfight=TeamfightConfig.from_dict(d.get("teamfight", {})),
        )


# ═══════════════════════════════════════════════════════════════════════════
# Claude20: Production-grade config validation, schema, runtime reload
# ═══════════════════════════════════════════════════════════════════════════
#
# 查看 Apollo modules/prediction/evaluator/evaluator_manager.h 上现有
# EvaluatorManager 的配置加载方式, 理解其模式, 特别是 proto config
# 如何被解析并校验。从 Apollo PredictionConf 这个好例子开始。然后,
# 遵循该模式实现完整的配置校验层, 让配置可以在加载时自动检测越界值,
# 并能在运行时热更新而不中断 Proc() 循环。
# ═══════════════════════════════════════════════════════════════════════════

import copy
import json
import logging
import os
import time
from pathlib import Path
from typing import Callable, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


class ConfigValidationError(ValueError):
    """Raised when a configuration value is out of valid range."""
    def __init__(self, field: str, value: Any, reason: str) -> None:
        self.field = field
        self.value = value
        self.reason = reason
        super().__init__(f"Config validation failed: {field}={value!r} — {reason}")


class ConfigValidator:
    """Validates prediction layer configuration values.

    Apollo pattern: EvaluatorManager validates config proto fields before
    passing them to evaluators. We validate at load time AND at runtime
    hot-reload to prevent garbage configs from crashing Proc().

    Usage::
        validator = ConfigValidator()
        errors = validator.validate(config)
        if errors:
            for err in errors:
                logger.error("Config error: %s", err)
            raise ConfigValidationError(errors[0].field, errors[0].value, errors[0].reason)
    """

    # Valid ranges for all numeric fields
    _RANGES: Dict[str, Tuple[float, float]] = {
        "tick_interval_ms": (50.0, 5000.0),
        "warn_threshold_ms": (10.0, 5000.0),
        "history_max": (10, 10000),
        "smoothing_alpha": (0.01, 1.0),
        "confidence_ramp_time_s": (0.0, 3600.0),
        "min_game_time_s": (0.0, 600.0),
        "tick_divisor": (1, 100),
        "engage_threshold": (0.0, 1.0),
        "disengage_threshold": (0.0, 1.0),
    }

    def validate(self, config: PredictionLayerConfig) -> List[ConfigValidationError]:
        """Validate all fields. Returns list of errors (empty = OK)."""
        errors: List[ConfigValidationError] = []

        # Top-level
        self._check_range(errors, "tick_interval_ms", config.tick_interval_ms)
        self._check_range(errors, "warn_threshold_ms", config.warn_threshold_ms)
        self._check_range(errors, "history_max", config.history_max)

        # Warn must be <= tick interval (no point warning if we always exceed)
        if config.warn_threshold_ms > config.tick_interval_ms:
            errors.append(ConfigValidationError(
                "warn_threshold_ms", config.warn_threshold_ms,
                f"Must be <= tick_interval_ms ({config.tick_interval_ms})",
            ))

        # Win model
        wm = config.win_model
        self._check_range(errors, "smoothing_alpha", wm.smoothing_alpha)
        self._check_range(errors, "confidence_ramp_time_s", wm.confidence_ramp_time_s)
        self._check_range(errors, "min_game_time_s", wm.min_game_time_s)

        # Teamfight
        tf = config.teamfight
        self._check_range(errors, "tick_divisor", tf.tick_divisor)
        self._check_range(errors, "engage_threshold", tf.engage_threshold)
        self._check_range(errors, "disengage_threshold", tf.disengage_threshold)

        # engage > disengage (otherwise the logic is inverted)
        if tf.engage_threshold <= tf.disengage_threshold:
            errors.append(ConfigValidationError(
                "engage_threshold", tf.engage_threshold,
                f"Must be > disengage_threshold ({tf.disengage_threshold})",
            ))

        return errors

    def _check_range(
        self,
        errors: List[ConfigValidationError],
        field: str,
        value: Any,
    ) -> None:
        """Check if value is within valid range."""
        bounds = self._RANGES.get(field)
        if bounds is None:
            return
        lo, hi = bounds
        if not (lo <= value <= hi):
            errors.append(ConfigValidationError(
                field, value, f"Must be in [{lo}, {hi}]",
            ))


@dataclass
class DeathTimerConfig:
    """Configuration for the DeathTimerAnalyzer sub-module.

    Claude20: Extracted from hardcoded constants in death_timer_analyzer.py
    to make them configurable via the prediction config hierarchy.
    """
    early_game_penalty_s: float = 5.0
    mid_game_penalty_s: float = 15.0
    late_game_penalty_s: float = 30.0
    max_death_timer_s: float = 75.0
    level_scaling_factor: float = 2.5

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "DeathTimerConfig":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

    def validate(self) -> List[str]:
        """Return list of validation error messages."""
        errors = []
        if self.early_game_penalty_s < 0:
            errors.append("early_game_penalty_s must be >= 0")
        if self.max_death_timer_s < 10:
            errors.append("max_death_timer_s must be >= 10")
        if self.level_scaling_factor < 0:
            errors.append("level_scaling_factor must be >= 0")
        return errors


@dataclass
class ConfidenceCalibratorConfig:
    """Configuration for ConfidenceCalibrator sub-module.

    Claude20: Makes calibration parameters tunable via config.
    """
    signal_weights: Dict[str, float] = field(default_factory=lambda: {
        "data_quality": 0.3,
        "sample_size": 0.2,
        "model_agreement": 0.3,
        "temporal_stability": 0.2,
    })
    min_confidence_floor: float = 0.05
    max_confidence_cap: float = 0.99
    ramp_games: int = 5  # Number of games before full confidence

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ConfidenceCalibratorConfig":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

    def validate(self) -> List[str]:
        errors = []
        if self.min_confidence_floor < 0 or self.min_confidence_floor >= 1:
            errors.append("min_confidence_floor must be in [0, 1)")
        if self.max_confidence_cap <= self.min_confidence_floor:
            errors.append("max_confidence_cap must be > min_confidence_floor")
        weights_sum = sum(self.signal_weights.values())
        if abs(weights_sum - 1.0) > 0.01:
            errors.append(f"signal_weights must sum to 1.0 (got {weights_sum:.3f})")
        return errors


@dataclass
class MomentumConfig:
    """Configuration for MomentumTracker sub-module.

    Claude20: Extracted from hardcoded constants to make
    momentum sensitivity tunable by the evolution loop.
    """
    short_window_s: float = 30.0
    medium_window_s: float = 120.0
    surge_threshold: float = 0.7
    collapse_threshold: float = -0.7
    decay_rate: float = 0.95
    kill_weight: float = 0.3
    gold_weight: float = 0.4
    objective_weight: float = 0.3

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "MomentumConfig":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

    def validate(self) -> List[str]:
        errors = []
        if self.short_window_s <= 0:
            errors.append("short_window_s must be > 0")
        if self.medium_window_s <= self.short_window_s:
            errors.append("medium_window_s must be > short_window_s")
        ws = self.kill_weight + self.gold_weight + self.objective_weight
        if abs(ws - 1.0) > 0.01:
            errors.append(f"Momentum weights must sum to 1.0 (got {ws:.3f})")
        return errors


@dataclass
class CompAnalyzerConfig:
    """Configuration for team composition analysis.

    Claude20: Makes archetype thresholds tunable.
    """
    primary_threshold: float = 0.20
    secondary_threshold: float = 0.15
    phase_weight_scale: float = 0.5
    enabled: bool = True

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "CompAnalyzerConfig":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class ObjectiveWindowConfig:
    """Configuration for ObjectiveWindowAdvisor.

    Claude20: Makes spawn window timing and priority weights configurable.
    """
    advice_cooldown_s: float = 15.0
    contest_window_s: float = 15.0
    prepare_window_s: float = 45.0
    max_lookahead_s: float = 120.0
    baron_priority: float = 0.95
    dragon_priority: float = 0.75
    herald_priority: float = 0.55

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ObjectiveWindowConfig":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

    def validate(self) -> List[str]:
        errors = []
        for name in ("baron_priority", "dragon_priority", "herald_priority"):
            val = getattr(self, name)
            if not (0.0 <= val <= 1.0):
                errors.append(f"{name} must be in [0, 1] (got {val})")
        if self.contest_window_s >= self.prepare_window_s:
            errors.append("contest_window_s must be < prepare_window_s")
        return errors


# ─── Extended PredictionLayerConfig with Claude20 sub-configs ────────────

@dataclass
class PredictionLayerConfigV2(PredictionLayerConfig):
    """Extended prediction config with sub-module configurations.

    Claude20: Adds configuration for all prediction sub-modules that
    were previously hardcoded. This class extends the original
    PredictionLayerConfig to maintain backward compatibility.

    All existing fields from PredictionLayerConfig are preserved.
    """
    death_timer: DeathTimerConfig = field(default_factory=DeathTimerConfig)
    confidence: ConfidenceCalibratorConfig = field(
        default_factory=ConfidenceCalibratorConfig)
    momentum: MomentumConfig = field(default_factory=MomentumConfig)
    comp_analyzer: CompAnalyzerConfig = field(default_factory=CompAnalyzerConfig)
    objective_window: ObjectiveWindowConfig = field(
        default_factory=ObjectiveWindowConfig)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "PredictionLayerConfigV2":
        """Create from dict, handling both old and new format."""
        base = PredictionLayerConfig.from_dict(d)
        return cls(
            tick_interval_ms=base.tick_interval_ms,
            warn_threshold_ms=base.warn_threshold_ms,
            history_max=base.history_max,
            win_model=base.win_model,
            teamfight=base.teamfight,
            death_timer=DeathTimerConfig.from_dict(d.get("death_timer", {})),
            confidence=ConfidenceCalibratorConfig.from_dict(
                d.get("confidence", {})),
            momentum=MomentumConfig.from_dict(d.get("momentum", {})),
            comp_analyzer=CompAnalyzerConfig.from_dict(
                d.get("comp_analyzer", {})),
            objective_window=ObjectiveWindowConfig.from_dict(
                d.get("objective_window", {})),
        )

    def validate_all(self) -> List[str]:
        """Validate all sub-configs. Returns list of error messages."""
        errors: List[str] = []
        validator = ConfigValidator()
        for err in validator.validate(self):
            errors.append(str(err))
        errors.extend(self.death_timer.validate())
        errors.extend(self.confidence.validate())
        errors.extend(self.momentum.validate())
        errors.extend(self.objective_window.validate())
        return errors

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict for persistence and dashboard display."""
        return {
            "tick_interval_ms": self.tick_interval_ms,
            "warn_threshold_ms": self.warn_threshold_ms,
            "history_max": self.history_max,
            "win_model": {
                k: getattr(self.win_model, k)
                for k in WinModelConfig.__dataclass_fields__
            },
            "teamfight": {
                k: getattr(self.teamfight, k)
                for k in TeamfightConfig.__dataclass_fields__
            },
            "death_timer": {
                k: getattr(self.death_timer, k)
                for k in DeathTimerConfig.__dataclass_fields__
            },
            "confidence": {
                k: getattr(self.confidence, k)
                for k in ConfidenceCalibratorConfig.__dataclass_fields__
            },
            "momentum": {
                k: getattr(self.momentum, k)
                for k in MomentumConfig.__dataclass_fields__
            },
            "comp_analyzer": {
                k: getattr(self.comp_analyzer, k)
                for k in CompAnalyzerConfig.__dataclass_fields__
            },
            "objective_window": {
                k: getattr(self.objective_window, k)
                for k in ObjectiveWindowConfig.__dataclass_fields__
            },
        }

    def diff(self, other: "PredictionLayerConfigV2") -> Dict[str, Tuple[Any, Any]]:
        """Compare two configs and return changed fields.

        Returns dict of field_name → (old_value, new_value).
        Used by evolution loop to track what changed between generations.
        """
        changes: Dict[str, Tuple[Any, Any]] = {}
        self_d = self.to_dict()
        other_d = other.to_dict()
        self._diff_recursive(self_d, other_d, "", changes)
        return changes

    @staticmethod
    def _diff_recursive(
        a: Any, b: Any, prefix: str, changes: Dict[str, Tuple[Any, Any]],
    ) -> None:
        if isinstance(a, dict) and isinstance(b, dict):
            for key in set(list(a.keys()) + list(b.keys())):
                path = f"{prefix}.{key}" if prefix else key
                PredictionLayerConfigV2._diff_recursive(
                    a.get(key), b.get(key), path, changes,
                )
        elif a != b:
            changes[prefix] = (a, b)


class PredictionConfigLoader:
    """Loads and watches prediction config from file or dict.

    Claude20: Supports hot-reload via file mtime polling. When config
    changes, validates before applying. If validation fails, logs error
    and keeps old config (safe fallback).

    Apollo reference: cyber/conf/ — config reload mechanism.

    Usage::
        loader = PredictionConfigLoader("conf/prediction.json")
        config = loader.load()
        # Later, in health check:
        if loader.has_changed():
            new_config = loader.reload()
    """

    def __init__(self, config_path: Optional[str] = None) -> None:
        self._path = Path(config_path) if config_path else None
        self._last_mtime: float = 0.0
        self._current: Optional[PredictionLayerConfigV2] = None
        self._load_count: int = 0
        self._error_count: int = 0
        self._reload_callbacks: List[Callable[[PredictionLayerConfigV2], None]] = []

    def load(self) -> PredictionLayerConfigV2:
        """Load config from file (or return default)."""
        if self._path and self._path.exists():
            try:
                with open(self._path, "r") as f:
                    data = json.load(f)
                config = PredictionLayerConfigV2.from_dict(data)
                errors = config.validate_all()
                if errors:
                    for err in errors:
                        logger.error("Prediction config error: %s", err)
                    raise ConfigValidationError("config", str(self._path), "; ".join(errors))
                self._current = config
                self._last_mtime = self._path.stat().st_mtime
                self._load_count += 1
                logger.info("Loaded prediction config from %s", self._path)
                return config
            except (json.JSONDecodeError, OSError, ConfigValidationError) as exc:
                self._error_count += 1
                logger.error("Failed to load prediction config: %s", exc)

        # Default config
        if self._current is None:
            self._current = PredictionLayerConfigV2()
            self._load_count += 1
        return self._current

    def has_changed(self) -> bool:
        """Check if the config file has been modified since last load."""
        if self._path is None or not self._path.exists():
            return False
        try:
            current_mtime = self._path.stat().st_mtime
            return current_mtime > self._last_mtime
        except OSError:
            return False

    def reload(self) -> Optional[PredictionLayerConfigV2]:
        """Reload config if changed. Returns new config or None."""
        if not self.has_changed():
            return None
        old = self._current
        new = self.load()
        if old and new:
            changes = new.diff(old)
            if changes:
                logger.info("Prediction config changed: %s", list(changes.keys()))
                for cb in self._reload_callbacks:
                    try:
                        cb(new)
                    except Exception as exc:
                        logger.error("Config reload callback error: %s", exc)
        return new

    def on_reload(self, callback: Callable[[PredictionLayerConfigV2], None]) -> None:
        """Register a callback for config reload events."""
        self._reload_callbacks.append(callback)

    @property
    def current(self) -> PredictionLayerConfigV2:
        if self._current is None:
            return self.load()
        return self._current

    def stats(self) -> Dict[str, Any]:
        return {
            "path": str(self._path) if self._path else None,
            "load_count": self._load_count,
            "error_count": self._error_count,
            "has_changed": self.has_changed(),
        }
