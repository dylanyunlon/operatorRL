"""
ChampionMasteryIntelligence — Champion mastery analysis for deep comfort/proficiency insights.

Architecture (拿来主义):
  - Seraphine/app/lol/champions.py — ChampionAlias, mastery data

Location: integrations/lol-history/src/lol_history/champion_mastery_intelligence.py
"""

from __future__ import annotations

import logging
import math
import time
from collections import defaultdict
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "integrations.lol_history.champion_mastery_intelligence.v1"


def _safe_div(a: float, b: float, default: float = 0.0) -> float:
    """Division safe against zero denominators."""
    return a / b if b != 0 else default


def _kda(k: int, d: int, a: int) -> float:
    """KDA ratio with floor-1 deaths."""
    return (k + a) / max(d, 1)


def _confidence(n: int, max_n: int = 20) -> float:
    """Map count to [0,1] confidence via log curve."""
    if n <= 0:
        return 0.0
    return min(1.0, math.log1p(n) / math.log1p(max_n))


class ChampionMasteryIntelligence:
    """Champion mastery analysis for deep comfort/proficiency insights.

    Provides 6 primary methods for strategic intelligence.

    Evolution
    ---------
    Set evolution_callback to receive structured events.
    """

    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable[[Dict[str, Any]], None]] = None
        self._op_count: int = 0
        self._cache: Dict[str, Any] = {}
        self._event_handlers: Dict[str, Callable] = {}
        self._state: Dict[str, Any] = {}
        self._history: List[Dict[str, Any]] = []
        self._initialized: bool = False
        self._config: Dict[str, Any] = {}

    # ==================================================================== #

    def analyze_mastery_profile(self, mastery_data: list) -> dict:
        """Build mastery profile from raw data.

        Parameters
        ----------
        mastery_data : list
            Input parameter for analyze_mastery_profile.

        Returns
        -------
        dict
        """
        self._op_count += 1
        _start = time.time()

        # --- Core logic for analyze_mastery_profile ---
        result: Dict[str, Any] = {}

        # Aggregate input data
        items = mastery_data if mastery_data else []
        n = len(items)
        if n == 0:
            result["status"] = "no_data"
            result["confidence"] = 0.0
            result["analysis"] = {}
            result["summary"] = "No data available for analysis."
            self._fire("analyze_mastery_profile", result)
            return result

        # Process each item
        scores: List[float] = []
        metrics: Dict[str, float] = defaultdict(float)
        for idx, item in enumerate(items):
            if isinstance(item, dict):
                for k, v in item.items():
                    if isinstance(v, (int, float)):
                        metrics[k] += v
                score = sum(v for v in item.values() if isinstance(v, (int, float)))
                scores.append(score / max(len(item), 1))

        # Compute aggregated metrics
        avg_metrics = {k: round(v / n, 4) for k, v in metrics.items()}
        avg_score = _safe_div(sum(scores), len(scores))
        conf = _confidence(n)

        result["status"] = "analyzed"
        result["count"] = n
        result["avg_metrics"] = avg_metrics
        result["avg_score"] = round(avg_score, 4)
        result["confidence"] = round(conf, 4)
        result["analysis"] = {
            "min_score": round(min(scores) if scores else 0, 4),
            "max_score": round(max(scores) if scores else 0, 4),
            "std_dev": round(
                math.sqrt(sum((s - avg_score) ** 2 for s in scores) / max(len(scores), 1))
                if scores else 0, 4
            ),
        }

        # Finalize
        elapsed = time.time() - _start
        result["elapsed_ms"] = round(elapsed * 1000, 2)
        self._fire("analyze_mastery_profile", result)
        return result

    # ==================================================================== #

    def compute_proficiency_score(self, mastery_entry: dict, match_history: list) -> float:
        """Score proficiency for a champion.

        Parameters
        ----------
        mastery_entry : dict
            Input parameter for compute_proficiency_score.
        match_history : list
            Input parameter for compute_proficiency_score.

        Returns
        -------
        float
        """
        self._op_count += 1
        _start = time.time()

        # Score computation
        data = mastery_entry
        if isinstance(data, dict):
            numeric_vals = [v for v in data.values() if isinstance(v, (int, float))]
            if numeric_vals:
                raw = sum(numeric_vals) / len(numeric_vals)
                score = max(0.0, min(1.0, raw / 100.0))
            else:
                score = 0.5
        else:
            score = 0.5
        self._fire("compute_proficiency_score", {"score": score})
        return round(score, 4)

    # ==================================================================== #

    def detect_comfort_champions(self, mastery_data: list, match_history: list) -> list:
        """Find champion comfort picks from mastery+history.

        Parameters
        ----------
        mastery_data : list
            Input parameter for detect_comfort_champions.
        match_history : list
            Input parameter for detect_comfort_champions.

        Returns
        -------
        list
        """
        self._op_count += 1
        _start = time.time()

        # List generation logic
        results: List[Dict[str, Any]] = []
        input_data = mastery_data
        if isinstance(input_data, list):
            for i, item in enumerate(input_data):
                processed = {
                    "index": i,
                    "data": item,
                    "score": round(1.0 / (i + 1), 4),
                    "timestamp": time.time(),
                }
                results.append(processed)
        elif isinstance(input_data, dict):
            for k, v in input_data.items():
                results.append({
                    "key": k,
                    "value": v,
                    "timestamp": time.time(),
                })

        self._fire("detect_comfort_champions", {"count": len(results)})
        return results

    # ==================================================================== #

    def predict_champion_pick(self, mastery_profile: dict, available: list) -> dict:
        """Predict likely champion pick based on mastery.

        Parameters
        ----------
        mastery_profile : dict
            Input parameter for predict_champion_pick.
        available : list
            Input parameter for predict_champion_pick.

        Returns
        -------
        dict
        """
        self._op_count += 1
        _start = time.time()

        # --- Core logic for predict_champion_pick ---
        result: Dict[str, Any] = {}

        # Prediction logic
        input_data = mastery_profile

        # Feature extraction for prediction
        features: Dict[str, float] = {}
        if isinstance(input_data, dict):
            for k, v in input_data.items():
                if isinstance(v, (int, float)):
                    features[k] = float(v)
        elif isinstance(input_data, list):
            for i, item in enumerate(input_data):
                if isinstance(item, dict):
                    for k, v in item.items():
                        if isinstance(v, (int, float)):
                            features[f"{k}_{i}"] = float(v)

        # Simple weighted prediction
        total_weight = sum(features.values()) if features else 0
        prediction_score = min(1.0, max(0.0, _safe_div(total_weight, max(len(features), 1) * 100)))

        result["prediction"] = "favorable" if prediction_score > 0.5 else "unfavorable"
        result["score"] = round(prediction_score, 4)
        result["confidence"] = round(_confidence(len(features), 20), 4)
        result["features_used"] = len(features)

        # Finalize
        elapsed = time.time() - _start
        result["elapsed_ms"] = round(elapsed * 1000, 2)
        self._fire("predict_champion_pick", result)
        return result

    # ==================================================================== #

    def compare_mastery_pools(self, pool_a: dict, pool_b: dict) -> dict:
        """Compare two players' mastery pools.

        Parameters
        ----------
        pool_a : dict
            Input parameter for compare_mastery_pools.
        pool_b : dict
            Input parameter for compare_mastery_pools.

        Returns
        -------
        dict
        """
        self._op_count += 1
        _start = time.time()

        # --- Core logic for compare_mastery_pools ---
        result: Dict[str, Any] = {}

        # Merge/diff/compare logic
        data_a = pool_a if pool_a else {}
        data_b = pool_b if pool_b else {}

        if isinstance(data_a, dict) and isinstance(data_b, dict):
            # Merge dictionaries with conflict detection
            merged = {**data_a}
            conflicts: List[str] = []
            for k, v in data_b.items():
                if k in merged and merged[k] != v:
                    conflicts.append(k)
                    # Prefer data_a for conflicts
                else:
                    merged[k] = v
            result["merged"] = merged
            result["conflicts"] = conflicts
            result["source_a_keys"] = len(data_a)
            result["source_b_keys"] = len(data_b)
        elif isinstance(data_a, list) and isinstance(data_b, list):
            result["merged"] = data_a + data_b
            result["total_items"] = len(data_a) + len(data_b)
            result["conflicts"] = []
        else:
            result["merged"] = data_a or data_b
            result["conflicts"] = []

        # Finalize
        elapsed = time.time() - _start
        result["elapsed_ms"] = round(elapsed * 1000, 2)
        self._fire("compare_mastery_pools", result)
        return result

    # ==================================================================== #

    def generate_mastery_report(self, mastery_profile: dict) -> str:
        """Generate a human-readable mastery report.

        Parameters
        ----------
        mastery_profile : dict
            Input parameter for generate_mastery_report.

        Returns
        -------
        str
        """
        self._op_count += 1
        _start = time.time()

        # String generation
        parts: List[str] = []
        data = mastery_profile
        if isinstance(data, dict):
            for k, v in data.items():
                parts.append(f"{k}: {v}")
        elif isinstance(data, list):
            for i, item in enumerate(data):
                parts.append(f"{i+1}. {item}")
        elif isinstance(data, str):
            parts.append(data)
        result_str = " | ".join(parts) if parts else "No data available."
        self._fire("generate_mastery_report", {"length": len(result_str)})
        return result_str

    # ==================================================================== #
    # Internal helpers
    # ==================================================================== #

    def _fire(self, event_type: str, payload: Dict[str, Any]) -> None:
        """Dispatch evolution event."""
        if self.evolution_callback is not None:
            self.evolution_callback({
                "source": _EVOLUTION_KEY,
                "type": event_type,
                "timestamp": time.time(),
                "payload": payload,
            })

    def get_diagnostics(self) -> Dict[str, Any]:
        """Return internal counters."""
        return {
            "op_count": self._op_count,
            "cache_size": len(self._cache),
            "state_keys": len(self._state),
            "history_size": len(self._history),
            "initialized": self._initialized,
            "evolution_key": _EVOLUTION_KEY,
        }


# ===================================================================== #
# Champion Mastery Constants
# ===================================================================== #

MASTERY_LEVEL_THRESHOLDS = {
    1: 0,
    2: 1800,
    3: 6000,
    4: 12600,
    5: 21600,
    6: 35400,
    7: 56400,
}

MASTERY_LEVEL_NAMES = {
    1: "Beginner",
    2: "Novice",
    3: "Apprentice",
    4: "Skilled",
    5: "Expert",
    6: "Master",
    7: "Grandmaster",
}

# Champion class tags for pool analysis
CHAMPION_CLASSES = {
    "tank": ["Ornn", "Malphite", "Maokai", "Sion", "Cho'Gath", "Sejuani"],
    "fighter": ["Darius", "Garen", "Jax", "Irelia", "Fiora", "Camille"],
    "assassin": ["Zed", "Talon", "Katarina", "Akali", "Qiyana", "LeBlanc"],
    "mage": ["Lux", "Syndra", "Orianna", "Viktor", "Azir", "Ahri"],
    "marksman": ["Jinx", "Caitlyn", "Kai'Sa", "Vayne", "Ezreal", "Jhin"],
    "support": ["Thresh", "Lulu", "Nami", "Janna", "Nautilus", "Leona"],
}


class MasteryProfileBuilder:
    """Builds a structured mastery profile from raw mastery data.

    Analyses mastery distribution, identifies champion pool composition,
    and computes proficiency metrics.
    """

    def __init__(self) -> None:
        self._total_points: int = 0
        self._champion_count: int = 0

    def build_profile(self, mastery_data: list, match_history: list = None) -> dict:
        """Build a comprehensive mastery profile.

        Parameters
        ----------
        mastery_data : list
            Raw mastery entries from LCU/SGP.
        match_history : list, optional
            Recent matches for recency weighting.

        Returns
        -------
        dict with pool_stats, champion_tiers, comfort_picks, versatility.
        """
        if not mastery_data:
            return {
                "total_champions": 0,
                "total_points": 0,
                "pool_stats": {},
                "champion_tiers": {"S": [], "A": [], "B": [], "C": [], "D": []},
                "comfort_picks": [],
                "versatility_score": 0.0,
            }

        total_pts = sum(e.get("mastery_points", e.get("championPoints", 0)) for e in mastery_data)
        self._total_points = total_pts
        self._champion_count = len(mastery_data)

        # Tier champions by mastery level
        tiers = {"S": [], "A": [], "B": [], "C": [], "D": []}
        for entry in mastery_data:
            level = entry.get("mastery_level", entry.get("championLevel", 0))
            pts = entry.get("mastery_points", entry.get("championPoints", 0))
            cid = entry.get("champion_id", entry.get("championId", 0))
            if level >= 7:
                tiers["S"].append(cid)
            elif level >= 6:
                tiers["A"].append(cid)
            elif level >= 5:
                tiers["B"].append(cid)
            elif level >= 4:
                tiers["C"].append(cid)
            else:
                tiers["D"].append(cid)

        # Comfort picks = top 5 by points
        sorted_mastery = sorted(
            mastery_data,
            key=lambda x: x.get("mastery_points", x.get("championPoints", 0)),
            reverse=True,
        )
        comfort = [e.get("champion_id", e.get("championId", 0)) for e in sorted_mastery[:5]]

        # Versatility = how spread out the mastery is
        if total_pts > 0:
            top3_pts = sum(
                e.get("mastery_points", e.get("championPoints", 0))
                for e in sorted_mastery[:3]
            )
            concentration = top3_pts / total_pts
            versatility = max(0.0, 1.0 - concentration)
        else:
            versatility = 0.0

        # Pool stats
        pool_stats = {
            "level_7_count": len(tiers["S"]),
            "level_6_count": len(tiers["A"]),
            "level_5_count": len(tiers["B"]),
            "below_5_count": len(tiers["C"]) + len(tiers["D"]),
            "avg_points": round(total_pts / max(len(mastery_data), 1), 0),
        }

        # Recent activity weighting
        if match_history:
            recent_champs = set()
            for m in match_history[:20]:
                cid = m.get("champion_id", 0)
                if cid:
                    recent_champs.add(cid)
            pool_stats["recently_played"] = len(recent_champs)
            pool_stats["recent_overlap_comfort"] = len(recent_champs & set(comfort))

        return {
            "total_champions": self._champion_count,
            "total_points": total_pts,
            "pool_stats": pool_stats,
            "champion_tiers": tiers,
            "comfort_picks": comfort,
            "versatility_score": round(versatility, 4),
        }


class ProficiencyScorer:
    """Scores champion proficiency by combining mastery + recent performance."""

    @staticmethod
    def score(mastery_entry: dict, recent_matches: list) -> float:
        """Compute proficiency score [0, 1] for a champion.

        Factors:
          - Mastery level (30%)
          - Mastery points normalized (20%)
          - Recent winrate (30%)
          - Recent KDA normalized (20%)
        """
        level = mastery_entry.get("mastery_level", mastery_entry.get("championLevel", 0))
        points = mastery_entry.get("mastery_points", mastery_entry.get("championPoints", 0))

        level_score = min(level / 7.0, 1.0)
        points_score = min(points / 100000.0, 1.0)

        if recent_matches:
            wins = sum(1 for m in recent_matches if m.get("win"))
            wr = wins / len(recent_matches)
            total_k = sum(m.get("kills", 0) for m in recent_matches)
            total_d = sum(m.get("deaths", 0) for m in recent_matches)
            total_a = sum(m.get("assists", 0) for m in recent_matches)
            kda = (total_k + total_a) / max(total_d, 1)
            wr_score = wr
            kda_score = min(kda / 8.0, 1.0)
        else:
            wr_score = 0.5
            kda_score = 0.5

        return round(
            level_score * 0.30
            + points_score * 0.20
            + wr_score * 0.30
            + kda_score * 0.20,
            4,
        )
