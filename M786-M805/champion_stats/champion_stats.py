#!/usr/bin/env python3
"""
M791: Champion Stats
====================
查看 Seraphine 上现有 ChampionMastery 数据的实现方式,理解其模式,
特别是英雄统计和精通度数据是如何分离的。
从 champion-mastery endpoint 这个好例子开始。
然后,遵循该模式实现一个新的 ChampionStatsManager。

Reference: operatorRL agentic system / Seraphine LCU patterns
"""

import os, sys, json, time, math, hashlib, sqlite3, threading, logging, struct, re
from enum import Enum
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple, Union, Set, Callable
from dataclasses import dataclass, field, asdict
from collections import defaultdict, Counter, OrderedDict, deque

sys.path.insert(0, str(Path(__file__).parent.parent))
try:
    from logging_system.core_logger import get_logger, EventCategory
except ImportError:
    get_logger = lambda x: logging.getLogger(x)
    EventCategory = type('E', (), {'SYSTEM': 'system', 'DATA_PROCESSING': 'data_processing',
        'MATCH_ANALYSIS': 'match_analysis', 'LCU_API': 'lcu_api',
        'NETWORK': 'network', 'PERFORMANCE': 'performance'})()


# ============================================================================
# Constants & Configuration
# ============================================================================

MAX_CHAMPION_POOL = 200
MASTERY_LEVELS = {7: "Mastery 7", 6: "Mastery 6", 5: "Mastery 5",
                  4: "Mastery 4", 3: "Mastery 3", 2: "Mastery 2", 1: "Mastery 1"}
META_TIER_LIST = ["S+", "S", "A", "B", "C", "D"]
LANE_POSITIONS = ["TOP", "JUNGLE", "MIDDLE", "BOTTOM", "SUPPORT"]
SYNERGY_THRESHOLD = 0.55


class ChampionRole(Enum):
    TANK = "tank"
    FIGHTER = "fighter"
    ASSASSIN = "assassin"
    MAGE = "mage"
    MARKSMAN = "marksman"
    SUPPORT = "support"


class DamageType(Enum):
    PHYSICAL = "physical"
    MAGIC = "magic"
    TRUE = "true"
    MIXED = "mixed"


@dataclass
class ChampionMastery:
    champion_id: int = 0
    champion_name: str = ""
    mastery_level: int = 0
    mastery_points: int = 0
    last_play_time: int = 0
    chest_granted: bool = False
    tokens_earned: int = 0


@dataclass
class ChampionWinRate:
    champion_name: str = ""
    games_played: int = 0
    wins: int = 0
    losses: int = 0
    avg_kda: float = 0.0
    avg_cs_per_min: float = 0.0
    avg_damage_share: float = 0.0
    best_lane: str = ""
    pick_rate: float = 0.0
    ban_rate: float = 0.0
    @property
    def winrate(self) -> float:
        return round(self.wins / max(self.games_played, 1) * 100, 1)


@dataclass
class CounterRelation:
    champion: str = ""
    counter: str = ""
    win_rate_against: float = 50.0
    games_sample: int = 0
    gold_diff_at_15: float = 0.0
    confidence: float = 0.0
    lane: str = ""


@dataclass
class SynergyRelation:
    champion_a: str = ""
    champion_b: str = ""
    combined_winrate: float = 50.0
    games_sample: int = 0
    synergy_score: float = 0.0
    best_composition_role: str = ""


@dataclass
class MetaTierEntry:
    champion_name: str = ""
    tier: str = "B"
    lane: str = ""
    win_rate: float = 50.0
    pick_rate: float = 0.0
    ban_rate: float = 0.0
    patch_version: str = ""
    trend: str = "stable"


class MetaTracker:
    """Tracks current meta champion tiers based on aggregate data."""
    def __init__(self, logger=None):
        self._logger = logger
        self._tier_list: Dict[str, List[MetaTierEntry]] = defaultdict(list)
        self._patch_version = ""

    def update_meta(self, tier_data: List[Dict], patch: str = ""):
        self._patch_version = patch
        self._tier_list.clear()
        for entry in tier_data:
            meta = MetaTierEntry(
                champion_name=entry.get("champion", ""), tier=entry.get("tier", "B"),
                lane=entry.get("lane", ""), win_rate=entry.get("win_rate", 50.0),
                pick_rate=entry.get("pick_rate", 0.0), ban_rate=entry.get("ban_rate", 0.0),
                patch_version=patch, trend=entry.get("trend", "stable"))
            self._tier_list[meta.lane].append(meta)

    def get_tier(self, champion: str, lane: str = "") -> Optional[MetaTierEntry]:
        for entries in ([self._tier_list.get(lane, [])] if lane else self._tier_list.values()):
            for e in entries:
                if e.champion_name.lower() == champion.lower():
                    return e
        return None

    def get_top_champions(self, lane: str, limit: int = 10) -> List[MetaTierEntry]:
        tier_order = {t: i for i, t in enumerate(META_TIER_LIST)}
        entries = self._tier_list.get(lane, [])
        return sorted(entries, key=lambda e: (tier_order.get(e.tier, 99), -e.win_rate))[:limit]

    def is_meta(self, champion: str) -> bool:
        entry = self.get_tier(champion)
        return entry is not None and entry.tier in ("S+", "S", "A")


class CounterPicker:
    """Provides counter-pick recommendations based on matchup data."""
    def __init__(self, logger=None):
        self._logger = logger
        self._counter_db: Dict[str, List[CounterRelation]] = defaultdict(list)

    def load_counters(self, counter_data: List[Dict]):
        self._counter_db.clear()
        for entry in counter_data:
            cr = CounterRelation(champion=entry.get("champion", ""), counter=entry.get("counter", ""),
                win_rate_against=entry.get("win_rate_against", 50.0), games_sample=entry.get("games", 0),
                gold_diff_at_15=entry.get("gold_diff_15", 0.0), confidence=entry.get("confidence", 0.5),
                lane=entry.get("lane", ""))
            self._counter_db[cr.champion.lower()].append(cr)

    def get_counters(self, champion: str, lane: str = "", limit: int = 5) -> List[CounterRelation]:
        counters = self._counter_db.get(champion.lower(), [])
        if lane:
            counters = [c for c in counters if c.lane == lane or not c.lane]
        return sorted(counters, key=lambda c: c.win_rate_against, reverse=True)[:limit]

    def get_weak_against(self, champion: str, limit: int = 5) -> List[CounterRelation]:
        counters = self._counter_db.get(champion.lower(), [])
        weak = [c for c in counters if c.win_rate_against < 48]
        return sorted(weak, key=lambda c: c.win_rate_against)[:limit]


class SynergyCalculator:
    """Calculates champion synergy scores for team composition."""
    def __init__(self, logger=None):
        self._logger = logger
        self._synergy_db: Dict[str, SynergyRelation] = {}

    def load_synergies(self, synergy_data: List[Dict]):
        self._synergy_db.clear()
        for entry in synergy_data:
            sr = SynergyRelation(
                champion_a=entry.get("champion_a", ""), champion_b=entry.get("champion_b", ""),
                combined_winrate=entry.get("combined_winrate", 50.0),
                games_sample=entry.get("games", 0), synergy_score=entry.get("synergy_score", 0.0))
            key = f"{sr.champion_a.lower()}:{sr.champion_b.lower()}"
            self._synergy_db[key] = sr

    def get_synergy(self, champ_a: str, champ_b: str) -> Optional[SynergyRelation]:
        for key in (f"{champ_a.lower()}:{champ_b.lower()}", f"{champ_b.lower()}:{champ_a.lower()}"):
            if key in self._synergy_db:
                return self._synergy_db[key]
        return None

    def calculate_team_synergy(self, team: List[str]) -> float:
        if len(team) < 2: return 50.0
        scores = []
        for i in range(len(team)):
            for j in range(i+1, len(team)):
                syn = self.get_synergy(team[i], team[j])
                if syn: scores.append(syn.synergy_score)
        return round(sum(scores) / max(len(scores), 1), 1) if scores else 50.0

    def find_best_partner(self, champion: str, available: List[str]) -> Optional[SynergyRelation]:
        best = None
        for a in available:
            syn = self.get_synergy(champion, a)
            if syn and (best is None or syn.synergy_score > best.synergy_score):
                best = syn
        return best


class ChampionRecommender:
    """Recommends champions based on player profile and current meta."""
    def __init__(self, meta: MetaTracker, counter: CounterPicker, synergy: SynergyCalculator, logger=None):
        self._meta = meta
        self._counters = counter
        self._synergy = synergy
        self._logger = logger

    def recommend(self, player_champs: List[Dict], enemy_picks=None, ally_picks=None,
                  lane: str = "", limit: int = 5) -> List[Dict]:
        candidates = []
        for pc in player_champs:
            name = pc.get("name", "")
            games = pc.get("games", 0)
            wr = pc.get("winrate", 50.0)
            if games < 3: continue
            score = wr * 0.3
            meta_entry = self._meta.get_tier(name, lane)
            if meta_entry:
                bonus = {"S+":20,"S":15,"A":10,"B":5,"C":0,"D":-5}
                score += bonus.get(meta_entry.tier, 0)
            if enemy_picks:
                for enemy in enemy_picks:
                    for c in self._counters.get_counters(enemy, lane):
                        if c.counter.lower() == name.lower():
                            score += (c.win_rate_against - 50) * 0.5
            if ally_picks:
                for ally in ally_picks:
                    syn = self._synergy.get_synergy(name, ally)
                    if syn: score += syn.synergy_score * 0.2
            candidates.append({"champion": name, "score": round(score, 1), "games": games,
                               "personal_winrate": wr,
                               "meta_tier": meta_entry.tier if meta_entry else "?"})
        return sorted(candidates, key=lambda c: c["score"], reverse=True)[:limit]


class ChampionStatsManager:
    """Primary champion statistics engine."""
    def __init__(self, db_path: Optional[Path] = None, logger=None):
        self._logger = logger or (get_logger("M791") if callable(get_logger) else logging.getLogger("M791"))
        self._db_path = db_path or Path(__file__).parent / "champion_stats.db"
        self._meta_tracker = MetaTracker(self._logger)
        self._counter_picker = CounterPicker(self._logger)
        self._synergy_calc = SynergyCalculator(self._logger)
        self._recommender = ChampionRecommender(
            self._meta_tracker, self._counter_picker, self._synergy_calc, self._logger)
        self._mastery_cache: Dict[str, List[ChampionMastery]] = {}
        self._winrate_cache: Dict[str, List[ChampionWinRate]] = {}
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self):
        os.makedirs(self._db_path.parent, exist_ok=True)
        conn = sqlite3.connect(str(self._db_path))
        conn.execute("""CREATE TABLE IF NOT EXISTS champion_mastery (
            puuid TEXT, champion_id INTEGER, champion_name TEXT,
            mastery_level INTEGER, mastery_points INTEGER,
            last_play_time INTEGER, updated_at TEXT,
            PRIMARY KEY (puuid, champion_id))""")
        conn.execute("""CREATE TABLE IF NOT EXISTS champion_winrates (
            puuid TEXT, champion_name TEXT, games_played INTEGER,
            wins INTEGER, losses INTEGER, avg_kda REAL, avg_cs REAL,
            best_lane TEXT, updated_at TEXT,
            PRIMARY KEY (puuid, champion_name))""")
        conn.commit(); conn.close()

    def update_mastery(self, puuid: str, mastery_data: List[Dict]):
        masteries = []
        for m in mastery_data:
            cm = ChampionMastery(champion_id=m.get("championId",0),
                champion_name=m.get("championName",str(m.get("championId",0))),
                mastery_level=m.get("championLevel",0), mastery_points=m.get("championPoints",0),
                last_play_time=m.get("lastPlayTime",0), chest_granted=m.get("chestGranted",False),
                tokens_earned=m.get("tokensEarned",0))
            masteries.append(cm)
        with self._lock:
            self._mastery_cache[puuid] = masteries
        self._store_mastery(puuid, masteries)

    def update_winrates(self, puuid: str, match_history: List[Dict]):
        stats = defaultdict(lambda: {"games":0,"wins":0,"kda":0,"cs":0,"lanes":Counter()})
        for m in match_history:
            c = m.get("champion","Unknown")
            stats[c]["games"] += 1
            if m.get("win"): stats[c]["wins"] += 1
            stats[c]["kda"] += m.get("kda_ratio",0)
            stats[c]["cs"] += m.get("cs_per_min",0)
            stats[c]["lanes"][m.get("lane","UNKNOWN")] += 1
        winrates = []
        for name, s in stats.items():
            g = s["games"]
            wr = ChampionWinRate(champion_name=name, games_played=g,
                wins=s["wins"], losses=g-s["wins"],
                avg_kda=round(s["kda"]/max(g,1),2),
                avg_cs_per_min=round(s["cs"]/max(g,1),1),
                best_lane=s["lanes"].most_common(1)[0][0] if s["lanes"] else "")
            winrates.append(wr)
        with self._lock:
            self._winrate_cache[puuid] = winrates

    def get_winrates(self, puuid: str) -> List[ChampionWinRate]:
        with self._lock:
            return self._winrate_cache.get(puuid, [])

    def recommend_champion(self, puuid: str, enemy_picks=None, ally_picks=None, lane="") -> List[Dict]:
        wrs = self.get_winrates(puuid)
        pc = [{"name":w.champion_name,"games":w.games_played,"winrate":w.winrate} for w in wrs]
        return self._recommender.recommend(pc, enemy_picks, ally_picks, lane)

    def _store_mastery(self, puuid, masteries):
        try:
            conn = sqlite3.connect(str(self._db_path))
            now = datetime.now(timezone.utc).isoformat()
            for cm in masteries:
                conn.execute("INSERT OR REPLACE INTO champion_mastery VALUES (?,?,?,?,?,?,?)",
                    (puuid, cm.champion_id, cm.champion_name, cm.mastery_level,
                     cm.mastery_points, cm.last_play_time, now))
            conn.commit(); conn.close()
        except Exception as e:
            if self._logger: self._logger.error(f"Mastery store error: {e}")

    @property
    def meta_tracker(self): return self._meta_tracker
    @property
    def counter_picker(self): return self._counter_picker
    @property
    def synergy_calculator(self): return self._synergy_calc

    def get_mastery(self, puuid: str) -> List[ChampionMastery]:
        with self._lock:
            return self._mastery_cache.get(puuid, [])

    def get_champion_detail(self, puuid: str, champion_name: str) -> Dict:
        winrates = self.get_winrates(puuid)
        for wr in winrates:
            if wr.champion_name.lower() == champion_name.lower():
                return {
                    "champion": wr.champion_name,
                    "games": wr.games_played,
                    "wins": wr.wins,
                    "losses": wr.losses,
                    "winrate": wr.winrate,
                    "avg_kda": wr.avg_kda,
                    "avg_cs": wr.avg_cs_per_min,
                    "best_lane": wr.best_lane,
                    "meta_tier": self._meta_tracker.get_tier(champion_name),
                }
        return {"champion": champion_name, "games": 0}

    def _store_winrates(self, puuid: str, winrates: List[ChampionWinRate]):
        try:
            conn = sqlite3.connect(str(self._db_path))
            now = datetime.now(timezone.utc).isoformat()
            for wr in winrates:
                conn.execute("INSERT OR REPLACE INTO champion_winrates VALUES (?,?,?,?,?,?,?,?,?)",
                    (puuid, wr.champion_name, wr.games_played,
                     wr.wins, wr.losses, wr.avg_kda,
                     wr.avg_cs_per_min, wr.best_lane, now))
            conn.commit(); conn.close()
        except Exception as e:
            if self._logger: self._logger.error(f"Winrate store error: {e}")

    def export_for_draft(self, puuid: str) -> List[Dict]:
        wrs = self.get_winrates(puuid)
        return sorted([{
            "name": wr.champion_name,
            "games": wr.games_played,
            "winrate": wr.winrate,
            "kda": wr.avg_kda,
            "lane": wr.best_lane,
            "is_meta": self._meta_tracker.is_meta(wr.champion_name),
        } for wr in wrs if wr.games_played >= 3], key=lambda x: x["winrate"], reverse=True)


# ============================================================================
# Champion Pool Analyzer
# ============================================================================

class ChampionPoolAnalyzer:
    """Analyzes player champion pool depth, breadth, and flexibility."""

    def __init__(self, logger=None):
        self._logger = logger

    def analyze_pool(self, winrates: List[ChampionWinRate],
                     masteries: List[ChampionMastery] = None) -> Dict:
        if not winrates:
            return {"pool_size": 0, "depth": "none"}

        total_games = sum(wr.games_played for wr in winrates)
        played_champs = [wr for wr in winrates if wr.games_played >= 3]
        comfortable = [wr for wr in played_champs if wr.games_played >= 10]
        mastered = [wr for wr in comfortable if wr.winrate >= 55]

        lanes_played = set(wr.best_lane for wr in played_champs if wr.best_lane)
        lane_depth = {}
        for lane in LANE_POSITIONS:
            lane_champs = [wr for wr in played_champs if wr.best_lane == lane]
            lane_depth[lane] = {
                "champions": len(lane_champs),
                "total_games": sum(wr.games_played for wr in lane_champs),
                "avg_winrate": round(
                    sum(wr.winrate for wr in lane_champs) / max(len(lane_champs), 1), 1
                ),
            }

        concentration = 0.0
        if total_games > 0:
            for wr in winrates:
                share = wr.games_played / total_games
                concentration += share * share

        if len(mastered) >= 5:
            depth = "deep"
        elif len(comfortable) >= 5:
            depth = "moderate"
        elif len(played_champs) >= 3:
            depth = "shallow"
        else:
            depth = "limited"

        flex_score = len(lanes_played) * 15 + len(played_champs) * 3
        flex_score = min(100, flex_score)

        return {
            "pool_size": len(played_champs),
            "comfortable_count": len(comfortable),
            "mastered_count": len(mastered),
            "depth": depth,
            "lanes_covered": sorted(lanes_played),
            "lane_depth": lane_depth,
            "concentration_index": round(concentration, 3),
            "flexibility_score": round(flex_score, 1),
            "total_games_analyzed": total_games,
        }

    def find_gaps(self, winrates: List[ChampionWinRate]) -> List[str]:
        lanes_covered = set()
        for wr in winrates:
            if wr.games_played >= 5 and wr.best_lane:
                lanes_covered.add(wr.best_lane)
        return [lane for lane in LANE_POSITIONS if lane not in lanes_covered]


# ============================================================================
# Patch Impact Tracker
# ============================================================================

class PatchImpactTracker:
    """Tracks how patch changes affect champion performance."""

    def __init__(self, logger=None):
        self._logger = logger
        self._patch_data: Dict[str, Dict[str, MetaTierEntry]] = {}

    def record_patch(self, patch: str, tier_data: List[MetaTierEntry]):
        self._patch_data[patch] = {e.champion_name: e for e in tier_data}

    def compare_patches(self, patch_a: str, patch_b: str,
                        champion: str) -> Optional[Dict]:
        data_a = self._patch_data.get(patch_a, {})
        data_b = self._patch_data.get(patch_b, {})
        entry_a = data_a.get(champion)
        entry_b = data_b.get(champion)
        if not entry_a or not entry_b:
            return None
        return {
            "champion": champion,
            "patch_from": patch_a,
            "patch_to": patch_b,
            "winrate_change": round(entry_b.win_rate - entry_a.win_rate, 2),
            "pickrate_change": round(entry_b.pick_rate - entry_a.pick_rate, 2),
            "banrate_change": round(entry_b.ban_rate - entry_a.ban_rate, 2),
            "tier_from": entry_a.tier,
            "tier_to": entry_b.tier,
            "significant": abs(entry_b.win_rate - entry_a.win_rate) > 2.0,
        }

    def get_biggest_winners(self, patch_a: str, patch_b: str,
                            limit: int = 5) -> List[Dict]:
        results = []
        data_a = self._patch_data.get(patch_a, {})
        data_b = self._patch_data.get(patch_b, {})
        for champ in data_b:
            comp = self.compare_patches(patch_a, patch_b, champ)
            if comp:
                results.append(comp)
        return sorted(results, key=lambda x: x["winrate_change"], reverse=True)[:limit]

    def get_biggest_losers(self, patch_a: str, patch_b: str,
                           limit: int = 5) -> List[Dict]:
        results = []
        data_a = self._patch_data.get(patch_a, {})
        data_b = self._patch_data.get(patch_b, {})
        for champ in data_b:
            comp = self.compare_patches(patch_a, patch_b, champ)
            if comp:
                results.append(comp)
        return sorted(results, key=lambda x: x["winrate_change"])[:limit]


# ============================================================================
# Ban Prioritizer
# ============================================================================

class BanPrioritizer:
    """Prioritizes bans based on meta, opponent data, and team weaknesses."""

    def __init__(self, meta: MetaTracker, counter: CounterPicker, logger=None):
        self._meta = meta
        self._counter = counter
        self._logger = logger

    def prioritize(self, our_team: List[str] = None,
                   enemy_mains: List[Dict] = None,
                   lane: str = "") -> List[Dict]:
        candidates = {}

        top_meta = self._meta.get_top_champions(lane, limit=20) if lane else []
        for entry in top_meta:
            if entry.tier in ("S+", "S"):
                name = entry.champion_name
                candidates[name] = candidates.get(name, 0) + 15
            elif entry.tier == "A":
                name = entry.champion_name
                candidates[name] = candidates.get(name, 0) + 8

        if enemy_mains:
            for champ_info in enemy_mains[:5]:
                name = champ_info.get("name", "")
                games = champ_info.get("games", 0)
                wr = champ_info.get("winrate", 50)
                if games >= 10 and wr >= 55:
                    candidates[name] = candidates.get(name, 0) + 20
                elif games >= 5:
                    candidates[name] = candidates.get(name, 0) + 10

        if our_team:
            for ally_champ in our_team:
                weak = self._counter.get_weak_against(ally_champ, limit=3)
                for wr in weak:
                    name = wr.counter
                    candidates[name] = candidates.get(name, 0) + 12

        if our_team:
            for name in list(candidates.keys()):
                if name in our_team:
                    del candidates[name]

        sorted_bans = sorted(candidates.items(), key=lambda x: x[1], reverse=True)
        return [{"champion": name, "priority_score": score}
                for name, score in sorted_bans[:5]]


def _self_test():
    print("[M791] ChampionStatsManager self-test...")
    meta = MetaTracker()
    meta.update_meta([
        {"champion":"Jinx","tier":"S","lane":"BOTTOM","win_rate":52.5,"pick_rate":12.0},
        {"champion":"Yasuo","tier":"A","lane":"MIDDLE","win_rate":50.1,"pick_rate":15.0},
        {"champion":"Leona","tier":"S+","lane":"SUPPORT","win_rate":53.2,"pick_rate":8.0}
    ], patch="14.5")
    assert meta.is_meta("Jinx")
    assert not meta.is_meta("UnknownChamp")
    counter = CounterPicker()
    counter.load_counters([{"champion":"Yasuo","counter":"Malzahar","win_rate_against":55.0,
        "games":5000,"gold_diff_15":-300,"confidence":0.9,"lane":"MIDDLE"}])
    assert len(counter.get_counters("Yasuo","MIDDLE")) == 1
    synergy = SynergyCalculator()
    synergy.load_synergies([{"champion_a":"Jinx","champion_b":"Leona",
        "combined_winrate":55.0,"games":3000,"synergy_score":8.5}])
    assert synergy.get_synergy("Jinx","Leona") is not None
    mgr = ChampionStatsManager(db_path=Path("/tmp/test_champ_stats.db"))
    mgr.update_winrates("test-puuid", [
        {"champion":"Jinx","win":True,"kda_ratio":5.0,"cs_per_min":8.5,"lane":"BOTTOM"},
        {"champion":"Jinx","win":True,"kda_ratio":4.0,"cs_per_min":7.0,"lane":"BOTTOM"},
        {"champion":"Yasuo","win":False,"kda_ratio":2.0,"cs_per_min":7.5,"lane":"MIDDLE"}])
    assert len(mgr.get_winrates("test-puuid")) == 2
    print("[M791] All tests passed.\n")
    return True

if __name__ == "__main__":
    _self_test()
