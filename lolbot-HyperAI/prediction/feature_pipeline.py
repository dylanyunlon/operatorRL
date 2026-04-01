#!/usr/bin/env python3
"""
prediction/feature_pipeline.py — ML Feature Extraction Pipeline
=================================================================
lolbot-HyperAI · Prediction Layer

In Apollo, the prediction module takes perception output (tracked objects)
and extracts features for trajectory prediction (will this car cut in?).
Our prediction layer takes the fused GameState and extracts features
for game outcome prediction (will we win?).

Feature categories:
    1. Gold-economy features (gold diff, cs diff, item completions)
    2. Tempo features (kills/min, objectives/min in last N minutes)
    3. Composition features (team comp synergy, scaling profiles)
    4. Vision features (ward score, vision control %)
    5. Structural features (turrets, inhibs, dragon/baron state)
    6. Momentum features (recent kill streaks, comeback indicators)
    7. Historical features (player win rates, champion mastery)

The pipeline is designed for:
    - Online inference: extract features from live game state every 2s
    - Offline training: extract features from match history for model training
    - Evolution evaluation: compare feature distributions across generations

All features are normalized to [-1, 1] or [0, 1] range for model input.

Subscribes to: CH_LIVE_GAME_STATE
Publishes to: (consumed directly by win_probability_engine)
"""

from __future__ import annotations

import math
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).parent.parent))
from canbus.channel_message import (
    CH_LIVE_GAME_STATE,
    ChannelMessage,
    MessageFactory,
)
from canbus.transport import Transport


# ---------------------------------------------------------------------------
# Feature vector definition
# ---------------------------------------------------------------------------
class FeatureCategory(Enum):
    """Categories of features for interpretability."""
    GOLD_ECONOMY = "gold_economy"
    TEMPO = "tempo"
    COMPOSITION = "composition"
    VISION = "vision"
    STRUCTURAL = "structural"
    MOMENTUM = "momentum"
    HISTORICAL = "historical"
    TIME = "time"


@dataclass(frozen=True)
class FeatureSpec:
    """Metadata for a single feature."""
    name: str
    category: FeatureCategory
    description: str
    min_val: float = -1.0
    max_val: float = 1.0


# Master feature registry
FEATURE_SPECS: List[FeatureSpec] = [
    # Gold economy (8 features)
    FeatureSpec("gold_diff_norm", FeatureCategory.GOLD_ECONOMY,
                "Normalized gold difference (team total)"),
    FeatureSpec("gold_diff_per_min", FeatureCategory.GOLD_ECONOMY,
                "Gold difference per minute"),
    FeatureSpec("cs_diff_norm", FeatureCategory.GOLD_ECONOMY,
                "Normalized CS difference"),
    FeatureSpec("cs_per_min_avg", FeatureCategory.GOLD_ECONOMY,
                "Average CS per minute across team", 0, 1),
    FeatureSpec("gold_share_carry", FeatureCategory.GOLD_ECONOMY,
                "Gold share of highest-gold player", 0, 1),
    FeatureSpec("item_completion_diff", FeatureCategory.GOLD_ECONOMY,
                "Completed items differential"),
    FeatureSpec("gold_efficiency", FeatureCategory.GOLD_ECONOMY,
                "Gold to KDA efficiency ratio", 0, 1),
    FeatureSpec("bounty_state", FeatureCategory.GOLD_ECONOMY,
                "Estimated bounty gold available"),

    # Tempo (6 features)
    FeatureSpec("kills_per_min_diff", FeatureCategory.TEMPO,
                "Kill rate differential (last 5 min)"),
    FeatureSpec("kill_streak_state", FeatureCategory.TEMPO,
                "Current kill streak (+) or death streak (-)"),
    FeatureSpec("first_blood", FeatureCategory.TEMPO,
                "Did we get first blood?", 0, 1),
    FeatureSpec("recent_kill_burst", FeatureCategory.TEMPO,
                "Kills in last 60 seconds (normalized)"),
    FeatureSpec("death_timer_pressure", FeatureCategory.TEMPO,
                "Enemy death timer sum (normalized)", 0, 1),
    FeatureSpec("objective_tempo", FeatureCategory.TEMPO,
                "Objectives taken per 5 minutes"),

    # Composition (5 features)
    FeatureSpec("team_damage_profile", FeatureCategory.COMPOSITION,
                "AD/AP/mixed damage balance", 0, 1),
    FeatureSpec("scaling_score", FeatureCategory.COMPOSITION,
                "Team scaling rating (late game strength)", 0, 1),
    FeatureSpec("engage_score", FeatureCategory.COMPOSITION,
                "Team engage/initiation capacity", 0, 1),
    FeatureSpec("peel_score", FeatureCategory.COMPOSITION,
                "Team peel/protection capacity", 0, 1),
    FeatureSpec("comp_synergy", FeatureCategory.COMPOSITION,
                "Estimated team composition synergy", 0, 1),

    # Structural (6 features)
    FeatureSpec("turret_diff", FeatureCategory.STRUCTURAL,
                "Turret count differential"),
    FeatureSpec("inhib_diff", FeatureCategory.STRUCTURAL,
                "Inhibitor state differential"),
    FeatureSpec("dragon_diff", FeatureCategory.STRUCTURAL,
                "Dragon count differential"),
    FeatureSpec("baron_state", FeatureCategory.STRUCTURAL,
                "Baron buff status", 0, 1),
    FeatureSpec("elder_state", FeatureCategory.STRUCTURAL,
                "Elder dragon buff status", 0, 1),
    FeatureSpec("soul_state", FeatureCategory.STRUCTURAL,
                "Dragon soul status (-1 enemy, 0 none, +1 ally)"),

    # Momentum (4 features)
    FeatureSpec("momentum_score", FeatureCategory.MOMENTUM,
                "Overall momentum indicator"),
    FeatureSpec("comeback_potential", FeatureCategory.MOMENTUM,
                "Comeback scaling factor (higher when behind but scaling)", 0, 1),
    FeatureSpec("tilt_indicator", FeatureCategory.MOMENTUM,
                "Estimated tilt from repeated deaths", 0, 1),
    FeatureSpec("snowball_indicator", FeatureCategory.MOMENTUM,
                "Snowball progress (gold + kills + turrets combined)", 0, 1),

    # Time (3 features)
    FeatureSpec("game_time_norm", FeatureCategory.TIME,
                "Normalized game time (0-1 over 45 min)", 0, 1),
    FeatureSpec("phase_indicator", FeatureCategory.TIME,
                "Game phase as numeric (0=early, 0.33=lane, 0.66=mid, 1=late)", 0, 1),
    FeatureSpec("time_pressure", FeatureCategory.TIME,
                "Late-game time pressure (increases with time if behind)", 0, 1),
]

FEATURE_NAMES = [f.name for f in FEATURE_SPECS]
NUM_FEATURES = len(FEATURE_SPECS)


@dataclass
class FeatureVector:
    """
    Extracted feature vector for a single game state snapshot.

    All values are normalized. The vector includes interpretability
    metadata: which features contributed most to the prediction.
    """
    values: Dict[str, float] = field(default_factory=dict)
    game_time_sec: float = 0.0
    extraction_ms: int = 0
    version: int = 1

    def as_list(self) -> List[float]:
        """Return features as an ordered list (for model input)."""
        return [self.values.get(name, 0.0) for name in FEATURE_NAMES]

    def as_dict(self) -> Dict[str, float]:
        """Return features as a named dict."""
        return dict(self.values)

    def top_features(self, n: int = 5) -> List[Tuple[str, float]]:
        """Return the N features with the largest absolute values."""
        items = sorted(
            self.values.items(),
            key=lambda x: abs(x[1]),
            reverse=True,
        )
        return items[:n]

    def category_summary(self) -> Dict[str, float]:
        """Average absolute value per feature category."""
        cat_sums: Dict[str, List[float]] = {}
        spec_map = {s.name: s for s in FEATURE_SPECS}
        for name, val in self.values.items():
            spec = spec_map.get(name)
            if spec:
                cat = spec.category.value
                cat_sums.setdefault(cat, []).append(abs(val))
        return {
            cat: round(sum(vals) / len(vals), 4) if vals else 0
            for cat, vals in cat_sums.items()
        }


# ---------------------------------------------------------------------------
# Champion data for composition features
# ---------------------------------------------------------------------------
# Simplified champion classification (would come from DDragon in production)
_CHAMPION_SCALING: Dict[str, float] = {
    # 0 = pure early game, 1 = pure late game
    "Renekton": 0.25, "Pantheon": 0.3, "Lee Sin": 0.35,
    "Draven": 0.3, "Lucian": 0.4, "Caitlyn": 0.55,
    "Jinx": 0.85, "Vayne": 0.9, "Kayle": 0.95,
    "Kassadin": 0.9, "Veigar": 0.8, "Nasus": 0.85,
    "Jax": 0.8, "Vladimir": 0.75, "Azir": 0.7,
    "Viktor": 0.75, "Orianna": 0.6, "Syndra": 0.55,
    "Zed": 0.4, "Talon": 0.35, "LeBlanc": 0.4,
}

_CHAMPION_DAMAGE_TYPE: Dict[str, str] = {
    # "ad", "ap", "mixed", "true"
    "Zed": "ad", "Talon": "ad", "Lucian": "ad", "Jinx": "ad",
    "Syndra": "ap", "Veigar": "ap", "Viktor": "ap", "Azir": "ap",
    "Kayle": "mixed", "Jax": "mixed", "Corki": "mixed",
    "Vayne": "true",
}


# ---------------------------------------------------------------------------
# Feature Pipeline Component
# ---------------------------------------------------------------------------
class FeaturePipeline:
    """
    Extracts feature vectors from game state snapshots.

    Usage:
        pipeline = FeaturePipeline(transport)
        pipeline.init()
        # On each proc() tick:
        await pipeline.proc()
        # Get the latest feature vector:
        fv = pipeline.latest_features()
    """

    PROC_INTERVAL_MS = 2000  # Extract features every 2 seconds

    def __init__(self, transport: Transport) -> None:
        self._transport = transport
        self._factory = MessageFactory("prediction.feature_pipeline")
        self._latest_features: Optional[FeatureVector] = None
        self._feature_history: Deque[FeatureVector] = deque(maxlen=500)
        self._last_proc_ms = 0
        self._extraction_count = 0

        # Caches for temporal features
        self._kill_timestamps: Deque[Tuple[float, bool]] = deque(maxlen=100)
        self._objective_timestamps: Deque[Tuple[float, str]] = deque(maxlen=50)

    def init(self) -> None:
        """Subscribe to game state updates."""
        self._transport.subscribe(
            CH_LIVE_GAME_STATE, self._on_game_state,
        )

    async def proc(self) -> None:
        """Periodic feature extraction tick."""
        now_ms = int(time.monotonic() * 1000)
        if now_ms - self._last_proc_ms < self.PROC_INTERVAL_MS:
            return
        self._last_proc_ms = now_ms

        # Get latest game state from bus
        msg = self._transport.latest(CH_LIVE_GAME_STATE)
        if msg is None:
            return

        state = msg.payload
        fv = self._extract(state)
        self._latest_features = fv
        self._feature_history.append(fv)
        self._extraction_count += 1

    def _on_game_state(self, msg: ChannelMessage) -> None:
        """Cache kill/objective timestamps from game state updates."""
        payload = msg.payload
        for kill in payload.get("recent_kills", []):
            t = kill.get("game_time_sec", 0)
            is_ally = kill.get("is_ally_kill", False)
            self._kill_timestamps.append((t, is_ally))
        for obj in payload.get("recent_objectives", []):
            t = obj.get("game_time_sec", 0)
            name = obj.get("event_name", "")
            self._objective_timestamps.append((t, name))

    def _extract(self, state: Dict[str, Any]) -> FeatureVector:
        """
        Extract all features from a game state dict.

        This is the core computation — takes ~0.1ms for 32 features.
        """
        start = time.monotonic()
        fv = FeatureVector(game_time_sec=state.get("game_time_sec", 0))
        gt = fv.game_time_sec
        minutes = max(gt / 60.0, 0.1)

        our = state.get("our_team", {})
        enemy = state.get("enemy_team", {})
        objectives = state.get("objectives", {})

        # --- Gold economy features ---
        our_gold = our.get("total_gold", 0)
        enemy_gold = enemy.get("total_gold", 0)
        gold_diff = our_gold - enemy_gold
        fv.values["gold_diff_norm"] = self._normalize(gold_diff, -15000, 15000)
        fv.values["gold_diff_per_min"] = self._normalize(
            gold_diff / minutes, -2000, 2000
        )

        our_cs = our.get("total_cs", 0)
        enemy_cs = enemy.get("total_cs", 0)
        fv.values["cs_diff_norm"] = self._normalize(
            our_cs - enemy_cs, -100, 100
        )
        fv.values["cs_per_min_avg"] = self._normalize_01(
            our_cs / (5 * minutes), 0, 10
        )

        # Gold share of carry
        our_players = our.get("players", [])
        if our_players and our_gold > 0:
            max_gold = max(p.get("gold", 0) for p in our_players)
            fv.values["gold_share_carry"] = max_gold / max(our_gold, 1)
        else:
            fv.values["gold_share_carry"] = 0.2

        # Item completions
        our_items = sum(
            len(p.get("items", [])) for p in our_players
        )
        enemy_players = enemy.get("players", [])
        enemy_items = sum(
            len(p.get("items", [])) for p in enemy_players
        )
        fv.values["item_completion_diff"] = self._normalize(
            our_items - enemy_items, -15, 15
        )

        # Gold efficiency
        our_kills = our.get("total_kills", 0)
        our_assists = our.get("total_assists", 0)
        our_deaths = our.get("total_deaths", 0)
        ka = our_kills + our_assists
        gold_per_ka = our_gold / max(ka, 1)
        fv.values["gold_efficiency"] = self._normalize_01(
            gold_per_ka, 500, 3000
        )

        # Bounty estimate
        fv.values["bounty_state"] = self._normalize(
            self._estimate_bounty(our_kills, our_deaths,
                                  enemy.get("total_kills", 0),
                                  enemy.get("total_deaths", 0)),
            -2000, 2000,
        )

        # --- Tempo features ---
        our_kills_per_min = our_kills / minutes
        enemy_kills_per_min = enemy.get("total_kills", 0) / minutes
        fv.values["kills_per_min_diff"] = self._normalize(
            our_kills_per_min - enemy_kills_per_min, -1.5, 1.5
        )

        recent_kills = state.get("recent_kills", [])
        ally_recent = sum(1 for k in recent_kills if k.get("is_ally_kill"))
        enemy_recent = len(recent_kills) - ally_recent
        fv.values["kill_streak_state"] = self._normalize(
            ally_recent - enemy_recent, -5, 5
        )

        fv.values["first_blood"] = 1.0 if our_kills > 0 and gt < 180 else 0.0
        fv.values["recent_kill_burst"] = self._normalize_01(
            ally_recent, 0, 5
        )

        # Death timer pressure
        enemy_dead = sum(
            1 for p in enemy_players if p.get("is_dead", False)
        )
        fv.values["death_timer_pressure"] = self._normalize_01(
            enemy_dead, 0, 5
        )

        # Objective tempo
        obj_count = (
            objectives.get("dragon_count_ally", 0)
            + objectives.get("herald_count_ally", 0)
            + (1 if not objectives.get("baron_alive", True) else 0)
        )
        fv.values["objective_tempo"] = self._normalize_01(
            obj_count / max(minutes / 5, 0.1), 0, 2
        )

        # --- Composition features ---
        our_champs = [p.get("champion", "") for p in our_players]
        fv.values["team_damage_profile"] = self._compute_damage_profile(
            our_champs
        )
        fv.values["scaling_score"] = self._compute_scaling(our_champs)
        fv.values["engage_score"] = 0.5  # Placeholder
        fv.values["peel_score"] = 0.5    # Placeholder
        fv.values["comp_synergy"] = 0.5  # Placeholder (needs ML model)

        # --- Structural features ---
        fv.values["turret_diff"] = self._normalize(
            state.get("tower_diff", 0), -11, 11
        )
        fv.values["inhib_diff"] = self._normalize(
            (enemy.get("inhibs_destroyed", 0)
             - our.get("inhibs_destroyed", 0)),
            -3, 3,
        )
        fv.values["dragon_diff"] = self._normalize(
            state.get("dragon_diff", 0), -4, 4
        )

        fv.values["baron_state"] = 0.0  # TODO: track baron buff
        fv.values["elder_state"] = 0.0
        fv.values["soul_state"] = self._normalize(
            (1 if objectives.get("dragon_soul_ally") else 0)
            - (1 if objectives.get("dragon_soul_enemy") else 0),
            -1, 1,
        )

        # --- Momentum features ---
        fv.values["momentum_score"] = state.get("momentum_score", 0.0) \
            if callable(state.get("momentum_score")) \
            else self._compute_momentum(state)
        fv.values["comeback_potential"] = self._compute_comeback(
            gold_diff, gt, our_champs,
        )
        fv.values["tilt_indicator"] = self._compute_tilt(our_deaths, gt)
        fv.values["snowball_indicator"] = self._compute_snowball(
            gold_diff, state.get("kill_diff", 0),
            state.get("tower_diff", 0),
        )

        # --- Time features ---
        fv.values["game_time_norm"] = self._normalize_01(gt, 0, 2700)
        phase = state.get("phase", "none")
        phase_map = {
            "early_laning": 0.0, "laning": 0.33,
            "mid_game": 0.66, "late_game": 1.0,
        }
        fv.values["phase_indicator"] = phase_map.get(phase, 0.0)
        fv.values["time_pressure"] = self._compute_time_pressure(
            gold_diff, gt, our_champs,
        )

        fv.extraction_ms = int((time.monotonic() - start) * 1000)
        return fv

    # -- Normalization helpers ------------------------------------------

    @staticmethod
    def _normalize(value: float, vmin: float, vmax: float) -> float:
        """Normalize to [-1, 1] range with clamping."""
        if vmax == vmin:
            return 0.0
        n = 2.0 * (value - vmin) / (vmax - vmin) - 1.0
        return max(-1.0, min(1.0, n))

    @staticmethod
    def _normalize_01(value: float, vmin: float, vmax: float) -> float:
        """Normalize to [0, 1] range with clamping."""
        if vmax == vmin:
            return 0.0
        n = (value - vmin) / (vmax - vmin)
        return max(0.0, min(1.0, n))

    # -- Feature computation helpers ------------------------------------

    def _estimate_bounty(
        self,
        our_k: int, our_d: int,
        enemy_k: int, enemy_d: int,
    ) -> float:
        """Estimate net bounty gold available on the map."""
        # Simplified: bounties increase with kill streak
        our_streak = max(0, our_k - our_d)
        enemy_streak = max(0, enemy_k - enemy_d)
        our_bounty = min(our_streak * 100, 1000)
        enemy_bounty = min(enemy_streak * 100, 1000)
        return enemy_bounty - our_bounty

    def _compute_damage_profile(self, champions: List[str]) -> float:
        """
        Compute team damage type balance.
        0 = pure AD, 0.5 = balanced, 1 = pure AP.
        """
        if not champions:
            return 0.5
        ap_count = sum(
            1 for c in champions
            if _CHAMPION_DAMAGE_TYPE.get(c, "ad") in ("ap", "mixed")
        )
        return ap_count / max(len(champions), 1)

    def _compute_scaling(self, champions: List[str]) -> float:
        """Average scaling score of the team's champions."""
        if not champions:
            return 0.5
        scores = [
            _CHAMPION_SCALING.get(c, 0.5) for c in champions
        ]
        return sum(scores) / len(scores)

    def _compute_momentum(self, state: Dict) -> float:
        """Compute momentum from recent events."""
        recent = state.get("recent_kills", [])
        if not recent:
            return 0.0
        ally = sum(1 for k in recent if k.get("is_ally_kill"))
        enemy = len(recent) - ally
        return self._normalize(ally - enemy, -5, 5)

    def _compute_comeback(
        self,
        gold_diff: float,
        game_time: float,
        champions: List[str],
    ) -> float:
        """
        Comeback potential: high when behind but team scales well.
        """
        if gold_diff >= 0:
            return 0.0  # Not behind
        scaling = self._compute_scaling(champions)
        behind_severity = min(abs(gold_diff) / 10000, 1.0)
        time_remaining = max(0, 1.0 - game_time / 2700)
        return scaling * behind_severity * time_remaining

    def _compute_tilt(self, deaths: int, game_time: float) -> float:
        """Estimate tilt from death frequency."""
        if game_time < 60:
            return 0.0
        death_rate = deaths / (game_time / 60)
        return self._normalize_01(death_rate, 0, 2.0)

    def _compute_snowball(
        self, gold_diff: float, kill_diff: int, tower_diff: int,
    ) -> float:
        """Combined snowball indicator."""
        if gold_diff <= 0 and kill_diff <= 0:
            return 0.0
        g = self._normalize_01(gold_diff, 0, 10000)
        k = self._normalize_01(kill_diff, 0, 15)
        t = self._normalize_01(tower_diff, 0, 5)
        return 0.5 * g + 0.3 * k + 0.2 * t

    def _compute_time_pressure(
        self,
        gold_diff: float,
        game_time: float,
        champions: List[str],
    ) -> float:
        """Time pressure increases when behind with early-game comp."""
        scaling = self._compute_scaling(champions)
        if gold_diff >= 0:
            # Ahead with late-game comp = no pressure
            if scaling > 0.6:
                return 0.0
            # Ahead with early-game comp = some pressure to close
            return self._normalize_01(game_time, 1200, 2700)
        else:
            # Behind with early comp = high pressure
            if scaling < 0.4:
                return self._normalize_01(game_time, 600, 1800)
            # Behind with late comp = moderate (can outscale)
            return self._normalize_01(game_time, 1500, 2700)

    # -- Public API -----------------------------------------------------

    def latest_features(self) -> Optional[FeatureVector]:
        """Get the most recently extracted feature vector."""
        return self._latest_features

    def feature_history(
        self, last_n: Optional[int] = None,
    ) -> List[FeatureVector]:
        """Get recent feature vectors."""
        h = list(self._feature_history)
        if last_n:
            return h[-last_n:]
        return h

    def feature_trend(self, feature_name: str, last_n: int = 10) -> List[float]:
        """Get the trend of a single feature over time."""
        return [
            fv.values.get(feature_name, 0.0)
            for fv in list(self._feature_history)[-last_n:]
        ]

    def stats(self) -> Dict[str, Any]:
        """Component stats."""
        return {
            "extraction_count": self._extraction_count,
            "num_features": NUM_FEATURES,
            "latest_extraction_ms": (
                self._latest_features.extraction_ms
                if self._latest_features else 0
            ),
            "history_size": len(self._feature_history),
        }
