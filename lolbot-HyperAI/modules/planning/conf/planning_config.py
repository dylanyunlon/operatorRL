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


# ═══════════════════════════════════════════════════════════════════════════
# Claude20: Production-grade planning config with sub-module configs
# ═══════════════════════════════════════════════════════════════════════════

import copy
import json
import logging
import time
from pathlib import Path
from typing import Callable, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


class PlanningConfigError(ValueError):
    """Raised when planning configuration validation fails."""
    def __init__(self, field: str, value: Any, reason: str) -> None:
        self.field = field
        self.value = value
        self.reason = reason
        super().__init__(f"Planning config error: {field}={value!r} — {reason}")


@dataclass
class TempoConfig:
    """Configuration for tempo management (recall timing, wave control).

    Claude20: Extracted from hardcoded constants in recall_advisor.py
    and game_clock.py to enable evolution-driven tuning.
    """
    recall_health_critical: float = 0.25
    recall_health_low: float = 0.40
    recall_mana_low: float = 0.15
    recall_cooldown_s: float = 20.0
    objective_proximity_s: float = 60.0
    item_breakpoints: Tuple[Tuple[int, str], ...] = (
        (1300, "Needlessly Large Rod / B.F. Sword"),
        (1100, "Blasting Wand / Pickaxe + boots"),
        (900, "Component + Control Ward"),
        (700, "Boots upgrade"),
        (500, "Long Sword + Refillable"),
        (350, "Boots + potions"),
    )

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "TempoConfig":
        known = {k for k in cls.__dataclass_fields__}
        filtered = {k: v for k, v in d.items() if k in known and k != "item_breakpoints"}
        if "item_breakpoints" in d:
            filtered["item_breakpoints"] = tuple(
                tuple(bp) for bp in d["item_breakpoints"]
            )
        return cls(**filtered)

    def validate(self) -> List[str]:
        errors: List[str] = []
        if not (0.0 < self.recall_health_critical < 1.0):
            errors.append("recall_health_critical must be in (0, 1)")
        if self.recall_health_critical >= self.recall_health_low:
            errors.append("recall_health_critical must be < recall_health_low")
        if not (0.0 <= self.recall_mana_low < 1.0):
            errors.append("recall_mana_low must be in [0, 1)")
        if self.recall_cooldown_s < 0:
            errors.append("recall_cooldown_s must be >= 0")
        return errors


@dataclass
class PowerSpikeConfig:
    """Configuration for PowerSpikeDetector.

    Claude20: Makes champion power spike thresholds configurable.
    """
    enabled: bool = True
    key_levels: Tuple[int, ...] = (6, 11, 16)
    strong_level6_champions: Tuple[str, ...] = (
        "Zed", "Akali", "Katarina", "Fizz", "LeBlanc", "Talon",
        "Evelynn", "Rengar", "Kha'Zix", "Malzahar", "Annie",
        "Ahri", "Diana", "Syndra", "Veigar", "Lissandra",
    )
    adc_item_spike_counts: Tuple[int, ...] = (2, 3)
    item_spike_announce_cooldown_s: float = 30.0

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "PowerSpikeConfig":
        known = {k for k in cls.__dataclass_fields__}
        filtered = {}
        for k, v in d.items():
            if k in known:
                if isinstance(v, list):
                    filtered[k] = tuple(v)
                else:
                    filtered[k] = v
        return cls(**filtered)


@dataclass
class SpellTrackerConfig:
    """Configuration for SummonerSpellTracker.

    Claude20: Makes spell cooldown estimates and CDR assumptions
    configurable to adapt to different patches.
    """
    cdr_estimate: float = 0.95
    flash_cooldown_s: float = 300.0
    tp_cooldown_s: float = 360.0
    ignite_cooldown_s: float = 180.0
    exhaust_cooldown_s: float = 210.0
    heal_cooldown_s: float = 240.0
    barrier_cooldown_s: float = 180.0
    smite_cooldown_s: float = 90.0
    ghost_cooldown_s: float = 210.0
    cleanse_cooldown_s: float = 210.0
    death_flash_heuristic: bool = True

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "SpellTrackerConfig":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

    def get_cooldown(self, spell_name: str) -> float:
        """Get cooldown for a spell name, applying CDR estimate."""
        mapping = {
            "Flash": self.flash_cooldown_s,
            "Teleport": self.tp_cooldown_s,
            "Ignite": self.ignite_cooldown_s,
            "Exhaust": self.exhaust_cooldown_s,
            "Heal": self.heal_cooldown_s,
            "Barrier": self.barrier_cooldown_s,
            "Smite": self.smite_cooldown_s,
            "Ghost": self.ghost_cooldown_s,
            "Cleanse": self.cleanse_cooldown_s,
        }
        base = mapping.get(spell_name, 300.0)
        return base * self.cdr_estimate


@dataclass
class ObjectivePlanningConfig:
    """Configuration for objective-related planning decisions.

    Claude20: Centralizes objective priority weights used across
    macro_planner, objective_timer, and teamfight_caller.
    """
    baron_priority: float = 1.0
    dragon_priority: float = 1.0
    herald_priority: float = 0.55
    void_grubs_priority: float = 0.40
    tower_priority: float = 0.65
    inhibitor_priority: float = 0.85
    pre_baron_setup_s: float = 60.0
    pre_dragon_setup_s: float = 45.0
    numbers_advantage_threshold: int = 2

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ObjectivePlanningConfig":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

    def validate(self) -> List[str]:
        errors: List[str] = []
        for name in ("baron_priority", "dragon_priority", "herald_priority",
                      "void_grubs_priority", "tower_priority", "inhibitor_priority"):
            val = getattr(self, name)
            if not (0.0 <= val <= 2.0):
                errors.append(f"{name} must be in [0, 2] (got {val})")
        return errors


@dataclass
class TeamfightCallerConfig:
    """Configuration for TeamfightCaller strategy module.

    Claude20: Makes fight/disengage thresholds tunable.
    """
    engage_confidence_min: float = 0.55
    disengage_confidence_max: float = 0.40
    numbers_advantage_bonus: float = 0.15
    cooldown_ready_bonus: float = 0.10
    announce_cooldown_s: float = 8.0

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "TeamfightCallerConfig":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ─── Extended PlanningLayerConfig ────────────────────────────────────────

@dataclass
class PlanningLayerConfigV2(PlanningLayerConfig):
    """Extended planning config with all sub-module configurations.

    Claude20: Backward-compatible extension that adds configs for
    every planning sub-module. Old configs load fine (defaults used).
    """
    tempo: TempoConfig = field(default_factory=TempoConfig)
    power_spike: PowerSpikeConfig = field(default_factory=PowerSpikeConfig)
    spell_tracker: SpellTrackerConfig = field(default_factory=SpellTrackerConfig)
    objectives: ObjectivePlanningConfig = field(default_factory=ObjectivePlanningConfig)
    teamfight_caller: TeamfightCallerConfig = field(
        default_factory=TeamfightCallerConfig)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "PlanningLayerConfigV2":
        base = PlanningLayerConfig.from_dict(d)
        return cls(
            tick_interval_ms=base.tick_interval_ms,
            warn_threshold_ms=base.warn_threshold_ms,
            max_advice_per_tick=base.max_advice_per_tick,
            min_advice_confidence=base.min_advice_confidence,
            macro=base.macro,
            lane=base.lane,
            tempo=TempoConfig.from_dict(d.get("tempo", {})),
            power_spike=PowerSpikeConfig.from_dict(d.get("power_spike", {})),
            spell_tracker=SpellTrackerConfig.from_dict(d.get("spell_tracker", {})),
            objectives=ObjectivePlanningConfig.from_dict(d.get("objectives", {})),
            teamfight_caller=TeamfightCallerConfig.from_dict(
                d.get("teamfight_caller", {})),
        )

    def validate_all(self) -> List[str]:
        errors: List[str] = []
        if self.tick_interval_ms < 50 or self.tick_interval_ms > 5000:
            errors.append(f"tick_interval_ms out of range: {self.tick_interval_ms}")
        if self.min_advice_confidence < 0 or self.min_advice_confidence > 1:
            errors.append(f"min_advice_confidence must be [0,1]: {self.min_advice_confidence}")
        errors.extend(self.tempo.validate())
        errors.extend(self.objectives.validate())
        return errors

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tick_interval_ms": self.tick_interval_ms,
            "warn_threshold_ms": self.warn_threshold_ms,
            "max_advice_per_tick": self.max_advice_per_tick,
            "min_advice_confidence": self.min_advice_confidence,
            "macro": {k: getattr(self.macro, k)
                      for k in MacroPlannerConfig.__dataclass_fields__},
            "lane": {k: getattr(self.lane, k)
                     for k in LaneAdvisorConfig.__dataclass_fields__
                     if k != "active_phases"},
            "tempo": {k: getattr(self.tempo, k)
                      for k in TempoConfig.__dataclass_fields__
                      if k != "item_breakpoints"},
            "power_spike": {k: getattr(self.power_spike, k)
                            for k in PowerSpikeConfig.__dataclass_fields__
                            if k not in ("strong_level6_champions", "key_levels",
                                         "adc_item_spike_counts")},
            "spell_tracker": {k: getattr(self.spell_tracker, k)
                              for k in SpellTrackerConfig.__dataclass_fields__},
            "objectives": {k: getattr(self.objectives, k)
                           for k in ObjectivePlanningConfig.__dataclass_fields__},
            "teamfight_caller": {k: getattr(self.teamfight_caller, k)
                                  for k in TeamfightCallerConfig.__dataclass_fields__},
        }

    def diff(self, other: "PlanningLayerConfigV2") -> Dict[str, Tuple[Any, Any]]:
        """Compare two configs and return changed fields."""
        changes: Dict[str, Tuple[Any, Any]] = {}
        a, b = self.to_dict(), other.to_dict()
        _diff_nested(a, b, "", changes)
        return changes


def _diff_nested(a: Any, b: Any, prefix: str, out: Dict[str, Tuple[Any, Any]]) -> None:
    """Recursive diff helper for nested config dicts."""
    if isinstance(a, dict) and isinstance(b, dict):
        for key in set(list(a.keys()) + list(b.keys())):
            path = f"{prefix}.{key}" if prefix else key
            _diff_nested(a.get(key), b.get(key), path, out)
    elif a != b:
        out[prefix] = (a, b)


class PlanningConfigLoader:
    """Loads, validates, and hot-reloads planning configuration.

    Claude20: Mirror of PredictionConfigLoader for the planning layer.
    Supports evolution-driven parameter changes and file-based reload.
    """

    def __init__(self, config_path: Optional[str] = None) -> None:
        self._path = Path(config_path) if config_path else None
        self._last_mtime: float = 0.0
        self._current: Optional[PlanningLayerConfigV2] = None
        self._load_count: int = 0
        self._error_count: int = 0

    def load(self) -> PlanningLayerConfigV2:
        if self._path and self._path.exists():
            try:
                with open(self._path, "r") as f:
                    data = json.load(f)
                config = PlanningLayerConfigV2.from_dict(data)
                errors = config.validate_all()
                if errors:
                    for err in errors:
                        logger.error("Planning config error: %s", err)
                    if self._current:
                        return self._current
                self._current = config
                self._last_mtime = self._path.stat().st_mtime
                self._load_count += 1
                return config
            except Exception as exc:
                self._error_count += 1
                logger.error("Failed to load planning config: %s", exc)

        if self._current is None:
            self._current = PlanningLayerConfigV2()
            self._load_count += 1
        return self._current

    def has_changed(self) -> bool:
        if self._path is None or not self._path.exists():
            return False
        try:
            return self._path.stat().st_mtime > self._last_mtime
        except OSError:
            return False

    def reload(self) -> Optional[PlanningLayerConfigV2]:
        if not self.has_changed():
            return None
        return self.load()

    @property
    def current(self) -> PlanningLayerConfigV2:
        if self._current is None:
            return self.load()
        return self._current

    def stats(self) -> Dict[str, Any]:
        return {
            "path": str(self._path) if self._path else None,
            "load_count": self._load_count,
            "error_count": self._error_count,
        }
