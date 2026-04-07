"""
Perception layer configuration.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict

@dataclass
class KillFeedConfig:
    multi_kill_window_s: float = 10.0
    max_pattern_history: int = 200
    spree_threshold: int = 3

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "KillFeedConfig":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

@dataclass
class MinimapConfig:
    analysis_interval_ticks: int = 5
    history_window: int = 30
    zone_count: int = 19

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "MinimapConfig":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

@dataclass
class StateAssemblerConfig:
    publish_interval_ms: float = 500.0
    momentum_window_s: float = 60.0
    phase_transitions_enabled: bool = True

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "StateAssemblerConfig":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

@dataclass
class PerceptionLayerConfig:
    tick_interval_ms: float = 100.0
    warn_threshold_ms: float = 150.0
    kill_feed: KillFeedConfig = field(default_factory=KillFeedConfig)
    minimap: MinimapConfig = field(default_factory=MinimapConfig)
    state_assembler: StateAssemblerConfig = field(default_factory=StateAssemblerConfig)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "PerceptionLayerConfig":
        return cls(
            tick_interval_ms=d.get("tick_interval_ms", 100.0),
            warn_threshold_ms=d.get("warn_threshold_ms", 150.0),
            kill_feed=KillFeedConfig.from_dict(d.get("kill_feed", {})),
            minimap=MinimapConfig.from_dict(d.get("minimap", {})),
            state_assembler=StateAssemblerConfig.from_dict(d.get("state_assembler", {})),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tick_interval_ms": self.tick_interval_ms,
            "warn_threshold_ms": self.warn_threshold_ms,
            "kill_feed": {k: getattr(self.kill_feed, k) for k in KillFeedConfig.__dataclass_fields__},
            "minimap": {k: getattr(self.minimap, k) for k in MinimapConfig.__dataclass_fields__},
            "state_assembler": {k: getattr(self.state_assembler, k) for k in StateAssemblerConfig.__dataclass_fields__},
        }


# ═══════════════════════════════════════════════════════════════════════════
# Claude20: Extended perception config with fusion, phase, and gold configs
# ═══════════════════════════════════════════════════════════════════════════

import copy
import json
import logging
import time
from pathlib import Path
from typing import Callable, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


@dataclass
class GoldTrendConfig:
    """Configuration for GoldTrendAnalyzer.

    Claude20: Extracted from hardcoded constants to make gold
    momentum sensitivity tunable by evolution loop.
    """
    sub_sample_interval_s: float = 1.0
    short_window_s: float = 30.0
    medium_window_s: float = 120.0
    spike_threshold_gold: float = 500.0
    max_samples: int = 600
    advantage_thresholds: Tuple[Tuple[float, str], ...] = (
        (6000.0, "massive"),
        (3000.0, "large"),
        (1500.0, "moderate"),
        (500.0, "slight"),
    )

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "GoldTrendConfig":
        known = {k for k in cls.__dataclass_fields__}
        filtered = {k: v for k, v in d.items()
                    if k in known and k != "advantage_thresholds"}
        if "advantage_thresholds" in d:
            filtered["advantage_thresholds"] = tuple(
                tuple(t) for t in d["advantage_thresholds"]
            )
        return cls(**filtered)

    def validate(self) -> List[str]:
        errors: List[str] = []
        if self.sub_sample_interval_s <= 0:
            errors.append("sub_sample_interval_s must be > 0")
        if self.short_window_s >= self.medium_window_s:
            errors.append("short_window_s must be < medium_window_s")
        if self.spike_threshold_gold <= 0:
            errors.append("spike_threshold_gold must be > 0")
        if self.max_samples < 10:
            errors.append("max_samples must be >= 10")
        return errors


@dataclass
class PhaseDetectorConfig:
    """Configuration for PhaseDetector.

    Claude20: Makes phase transition thresholds tunable.
    Different metas have different tempo profiles.
    """
    time_laning_end_s: float = 480.0
    time_early_skirmish_end_s: float = 840.0
    time_mid_end_s: float = 1500.0
    time_late_mid_end_s: float = 1800.0
    kills_for_skirmish: int = 6
    kills_2min_for_teamfight: int = 4
    towers_for_mid: int = 2
    inhibs_for_ending: int = 1

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "PhaseDetectorConfig":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

    def validate(self) -> List[str]:
        errors: List[str] = []
        times = [self.time_laning_end_s, self.time_early_skirmish_end_s,
                 self.time_mid_end_s, self.time_late_mid_end_s]
        for i in range(len(times) - 1):
            if times[i] >= times[i + 1]:
                errors.append(f"Phase time thresholds must be strictly increasing")
                break
        if self.kills_for_skirmish < 1:
            errors.append("kills_for_skirmish must be >= 1")
        return errors


@dataclass
class SensorFusionConfig:
    """Configuration for SensorFusion module.

    Claude20: Makes fusion behavior configurable.
    """
    stale_threshold_s: float = 2.0
    alignment_tolerance_ms: float = 500.0
    dedup_hash_cache_size: int = 32
    priority_order: Tuple[str, ...] = ("lcu", "fiddler", "replay")

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "SensorFusionConfig":
        known = {k for k in cls.__dataclass_fields__}
        filtered = {k: v for k, v in d.items() if k in known and k != "priority_order"}
        if "priority_order" in d:
            filtered["priority_order"] = tuple(d["priority_order"])
        return cls(**filtered)

    def validate(self) -> List[str]:
        errors: List[str] = []
        if self.stale_threshold_s <= 0:
            errors.append("stale_threshold_s must be > 0")
        if self.alignment_tolerance_ms < 50:
            errors.append("alignment_tolerance_ms must be >= 50")
        return errors


@dataclass
class EventDetectorConfig:
    """Configuration for event detection pipeline.

    Claude20: Makes event dedup and pattern detection tunable.
    """
    dedup_window_s: float = 2.0
    multi_kill_window_s: float = 10.0
    ace_detection_enabled: bool = True
    event_history_max: int = 500

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "EventDetectorConfig":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class WardTrackerConfig:
    """Configuration for ward tracking module.

    Claude20: Makes ward timeout and position estimation tunable.
    """
    stealth_ward_duration_s: float = 90.0
    control_ward_duration_s: float = float("inf")
    zombie_ward_duration_s: float = 120.0
    position_estimation_enabled: bool = True
    max_tracked_wards: int = 50

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "WardTrackerConfig":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ─── Extended PerceptionLayerConfig ──────────────────────────────────────

@dataclass
class PerceptionLayerConfigV2(PerceptionLayerConfig):
    """Extended perception config with all sub-module configurations.

    Claude20: Adds configs for gold_trend, phase_detector, sensor_fusion,
    event_detector, and ward_tracker. Backward-compatible with V1.
    """
    gold_trend: GoldTrendConfig = field(default_factory=GoldTrendConfig)
    phase_detector: PhaseDetectorConfig = field(default_factory=PhaseDetectorConfig)
    sensor_fusion: SensorFusionConfig = field(default_factory=SensorFusionConfig)
    event_detector: EventDetectorConfig = field(default_factory=EventDetectorConfig)
    ward_tracker: WardTrackerConfig = field(default_factory=WardTrackerConfig)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "PerceptionLayerConfigV2":
        base = PerceptionLayerConfig.from_dict(d)
        return cls(
            tick_interval_ms=base.tick_interval_ms,
            warn_threshold_ms=base.warn_threshold_ms,
            kill_feed=base.kill_feed,
            minimap=base.minimap,
            state_assembler=base.state_assembler,
            gold_trend=GoldTrendConfig.from_dict(d.get("gold_trend", {})),
            phase_detector=PhaseDetectorConfig.from_dict(d.get("phase_detector", {})),
            sensor_fusion=SensorFusionConfig.from_dict(d.get("sensor_fusion", {})),
            event_detector=EventDetectorConfig.from_dict(d.get("event_detector", {})),
            ward_tracker=WardTrackerConfig.from_dict(d.get("ward_tracker", {})),
        )

    def validate_all(self) -> List[str]:
        errors: List[str] = []
        if self.tick_interval_ms < 10 or self.tick_interval_ms > 5000:
            errors.append(f"tick_interval_ms out of range: {self.tick_interval_ms}")
        errors.extend(self.gold_trend.validate())
        errors.extend(self.phase_detector.validate())
        errors.extend(self.sensor_fusion.validate())
        return errors

    def to_dict(self) -> Dict[str, Any]:
        base = super().to_dict()
        base["gold_trend"] = {
            k: getattr(self.gold_trend, k)
            for k in GoldTrendConfig.__dataclass_fields__
            if k != "advantage_thresholds"
        }
        base["phase_detector"] = {
            k: getattr(self.phase_detector, k)
            for k in PhaseDetectorConfig.__dataclass_fields__
        }
        base["sensor_fusion"] = {
            k: getattr(self.sensor_fusion, k)
            for k in SensorFusionConfig.__dataclass_fields__
            if k != "priority_order"
        }
        base["event_detector"] = {
            k: getattr(self.event_detector, k)
            for k in EventDetectorConfig.__dataclass_fields__
        }
        base["ward_tracker"] = {
            k: getattr(self.ward_tracker, k)
            for k in WardTrackerConfig.__dataclass_fields__
        }
        return base


class PerceptionConfigLoader:
    """Loads, validates, and hot-reloads perception configuration.

    Claude20: Consistent pattern with prediction/planning config loaders.
    """

    def __init__(self, config_path: Optional[str] = None) -> None:
        self._path = Path(config_path) if config_path else None
        self._last_mtime: float = 0.0
        self._current: Optional[PerceptionLayerConfigV2] = None
        self._load_count: int = 0
        self._error_count: int = 0

    def load(self) -> PerceptionLayerConfigV2:
        if self._path and self._path.exists():
            try:
                with open(self._path, "r") as f:
                    data = json.load(f)
                config = PerceptionLayerConfigV2.from_dict(data)
                errors = config.validate_all()
                if errors:
                    for err in errors:
                        logger.error("Perception config error: %s", err)
                    if self._current:
                        return self._current
                self._current = config
                self._last_mtime = self._path.stat().st_mtime
                self._load_count += 1
                return config
            except Exception as exc:
                self._error_count += 1
                logger.error("Failed to load perception config: %s", exc)

        if self._current is None:
            self._current = PerceptionLayerConfigV2()
            self._load_count += 1
        return self._current

    def has_changed(self) -> bool:
        if self._path is None or not self._path.exists():
            return False
        try:
            return self._path.stat().st_mtime > self._last_mtime
        except OSError:
            return False

    def reload(self) -> Optional[PerceptionLayerConfigV2]:
        if not self.has_changed():
            return None
        return self.load()

    @property
    def current(self) -> PerceptionLayerConfigV2:
        if self._current is None:
            return self.load()
        return self._current

    def stats(self) -> Dict[str, Any]:
        return {
            "path": str(self._path) if self._path else None,
            "load_count": self._load_count,
            "error_count": self._error_count,
        }
