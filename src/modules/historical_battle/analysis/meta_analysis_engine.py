#!/usr/bin/env python3
"""
M822 - Meta Analysis Engine
====================================
OperatorRL Historical Battle System - Current meta trends, patch impact analysis

查看英雄联盟版本分析工具的实现方式，理解其模式，
特别是版本更新对英雄胜率和登场率的影响是如何量化的。
从版本更新日志解析开始，遵循该模式实现Meta分析引擎，
使系统可以追踪当前版本的强势英雄和流行战术。

Core: Patch impact analysis, tier list generation, meta trend tracking
"""

import os
import sys
import json
import time
import math
import logging
import hashlib
import statistics
from pathlib import Path
from enum import Enum, auto
from typing import Dict, List, Any, Optional, Tuple, Set, Union, Callable
from dataclasses import dataclass, field, asdict
from abc import ABC, abstractmethod
from collections import defaultdict
from datetime import datetime, timezone

logger = logging.getLogger("operatorRL.historical_battle.analysis.meta")
logger.setLevel(logging.DEBUG)

# ─── Constants ──────────────────────────────────────────────────────────────

PATCH_CYCLE_DAYS = 14
MIN_GAMES_FOR_TIER = 100
TIER_THRESHOLDS = {"S": 0.53, "A": 0.51, "B": 0.49, "C": 0.47, "D": 0.0}
META_SHIFT_THRESHOLD = 0.03
PICKRATE_RELEVANCE_THRESHOLD = 0.01
BANRATE_HIGH_THRESHOLD = 0.30
MAX_TIER_LIST_SIZE = 200
WINRATE_ANOMALY_THRESHOLD = 0.60

class TierRank(Enum):
    S_TIER = "S"
    A_TIER = "A"
    B_TIER = "B"
    C_TIER = "C"
    D_TIER = "D"

class MetaTrend(Enum):
    RISING = "rising"
    FALLING = "falling"
    STABLE = "stable"
    NEW_META = "new_meta"
    FALLING_OFF = "falling_off"

class ChangeType(Enum):
    BUFF = "buff"
    NERF = "nerf"
    REWORK = "rework"
    ADJUST = "adjust"
    BUG_FIX = "bug_fix"

# ─── Data Models ────────────────────────────────────────────────────────────

@dataclass
class PatchChange:
    target_id: int
    target_name: str
    change_type: ChangeType
    description: str
    affected_stats: List[str] = field(default_factory=list)
    magnitude: float = 0.0  # estimated impact -1 to 1

    def to_dict(self) -> Dict[str, Any]:
        return {
            "target": self.target_name, "type": self.change_type.value,
            "description": self.description, "magnitude": round(self.magnitude, 3),
        }

@dataclass
class PatchInfo:
    patch_version: str
    release_date: str
    champion_changes: Dict[int, List[PatchChange]] = field(default_factory=dict)
    item_changes: Dict[int, List[PatchChange]] = field(default_factory=dict)
    system_changes: List[str] = field(default_factory=list)
    rune_changes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.patch_version, "date": self.release_date,
            "champ_changes": sum(len(v) for v in self.champion_changes.values()),
            "item_changes": sum(len(v) for v in self.item_changes.values()),
            "system_changes": len(self.system_changes),
        }

    @property
    def total_changes(self) -> int:
        return (sum(len(v) for v in self.champion_changes.values()) +
                sum(len(v) for v in self.item_changes.values()) +
                len(self.system_changes))

@dataclass
class ChampionTierEntry:
    champion_id: int
    champion_name: str
    role: str
    tier: TierRank
    winrate: float
    pickrate: float
    banrate: float
    games: int
    trend: MetaTrend = MetaTrend.STABLE
    winrate_delta: float = 0.0
    pickrate_delta: float = 0.0
    score: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "champion": self.champion_name, "id": self.champion_id,
            "role": self.role, "tier": self.tier.value,
            "winrate": round(self.winrate, 4),
            "pickrate": round(self.pickrate, 4),
            "banrate": round(self.banrate, 4),
            "games": self.games, "trend": self.trend.value,
            "wr_delta": round(self.winrate_delta, 4),
            "score": round(self.score, 3),
        }

@dataclass
class TierList:
    patch: str
    role: str
    generated_at: float = field(default_factory=time.time)
    entries: List[ChampionTierEntry] = field(default_factory=list)

    def get_tier(self, tier: TierRank) -> List[ChampionTierEntry]:
        return [e for e in self.entries if e.tier == tier]

    def top_n(self, n: int = 10) -> List[ChampionTierEntry]:
        return sorted(self.entries, key=lambda e: e.score, reverse=True)[:n]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "patch": self.patch, "role": self.role,
            "total_champions": len(self.entries),
            "tiers": {t.value: [e.to_dict() for e in self.get_tier(t)] for t in TierRank},
        }

@dataclass
class MetaSnapshot:
    patch: str
    timestamp: float
    tier_lists: Dict[str, TierList] = field(default_factory=dict)
    top_bans: List[Tuple[int, float]] = field(default_factory=list)
    emerging_picks: List[int] = field(default_factory=list)
    falling_picks: List[int] = field(default_factory=list)
    dominant_strategies: List[str] = field(default_factory=list)
    overall_meta_diversity: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "patch": self.patch,
            "roles_analyzed": list(self.tier_lists.keys()),
            "top_bans": self.top_bans[:10],
            "emerging": self.emerging_picks[:10],
            "falling": self.falling_picks[:10],
            "strategies": self.dominant_strategies,
            "diversity": round(self.overall_meta_diversity, 3),
        }

@dataclass
class PatchComparison:
    old_patch: str
    new_patch: str
    biggest_winners: List[Tuple[int, str, float]] = field(default_factory=list)
    biggest_losers: List[Tuple[int, str, float]] = field(default_factory=list)
    new_meta_picks: List[int] = field(default_factory=list)
    fallen_picks: List[int] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "from": self.old_patch, "to": self.new_patch,
            "winners": [{"id": w[0], "name": w[1], "delta": round(w[2], 4)} for w in self.biggest_winners[:5]],
            "losers": [{"id": l[0], "name": l[1], "delta": round(l[2], 4)} for l in self.biggest_losers[:5]],
        }


# ─── Meta Analysis Engine ──────────────────────────────────────────────────

class MetaAnalysisEngine:
    """
    Analyzes the current game meta, tracks patch-to-patch trends,
    generates tier lists, and identifies meta shifts.
    """

    def __init__(self):
        self._snapshots: List[MetaSnapshot] = []
        self._patch_history: List[PatchInfo] = []
        self._champion_history: Dict[int, List[Dict]] = defaultdict(list)

    def register_patch(self, patch: PatchInfo) -> None:
        self._patch_history.append(patch)

    def generate_tier_list(
        self, role: str, patch: str,
        champion_data: List[Dict[str, Any]],
        previous_data: Optional[List[Dict[str, Any]]] = None,
    ) -> TierList:
        """Generate a tier list for a specific role and patch."""
        tier_list = TierList(patch=patch, role=role)
        prev_map: Dict[int, Dict] = {}
        if previous_data:
            for cd in previous_data:
                prev_map[cd.get("champion_id", 0)] = cd

        for cd in champion_data:
            cid = cd.get("champion_id", 0)
            wr = cd.get("winrate", 0.5)
            pr = cd.get("pickrate", 0.0)
            br = cd.get("banrate", 0.0)
            games = cd.get("games", 0)

            if games < MIN_GAMES_FOR_TIER:
                continue

            tier = TierRank.D_TIER
            for rank_name, threshold in TIER_THRESHOLDS.items():
                if wr >= threshold:
                    tier = TierRank(rank_name)
                    break

            wr_delta = 0.0
            pr_delta = 0.0
            trend = MetaTrend.STABLE
            if cid in prev_map:
                prev = prev_map[cid]
                wr_delta = wr - prev.get("winrate", 0.5)
                pr_delta = pr - prev.get("pickrate", 0.0)
                if wr_delta > META_SHIFT_THRESHOLD:
                    trend = MetaTrend.RISING
                elif wr_delta < -META_SHIFT_THRESHOLD:
                    trend = MetaTrend.FALLING
            else:
                if pr > 0.05:
                    trend = MetaTrend.NEW_META

            # Composite score: winrate weighted by pickrate relevance
            score = wr * 0.6 + min(pr * 5, 0.3) + (1 - br) * 0.1

            tier_list.entries.append(ChampionTierEntry(
                champion_id=cid, champion_name=cd.get("name", f"Champ{cid}"),
                role=role, tier=tier, winrate=wr, pickrate=pr, banrate=br,
                games=games, trend=trend, winrate_delta=wr_delta,
                pickrate_delta=pr_delta, score=score,
            ))

        tier_list.entries.sort(key=lambda e: e.score, reverse=True)
        return tier_list

    def create_meta_snapshot(
        self, patch: str,
        data_by_role: Dict[str, List[Dict[str, Any]]],
        prev_data_by_role: Optional[Dict[str, List[Dict[str, Any]]]] = None,
    ) -> MetaSnapshot:
        """Create a full meta snapshot across all roles."""
        snapshot = MetaSnapshot(patch=patch, timestamp=time.time())

        all_pickrates = []
        for role, champ_data in data_by_role.items():
            prev_data = prev_data_by_role.get(role) if prev_data_by_role else None
            tier_list = self.generate_tier_list(role, patch, champ_data, prev_data)
            snapshot.tier_lists[role] = tier_list

            for entry in tier_list.entries:
                all_pickrates.append(entry.pickrate)
                if entry.trend == MetaTrend.RISING:
                    snapshot.emerging_picks.append(entry.champion_id)
                elif entry.trend == MetaTrend.FALLING:
                    snapshot.falling_picks.append(entry.champion_id)
                if entry.banrate > BANRATE_HIGH_THRESHOLD:
                    snapshot.top_bans.append((entry.champion_id, entry.banrate))

        snapshot.top_bans.sort(key=lambda x: x[1], reverse=True)

        # Calculate meta diversity (entropy of pick rates)
        if all_pickrates:
            total = sum(all_pickrates) or 1
            entropy = 0
            for pr in all_pickrates:
                p = pr / total
                if p > 0:
                    entropy -= p * math.log2(p)
            max_entropy = math.log2(len(all_pickrates)) if len(all_pickrates) > 1 else 1
            snapshot.overall_meta_diversity = entropy / max_entropy if max_entropy > 0 else 0

        self._snapshots.append(snapshot)
        return snapshot

    def compare_patches(self, old_patch: str, new_patch: str) -> PatchComparison:
        """Compare meta between two patches."""
        old = next((s for s in self._snapshots if s.patch == old_patch), None)
        new = next((s for s in self._snapshots if s.patch == new_patch), None)
        comparison = PatchComparison(old_patch=old_patch, new_patch=new_patch)
        if not old or not new:
            return comparison

        comparison.new_meta_picks = new.emerging_picks
        comparison.fallen_picks = new.falling_picks
        return comparison

    def get_latest_snapshot(self) -> Optional[MetaSnapshot]:
        return self._snapshots[-1] if self._snapshots else None

    def get_patch_history(self) -> List[Dict[str, Any]]:
        return [p.to_dict() for p in self._patch_history]




class MetaReportBuilder:
    """Builds formatted meta reports from snapshots."""

    def __init__(self):
        self._report_history: List[Dict] = []

    def build_patch_report(self, snapshot: MetaSnapshot, patch_info: Optional[PatchInfo] = None) -> Dict[str, Any]:
        """Build a comprehensive meta report for a patch."""
        report = {
            "patch": snapshot.patch,
            "generated": datetime.now(timezone.utc).isoformat(),
            "summary": self._generate_summary(snapshot),
            "tier_lists": {},
            "highlights": self._generate_highlights(snapshot),
            "warnings": self._generate_warnings(snapshot),
        }
        for role, tl in snapshot.tier_lists.items():
            report["tier_lists"][role] = {
                "top_5": [e.to_dict() for e in tl.top_n(5)],
                "total": len(tl.entries),
            }
        if patch_info:
            report["patch_changes"] = patch_info.to_dict()
        self._report_history.append(report)
        return report

    def _generate_summary(self, snapshot: MetaSnapshot) -> str:
        total_champs = sum(len(tl.entries) for tl in snapshot.tier_lists.values())
        rising = len(snapshot.emerging_picks)
        falling = len(snapshot.falling_picks)
        return (f"Patch {snapshot.patch}: {total_champs} champions analyzed across "
                f"{len(snapshot.tier_lists)} roles. {rising} rising, {falling} falling.")

    def _generate_highlights(self, snapshot: MetaSnapshot) -> List[str]:
        highlights = []
        for role, tl in snapshot.tier_lists.items():
            top = tl.top_n(1)
            if top:
                highlights.append(f"{role}: {top[0].champion_name} leads with {top[0].winrate:.1%} WR")
        return highlights

    def _generate_warnings(self, snapshot: MetaSnapshot) -> List[str]:
        warnings = []
        for role, tl in snapshot.tier_lists.items():
            for entry in tl.entries:
                if entry.winrate > WINRATE_ANOMALY_THRESHOLD and entry.games > 200:
                    warnings.append(f"{entry.champion_name} ({role}): abnormally high WR {entry.winrate:.1%}")
                if entry.banrate > 0.5:
                    warnings.append(f"{entry.champion_name} ({role}): very high ban rate {entry.banrate:.1%}")
        return warnings


class MetaTrendTracker:
    """Tracks how the meta evolves over multiple patches."""

    def __init__(self):
        self._champion_winrates: Dict[int, List[Tuple[str, float]]] = defaultdict(list)
        self._champion_pickrates: Dict[int, List[Tuple[str, float]]] = defaultdict(list)

    def record_snapshot(self, snapshot: MetaSnapshot) -> None:
        for role, tl in snapshot.tier_lists.items():
            for entry in tl.entries:
                key = entry.champion_id
                self._champion_winrates[key].append((snapshot.patch, entry.winrate))
                self._champion_pickrates[key].append((snapshot.patch, entry.pickrate))

    def get_champion_trend(self, champion_id: int) -> Dict[str, Any]:
        wr_history = self._champion_winrates.get(champion_id, [])
        pr_history = self._champion_pickrates.get(champion_id, [])
        if not wr_history:
            return {"champion_id": champion_id, "data_points": 0}
        wr_values = [wr for _, wr in wr_history]
        return {
            "champion_id": champion_id,
            "data_points": len(wr_history),
            "patches": [p for p, _ in wr_history],
            "winrate_trend": wr_values,
            "avg_winrate": round(statistics.mean(wr_values), 4),
            "winrate_std": round(statistics.stdev(wr_values), 4) if len(wr_values) > 1 else 0,
            "latest_winrate": round(wr_values[-1], 4),
        }

    def get_most_volatile(self, top_n: int = 10) -> List[Dict[str, Any]]:
        volatility = []
        for cid, history in self._champion_winrates.items():
            if len(history) >= 3:
                values = [wr for _, wr in history]
                std = statistics.stdev(values)
                volatility.append({"champion_id": cid, "volatility": round(std, 4), "patches": len(history)})
        return sorted(volatility, key=lambda x: x["volatility"], reverse=True)[:top_n]


class RoleMetaAnalyzer:
    """Analyzes meta specifically per role."""

    def __init__(self):
        self._role_snapshots: Dict[str, List[TierList]] = defaultdict(list)

    def add_tier_list(self, tier_list: TierList) -> None:
        self._role_snapshots[tier_list.role].append(tier_list)

    def get_role_diversity(self, role: str) -> float:
        """Calculate how diverse champion picks are for a role."""
        tier_lists = self._role_snapshots.get(role, [])
        if not tier_lists:
            return 0.0
        latest = tier_lists[-1]
        pickrates = [e.pickrate for e in latest.entries]
        if not pickrates:
            return 0.0
        total = sum(pickrates) or 1
        entropy = -sum((p/total) * math.log2(p/total) for p in pickrates if p > 0)
        max_entropy = math.log2(len(pickrates)) if len(pickrates) > 1 else 1
        return entropy / max_entropy if max_entropy > 0 else 0

    def get_role_summary(self, role: str) -> Dict[str, Any]:
        tier_lists = self._role_snapshots.get(role, [])
        if not tier_lists:
            return {"role": role, "snapshots": 0}
        latest = tier_lists[-1]
        return {
            "role": role,
            "snapshots": len(tier_lists),
            "current_patch": latest.patch,
            "champions_tracked": len(latest.entries),
            "diversity": round(self.get_role_diversity(role), 3),
            "top_champion": latest.entries[0].champion_name if latest.entries else "N/A",
        }


# ─── Module Self-Test ─────────────────────────────────────────────────────

def _self_test() -> Dict[str, Any]:
    results = {"module": "M822_meta_analysis_engine", "tests": []}

    try:
        engine = MetaAnalysisEngine()
        data = [
            {"champion_id": 1, "name": "ChampA", "winrate": 0.55, "pickrate": 0.15, "banrate": 0.1, "games": 500},
            {"champion_id": 2, "name": "ChampB", "winrate": 0.48, "pickrate": 0.08, "banrate": 0.05, "games": 300},
            {"champion_id": 3, "name": "ChampC", "winrate": 0.52, "pickrate": 0.12, "banrate": 0.35, "games": 450},
        ]
        tl = engine.generate_tier_list("MID", "14.1", data)
        assert len(tl.entries) == 3
        assert tl.entries[0].score >= tl.entries[-1].score
        results["tests"].append({"name": "tier_list_generation", "status": "pass"})
    except Exception as e:
        results["tests"].append({"name": "tier_list_generation", "status": "fail", "error": str(e)})

    try:
        engine = MetaAnalysisEngine()
        roles_data = {"MID": [
            {"champion_id": 1, "name": "A", "winrate": 0.54, "pickrate": 0.2, "banrate": 0.4, "games": 1000},
            {"champion_id": 2, "name": "B", "winrate": 0.51, "pickrate": 0.1, "banrate": 0.05, "games": 500},
        ]}
        snap = engine.create_meta_snapshot("14.2", roles_data)
        assert snap.patch == "14.2"
        assert len(snap.top_bans) > 0
        assert snap.overall_meta_diversity > 0
        results["tests"].append({"name": "meta_snapshot", "status": "pass"})
    except Exception as e:
        results["tests"].append({"name": "meta_snapshot", "status": "fail", "error": str(e)})

    try:
        pc = PatchChange(target_id=1, target_name="Ahri", change_type=ChangeType.BUFF,
                         description="Q damage increased", magnitude=0.3)
        pi = PatchInfo(patch_version="14.3", release_date="2024-02-07")
        pi.champion_changes[1] = [pc]
        assert pi.total_changes == 1
        results["tests"].append({"name": "patch_model", "status": "pass"})
    except Exception as e:
        results["tests"].append({"name": "patch_model", "status": "fail", "error": str(e)})

    results["passed"] = sum(1 for t in results["tests"] if t["status"] == "pass")
    results["total"] = len(results["tests"])
    return results


if __name__ == "__main__":
    print(json.dumps(_self_test(), indent=2))
