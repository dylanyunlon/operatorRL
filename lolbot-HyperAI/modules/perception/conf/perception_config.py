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
