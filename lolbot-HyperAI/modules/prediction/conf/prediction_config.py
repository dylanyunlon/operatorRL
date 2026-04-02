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
