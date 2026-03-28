"""
ML Feature Extractor - Feature engineering pipeline for prediction models.

Implements the feature engineering from leagueoflegends-optimizer
(f1: deaths/min, f2: (k+a)/min, f3: level/min) plus extended
features for higher-accuracy predictions.

All features are computed incrementally to avoid reprocessing
historical snapshots.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np

from lol_fiddler_agent.models.game_snapshot import GameSnapshot

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "lol_fiddler_agent.ml.feature_extractor.v1"


@dataclass
class FeatureSpec:
    """Specification of a single feature."""
    name: str
    description: str
    min_value: float = 0.0
    max_value: float = float('inf')
    higher_is_better: bool = True
    group: str = "basic"


# Feature registry
FEATURE_SPECS: list[FeatureSpec] = [
    # Core features from lol-optimizer
    FeatureSpec("f1_deaths_per_min", "Deaths per minute", 0, 2.0, False, "core"),
    FeatureSpec("f2_ka_per_min", "(Kills+Assists) per minute", 0, 3.0, True, "core"),
    FeatureSpec("f3_level_per_min", "Level per minute", 0, 1.5, True, "core"),
    # Extended features
    FeatureSpec("gold_diff", "Gold difference (ally - enemy)", -20000, 20000, True, "economy"),
    FeatureSpec("gold_diff_per_min", "Gold diff rate", -2000, 2000, True, "economy"),
    FeatureSpec("cs_per_min", "Creep score per minute", 0, 12, True, "economy"),
    FeatureSpec("kill_diff", "Kill difference", -30, 30, True, "combat"),
    FeatureSpec("kda_ratio", "KDA ratio", 0, 30, True, "combat"),
    FeatureSpec("ally_alive_pct", "Percentage of allies alive", 0, 1.0, True, "state"),
    FeatureSpec("enemy_alive_pct", "Percentage of enemies alive", 0, 1.0, False, "state"),
    FeatureSpec("health_pct", "Current health percentage", 0, 1.0, True, "state"),
    FeatureSpec("resource_pct", "Current resource percentage", 0, 1.0, True, "state"),
    FeatureSpec("dragon_diff", "Dragon count difference", -4, 4, True, "objective"),
    FeatureSpec("has_baron", "Team has baron buff", 0, 1, True, "objective"),
    FeatureSpec("items_diff", "Completed items difference", -5, 5, True, "economy"),
    FeatureSpec("level_diff", "Average level difference", -5, 5, True, "combat"),
    FeatureSpec("game_time_norm", "Normalized game time (0-1 for 0-40min)", 0, 1.5, True, "meta"),
    FeatureSpec("game_phase_encoded", "Game phase (0=early, 1=mid, 2=late)", 0, 2, True, "meta"),
]

FEATURE_NAMES = [f.name for f in FEATURE_SPECS]


def extract_features(snapshot: GameSnapshot) -> dict[str, float]:
    """Extract all features from a game snapshot.

    Returns a dictionary mapping feature names to values.
    """
    features: dict[str, float] = {}
    game_minutes = max(snapshot.game_time / 60, 0.1)

    # Core features
    features["f1_deaths_per_min"] = snapshot.f1_deaths_per_min
    features["f2_ka_per_min"] = snapshot.f2_ka_per_min
    features["f3_level_per_min"] = snapshot.f3_level_per_min

    # Economy
    features["gold_diff"] = float(snapshot.gold_difference)
    features["gold_diff_per_min"] = snapshot.gold_difference / game_minutes
    features["cs_per_min"] = _compute_cs_per_min(snapshot, game_minutes)

    # Combat
    features["kill_diff"] = float(snapshot.kill_difference)
    features["kda_ratio"] = _compute_kda(snapshot)

    # State
    if snapshot.ally_team:
        features["ally_alive_pct"] = snapshot.ally_team.alive_count / 5.0
    else:
        features["ally_alive_pct"] = 1.0
    if snapshot.enemy_team:
        features["enemy_alive_pct"] = snapshot.enemy_team.alive_count / 5.0
    else:
        features["enemy_alive_pct"] = 1.0

    features["health_pct"] = snapshot.my_health_pct / 100.0
    features["resource_pct"] = snapshot.my_resource_pct / 100.0

    # Objectives
    if snapshot.ally_team and snapshot.enemy_team:
        features["dragon_diff"] = float(
            snapshot.ally_team.dragon_count - snapshot.enemy_team.dragon_count
        )
        features["has_baron"] = 1.0 if snapshot.ally_team.has_baron else 0.0
        features["items_diff"] = float(
            snapshot.ally_team.completed_items - snapshot.enemy_team.completed_items
        )
        features["level_diff"] = snapshot.ally_team.avg_level - snapshot.enemy_team.avg_level
    else:
        features["dragon_diff"] = 0.0
        features["has_baron"] = 0.0
        features["items_diff"] = 0.0
        features["level_diff"] = 0.0

    # Meta
    features["game_time_norm"] = min(snapshot.game_time / (40 * 60), 1.5)
    phase_map = {"early": 0.0, "mid": 1.0, "late": 2.0}
    features["game_phase_encoded"] = phase_map.get(snapshot.game_phase, 0.0)

    return features


def extract_feature_vector(snapshot: GameSnapshot) -> list[float]:
    """Extract ordered feature vector matching FEATURE_NAMES order."""
    features = extract_features(snapshot)
    return [features.get(name, 0.0) for name in FEATURE_NAMES]


def normalize_features(
    features: dict[str, float],
    use_specs: bool = True,
) -> dict[str, float]:
    """Normalize features to [0, 1] range using spec bounds."""
    normalized: dict[str, float] = {}
    spec_map = {s.name: s for s in FEATURE_SPECS}

    for name, value in features.items():
        spec = spec_map.get(name)
        if spec and use_specs and spec.max_value != float('inf'):
            range_val = spec.max_value - spec.min_value
            if range_val > 0:
                normalized[name] = (value - spec.min_value) / range_val
            else:
                normalized[name] = 0.5
        else:
            normalized[name] = value  # Pass through unnormalized

    return normalized


def compute_win_probability(features: dict[str, float]) -> float:
    """Simple logistic model for win probability estimation.

    Uses a weighted combination of normalized features.
    Weights derived from lol-optimizer dataset analysis.
    """
    weights = {
        "f1_deaths_per_min": -2.0,  # Lower is better
        "f2_ka_per_min": 1.5,
        "f3_level_per_min": 1.0,
        "gold_diff_per_min": 0.002,
        "kill_diff": 0.05,
        "dragon_diff": 0.15,
        "has_baron": 0.3,
        "items_diff": 0.1,
        "level_diff": 0.1,
        "ally_alive_pct": 0.3,
        "enemy_alive_pct": -0.3,
    }

    score = 0.0
    for feature_name, weight in weights.items():
        score += features.get(feature_name, 0.0) * weight

    # Sigmoid
    probability = 1.0 / (1.0 + math.exp(-score))
    return max(0.01, min(0.99, probability))


def _compute_cs_per_min(snapshot: GameSnapshot, game_minutes: float) -> float:
    """Compute CS per minute for the active player."""
    my_champion = snapshot.my_champion
    for p in snapshot.players:
        if p.champion_name == my_champion:
            return p.creep_score / game_minutes
    return 0.0


def _compute_kda(snapshot: GameSnapshot) -> float:
    """Compute KDA for the active player."""
    for p in snapshot.players:
        if p.champion_name == snapshot.my_champion:
            return p.kda
    return 0.0


@dataclass
class FeatureHistory:
    """Tracks feature values over time for trend analysis."""
    max_entries: int = 300  # ~10 min at 2s intervals
    timestamps: list[float] = field(default_factory=list)
    vectors: list[list[float]] = field(default_factory=list)

    def add(self, timestamp: float, features: dict[str, float]) -> None:
        self.timestamps.append(timestamp)
        self.vectors.append([features.get(n, 0.0) for n in FEATURE_NAMES])
        # Evict old entries
        if len(self.timestamps) > self.max_entries:
            self.timestamps = self.timestamps[-self.max_entries:]
            self.vectors = self.vectors[-self.max_entries:]

    def get_trend(self, feature_name: str, window: int = 30) -> float:
        """Get trend direction for a feature (-1 to +1)."""
        if feature_name not in FEATURE_NAMES:
            return 0.0
        idx = FEATURE_NAMES.index(feature_name)
        values = [v[idx] for v in self.vectors[-window:]]
        if len(values) < 2:
            return 0.0
        # Simple linear trend
        n = len(values)
        x_mean = (n - 1) / 2
        y_mean = sum(values) / n
        num = sum((i - x_mean) * (values[i] - y_mean) for i in range(n))
        den = sum((i - x_mean) ** 2 for i in range(n))
        if den == 0:
            return 0.0
        slope = num / den
        # Normalize to [-1, 1]
        return max(-1.0, min(1.0, slope * 10))

    @property
    def entry_count(self) -> int:
        return len(self.timestamps)


# ──────────────── Pipeline Export Functions (M258) ────────────────

def features_to_triplet(
    features: dict[str, float],
    action: str = "",
    reward: float = 0.0,
) -> dict[str, Any]:
    """Convert features to AgentLightning-compatible triplet format.

    Args:
        features: Feature dict from extract_features().
        action: Action taken (e.g., "farm", "fight", "retreat").
        reward: Reward signal.

    Returns:
        Triplet dict with state, action, reward keys.
    """
    state = [features.get(n, 0.0) for n in FEATURE_NAMES]
    return {
        "state": state,
        "action": action,
        "reward": reward,
    }


def batch_features_to_dataset(
    batch: list[dict[str, Any]],
) -> dict[str, Any]:
    """Convert a batch of triplets to a training dataset.

    Args:
        batch: List of triplet dicts from features_to_triplet().

    Returns:
        Dataset dict with states, actions, rewards arrays.
    """
    states = [item.get("state", []) for item in batch]
    actions = [item.get("action", "") for item in batch]
    rewards = [item.get("reward", 0.0) for item in batch]
    return {
        "states": states,
        "actions": actions,
        "rewards": rewards,
        "size": len(batch),
    }


class IncrementalFeatureTracker:
    """Tracks features incrementally from raw game stats.

    Accepts raw stat updates (kills, deaths, assists, level, cs)
    and computes features on the fly.
    """

    def __init__(self) -> None:
        self._kills: int = 0
        self._deaths: int = 0
        self._assists: int = 0
        self._level: int = 0
        self._cs: int = 0
        self._game_time: float = 0.0

    def update(self, stats: dict[str, Any], game_time: float) -> None:
        """Update with latest raw stats."""
        self._kills = stats.get("kills", self._kills)
        self._deaths = stats.get("deaths", self._deaths)
        self._assists = stats.get("assists", self._assists)
        self._level = stats.get("level", self._level)
        self._cs = stats.get("cs", self._cs)
        self._game_time = game_time

    def current_features(self) -> dict[str, float]:
        """Compute current features from accumulated stats."""
        minutes = max(self._game_time / 60.0, 0.001)
        return {
            "f1_deaths_per_min": self._deaths / minutes,
            "f2_ka_per_min": (self._kills + self._assists) / minutes,
            "f3_level_per_min": self._level / minutes,
            "cs_per_min": self._cs / minutes,
        }

    def reset(self) -> None:
        """Reset all tracked stats."""
        self._kills = 0
        self._deaths = 0
        self._assists = 0
        self._level = 0
        self._cs = 0
        self._game_time = 0.0
