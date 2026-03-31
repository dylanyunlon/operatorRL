"""
DraftTrainingDataGenerator — Generates training data from historical draft outcomes.

Architecture (拿来主义):
  history_aware_draft_advisor.py（M709）— ban/pick scoring
  historical_training_exporter.py — export patterns

Location: integrations/lol-history/src/lol_history/draft_training_data_generator.py
"""
from __future__ import annotations
import logging, time
from typing import Any, Callable, Dict, List, Optional
logger = logging.getLogger(__name__)
_EVOLUTION_KEY = "integrations.lol_history.draft_training_data_generator.v1"

def _safe_div(a, b, d=0.0): return a / b if b else d

class DraftTrainingDataGenerator:
    """Generates draft-phase training samples from historical pick/ban + outcomes.

    Public API: ingest_draft_result, generate_samples, stratify_by_elo, get_stats
    """
    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable] = None
        self._op_count = 0
        self._drafts: List[Dict[str, Any]] = []
        self._generate_count = 0

    def _fire(self, et, data):
        if self.evolution_callback:
            self.evolution_callback({"type": et, "key": _EVOLUTION_KEY, **data})

    def ingest_draft_result(self, picks: List[str], bans: List[str],
                             opponent_picks: List[str], opponent_bans: List[str],
                             won: bool, elo_tier: str = "gold",
                             opponent_profiles: Dict[str, Any] = None) -> Dict[str, Any]:
        self._op_count += 1
        entry = {"picks": picks, "bans": bans, "opponent_picks": opponent_picks,
                 "opponent_bans": opponent_bans, "won": won, "elo_tier": elo_tier,
                 "opponent_profiles": opponent_profiles or {}, "timestamp": time.time()}
        self._drafts.append(entry)
        return {"status": "ok", "total_drafts": len(self._drafts)}

    def generate_samples(self, max_samples: int = 0) -> Dict[str, Any]:
        self._op_count += 1
        self._generate_count += 1
        drafts = self._drafts[:max_samples] if max_samples > 0 else self._drafts
        samples = []
        for d in drafts:
            features = {"picks": d["picks"], "bans": d["bans"],
                        "opponent_picks": d["opponent_picks"],
                        "opponent_bans": d["opponent_bans"],
                        "elo_tier": d["elo_tier"]}
            if d.get("opponent_profiles"):
                features["opponent_profiles"] = d["opponent_profiles"]
            samples.append({"features": features, "label": d["won"]})
        self._fire("samples_generated", {"count": len(samples)})
        return {"status": "ok", "samples": samples, "count": len(samples)}

    def stratify_by_elo(self, elo_tier: str) -> Dict[str, Any]:
        self._op_count += 1
        filtered = [d for d in self._drafts if d["elo_tier"] == elo_tier]
        wins = sum(1 for d in filtered if d["won"])
        return {"status": "ok", "elo_tier": elo_tier, "total": len(filtered),
                "wins": wins, "win_rate": round(_safe_div(wins, len(filtered)), 4)}

    def get_stats(self) -> Dict[str, Any]:
        elo_dist = {}
        for d in self._drafts:
            elo_dist[d["elo_tier"]] = elo_dist.get(d["elo_tier"], 0) + 1
        return {"total_drafts": len(self._drafts), "generate_count": self._generate_count,
                "elo_distribution": elo_dist, "total_ops": self._op_count}
