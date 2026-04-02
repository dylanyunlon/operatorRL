"""
Planning layer configuration.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict

@dataclass
class MacroPlannerConfig:
    cooldown_s: float = 5.0
    baron_desire_weight: float = 1.0
    dragon_desire_weight: float = 1.0
    group_desire_weight: float = 1.0
    split_push_desire_weight: float = 0.8
    defend_desire_weight: float = 1.2
    min_confidence: float = 0.3

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "MacroPlannerConfig":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

@dataclass
class LaneAdvisorConfig:
    enabled: bool = True
    tick_divisor: int = 4
    cs_warning_threshold: int = 15
    gold_back_threshold: float = 1300.0
    active_phases: tuple = ("EARLY", "MID")

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "LaneAdvisorConfig":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

@dataclass
class PlanningLayerConfig:
    tick_interval_ms: float = 500.0
    warn_threshold_ms: float = 400.0
    max_advice_per_tick: int = 3
    min_advice_confidence: float = 0.3
    macro: MacroPlannerConfig = field(default_factory=MacroPlannerConfig)
    lane: LaneAdvisorConfig = field(default_factory=LaneAdvisorConfig)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "PlanningLayerConfig":
        return cls(
            tick_interval_ms=d.get("tick_interval_ms", 500.0),
            warn_threshold_ms=d.get("warn_threshold_ms", 400.0),
            max_advice_per_tick=d.get("max_advice_per_tick", 3),
            min_advice_confidence=d.get("min_advice_confidence", 0.3),
            macro=MacroPlannerConfig.from_dict(d.get("macro", {})),
            lane=LaneAdvisorConfig.from_dict(d.get("lane", {})),
        )
