#!/usr/bin/env python3
"""
M790: Player Profiler
=====================
查看 Seraphine 上现有 Summoner 信息获取的实现方式,理解其模式,
特别是玩家档案和行为数据是如何分离的。
从 summoner-by-puuid 接口这个好例子开始。
然后,遵循该模式实现一个新的 PlayerProfiler,
让 OperatorRL 可以构建完整的玩家画像,并能追踪行为趋势。
接着引入 PlayStyleClassifier,使系统能够识别激进/保守/团队型风格,
同时优化画像缓存以减少重复计算。
随后整合 RankTrajectory,令系统支持段位变化趋势分析,
进而增强 TiltDetector 的心态检测能力。
最终完善 PlayerComparisonEngine,确保对比结果兼容所有展示模块,
全面升级玩家分析以达成个性化的战略建议。
"""

import os, sys, json, time, math, hashlib, sqlite3, threading, logging
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
    EventCategory = type('E', (), {'SYSTEM': 'system'})()

PROFILE_CACHE_TTL_SEC = 3600
MAX_PROFILE_HISTORY = 200
PLAY_STYLE_WINDOW_GAMES = 20
TILT_DETECTION_LOSS_STREAK = 3
RANK_TIERS = ["IRON","BRONZE","SILVER","GOLD","PLATINUM","EMERALD","DIAMOND","MASTER","GRANDMASTER","CHALLENGER"]
RANK_DIVISIONS = ["IV","III","II","I"]

class PlayStyle(Enum):
    AGGRESSIVE = "aggressive"
    PASSIVE = "passive"
    BALANCED = "balanced"
    TEAM_ORIENTED = "team_oriented"
    SPLIT_PUSH = "split_push"
    OBJECTIVE_FOCUSED = "objective_focused"

class TiltLevel(Enum):
    NONE = "none"
    MILD = "mild"
    MODERATE = "moderate"
    SEVERE = "severe"
    EXTREME = "extreme"

class PlayerTrend(Enum):
    IMPROVING = "improving"
    STABLE = "stable"
    DECLINING = "declining"
    VOLATILE = "volatile"

@dataclass
class RankInfo:
    tier: str = "UNRANKED"
    division: str = ""
    lp: int = 0
    wins: int = 0
    losses: int = 0
    queue_type: str = "RANKED_SOLO_5x5"
    @property
    def winrate(self) -> float:
        total = self.wins + self.losses
        return round(self.wins / max(total, 1) * 100, 1)
    @property
    def tier_value(self) -> int:
        tier_idx = RANK_TIERS.index(self.tier) if self.tier in RANK_TIERS else -1
        div_idx = RANK_DIVISIONS.index(self.division) if self.division in RANK_DIVISIONS else 0
        return tier_idx * 4 + (3 - div_idx)

@dataclass
class PlayStyleProfile:
    style: PlayStyle = PlayStyle.BALANCED
    aggression_score: float = 50.0
    farm_focus_score: float = 50.0
    vision_score_avg: float = 0.0
    roam_tendency: float = 0.0
    team_fight_participation: float = 0.0
    objective_control_rate: float = 0.0
    early_game_dominance: float = 0.0
    late_game_scaling: float = 0.0
    champion_pool_diversity: float = 0.0
    consistency_score: float = 50.0

@dataclass
class TiltIndicator:
    level: TiltLevel = TiltLevel.NONE
    loss_streak: int = 0
    death_trend: float = 0.0
    kda_decline_rate: float = 0.0
    game_duration_trend: str = "stable"
    confidence: float = 0.0
    recommendation: str = ""

@dataclass
class PlayerProfile:
    puuid: str = ""
    summoner_name: str = ""
    summoner_level: int = 0
    region: str = "NA1"
    rank_solo: RankInfo = field(default_factory=RankInfo)
    rank_flex: RankInfo = field(default_factory=RankInfo)
    play_style: PlayStyleProfile = field(default_factory=PlayStyleProfile)
    tilt_indicator: TiltIndicator = field(default_factory=TiltIndicator)
    main_champions: List[Dict] = field(default_factory=list)
    main_roles: List[str] = field(default_factory=list)
    recent_performance: Dict = field(default_factory=dict)
    trend: PlayerTrend = PlayerTrend.STABLE
    last_updated: str = ""
    profile_hash: str = ""

class PlayStyleClassifier:
    """Classifies player style based on historical match data patterns."""
    def __init__(self, logger=None):
        self._logger = logger
    def classify(self, match_history: List[Dict]) -> PlayStyleProfile:
        if not match_history:
            return PlayStyleProfile()
        profile = PlayStyleProfile()
        recent = match_history[:PLAY_STYLE_WINDOW_GAMES]
        kills_avg = self._avg(recent, "kills")
        deaths_avg = self._avg(recent, "deaths")
        assists_avg = self._avg(recent, "assists")
        cs_avg = self._avg(recent, "cs_per_min")
        vision_avg = self._avg(recent, "vision_score")
        dmg_share_avg = self._avg(recent, "damage_share")
        profile.aggression_score = min(100, (kills_avg * 8 + deaths_avg * 3))
        profile.farm_focus_score = min(100, cs_avg * 12)
        profile.vision_score_avg = vision_avg
        profile.team_fight_participation = min(100, (kills_avg + assists_avg) * 5)
        profile.early_game_dominance = self._calc_early_dominance(recent)
        profile.late_game_scaling = self._calc_late_scaling(recent)
        profile.champion_pool_diversity = self._calc_diversity(recent)
        profile.consistency_score = self._calc_consistency(recent)
        if profile.aggression_score > 70 and kills_avg > 7:
            profile.style = PlayStyle.AGGRESSIVE
        elif profile.farm_focus_score > 80 and cs_avg > 8:
            profile.style = PlayStyle.SPLIT_PUSH
        elif assists_avg > kills_avg * 1.5:
            profile.style = PlayStyle.TEAM_ORIENTED
        elif profile.aggression_score < 30:
            profile.style = PlayStyle.PASSIVE
        elif dmg_share_avg < 20 and assists_avg > 8:
            profile.style = PlayStyle.OBJECTIVE_FOCUSED
        else:
            profile.style = PlayStyle.BALANCED
        return profile
    def _avg(self, matches, key):
        vals = [m.get(key, 0) for m in matches if key in m]
        return sum(vals) / max(len(vals), 1)
    def _calc_early_dominance(self, matches):
        scores = []
        for m in matches:
            gd = m.get("early_gold_diff", 0)
            ek = m.get("early_kills", 0)
            scores.append(min(100, max(0, 50 + gd / 50 + ek * 8)))
        return round(sum(scores) / max(len(scores), 1), 1)
    def _calc_late_scaling(self, matches):
        scores = []
        for m in matches:
            dur = m.get("duration_min", 25)
            win = m.get("win", False)
            if dur > 30 and win: scores.append(80)
            elif dur > 30: scores.append(30)
            elif dur < 25 and win: scores.append(60)
            else: scores.append(50)
        return round(sum(scores) / max(len(scores), 1), 1)
    def _calc_diversity(self, matches):
        return min(100, len(set(m.get("champion", "") for m in matches)) * 10)
    def _calc_consistency(self, matches):
        kdas = [m.get("kda_ratio", 2.0) for m in matches]
        if len(kdas) < 3: return 50.0
        mean = sum(kdas) / len(kdas)
        var = sum((k - mean) ** 2 for k in kdas) / len(kdas)
        return round(max(0, 100 - math.sqrt(var) * 20), 1)

class TiltDetector:
    """Detects player tilt state from recent match patterns."""
    def __init__(self, logger=None):
        self._logger = logger
    def detect(self, recent_matches: List[Dict]) -> TiltIndicator:
        indicator = TiltIndicator()
        if not recent_matches: return indicator
        loss_streak = 0
        for m in recent_matches:
            if not m.get("win", True): loss_streak += 1
            else: break
        indicator.loss_streak = loss_streak
        recent_5 = recent_matches[:5]
        older_5 = recent_matches[5:10] if len(recent_matches) > 5 else []
        if recent_5 and older_5:
            rd = sum(m.get("deaths", 0) for m in recent_5) / len(recent_5)
            od = sum(m.get("deaths", 0) for m in older_5) / len(older_5)
            indicator.death_trend = round(rd - od, 2)
            rk = sum(m.get("kda_ratio", 2) for m in recent_5) / len(recent_5)
            ok = sum(m.get("kda_ratio", 2) for m in older_5) / len(older_5)
            indicator.kda_decline_rate = round(ok - rk, 2)
        if loss_streak >= 6:
            indicator.level = TiltLevel.EXTREME
            indicator.confidence = 0.95
            indicator.recommendation = "Take a 30+ minute break"
        elif loss_streak >= 4 or (indicator.death_trend > 3 and indicator.kda_decline_rate > 2):
            indicator.level = TiltLevel.SEVERE
            indicator.confidence = 0.85
            indicator.recommendation = "Consider ARAM or break"
        elif loss_streak >= TILT_DETECTION_LOSS_STREAK:
            indicator.level = TiltLevel.MODERATE
            indicator.confidence = 0.7
            indicator.recommendation = "Watch for emotional decisions"
        elif loss_streak >= 2 and indicator.death_trend > 1:
            indicator.level = TiltLevel.MILD
            indicator.confidence = 0.5
            indicator.recommendation = "Focus on fundamentals"
        else:
            indicator.level = TiltLevel.NONE
            indicator.confidence = 0.9
        return indicator

class RankTrajectoryAnalyzer:
    """Analyzes rank progression over time."""
    def __init__(self, logger=None):
        self._logger = logger
    def analyze_trajectory(self, rank_history: List[Dict]) -> Dict:
        if not rank_history:
            return {"trend": "unknown", "data_points": 0}
        values = []
        for entry in rank_history:
            ri = RankInfo(tier=entry.get("tier","IRON"), division=entry.get("division","IV"), lp=entry.get("lp",0))
            values.append(ri.tier_value * 100 + ri.lp)
        if len(values) < 3:
            return {"trend": "insufficient_data", "data_points": len(values)}
        fh = values[:len(values)//2]
        sh = values[len(values)//2:]
        change = (sum(sh)/len(sh)) - (sum(fh)/len(fh))
        trend = "stable"
        if change > 50: trend = "climbing"
        elif change > 20: trend = "slightly_improving"
        elif change < -50: trend = "falling"
        elif change < -20: trend = "slightly_declining"
        return {"trend": trend, "data_points": len(values), "change_score": round(change, 1),
                "current_value": values[0], "peak_value": max(values), "valley_value": min(values)}

class PlayerComparisonEngine:
    """Compares two player profiles for matchup analysis."""
    def __init__(self, logger=None):
        self._logger = logger
    def compare(self, player_a: PlayerProfile, player_b: PlayerProfile) -> Dict:
        rank_a = player_a.rank_solo
        rank_b = player_b.rank_solo
        diff = rank_a.tier_value - rank_b.tier_value
        advantage = "player_a" if diff > 2 else ("player_b" if diff < -2 else "")
        return {
            "player_a": player_a.summoner_name,
            "player_b": player_b.summoner_name,
            "rank_comparison": {
                "a_rank": f"{rank_a.tier} {rank_a.division}",
                "b_rank": f"{rank_b.tier} {rank_b.division}",
                "tier_diff": diff, "advantage": advantage,
            },
            "style_comparison": {
                "a_style": player_a.play_style.style.value,
                "b_style": player_b.play_style.style.value,
            },
            "tilt_comparison": {
                "a_tilt": player_a.tilt_indicator.level.value,
                "b_tilt": player_b.tilt_indicator.level.value,
            },
        }

class PlayerProfiler:
    """Primary player profiling engine integrating all sub-components."""
    def __init__(self, db_path: Optional[Path] = None, logger=None):
        self._logger = logger or (get_logger("M790") if callable(get_logger) else logging.getLogger("M790"))
        self._db_path = db_path or Path(__file__).parent / "player_profiles.db"
        self._style_classifier = PlayStyleClassifier(self._logger)
        self._tilt_detector = TiltDetector(self._logger)
        self._rank_analyzer = RankTrajectoryAnalyzer(self._logger)
        self._comparison_engine = PlayerComparisonEngine(self._logger)
        self._cache: Dict[str, Tuple[float, PlayerProfile]] = {}
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self):
        os.makedirs(self._db_path.parent, exist_ok=True)
        conn = sqlite3.connect(str(self._db_path))
        conn.execute("""CREATE TABLE IF NOT EXISTS player_profiles (
            puuid TEXT PRIMARY KEY, summoner_name TEXT,
            profile_json TEXT, updated_at TEXT, profile_hash TEXT)""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pname ON player_profiles(summoner_name)")
        conn.commit(); conn.close()

    def build_profile(self, puuid: str, summoner_data: Dict, match_history: List[Dict],
                      rank_data: Optional[Dict] = None,
                      rank_history: Optional[List[Dict]] = None) -> PlayerProfile:
        with self._lock:
            cached = self._cache.get(puuid)
            if cached and (time.time() - cached[0]) < PROFILE_CACHE_TTL_SEC:
                return cached[1]
        profile = PlayerProfile()
        profile.puuid = puuid
        profile.summoner_name = summoner_data.get("displayName", summoner_data.get("gameName", ""))
        profile.summoner_level = summoner_data.get("summonerLevel", 0)
        profile.region = summoner_data.get("region", "NA1")
        if rank_data:
            for q in rank_data.get("queues", []):
                ri = RankInfo(tier=q.get("tier","UNRANKED"), division=q.get("division",""),
                              lp=q.get("leaguePoints",0), wins=q.get("wins",0),
                              losses=q.get("losses",0), queue_type=q.get("queueType",""))
                if "SOLO" in ri.queue_type.upper(): profile.rank_solo = ri
                elif "FLEX" in ri.queue_type.upper(): profile.rank_flex = ri
        if match_history:
            profile.play_style = self._style_classifier.classify(match_history)
            profile.tilt_indicator = self._tilt_detector.detect(match_history)
            profile.main_champions = self._extract_main_champions(match_history)
            profile.main_roles = self._extract_main_roles(match_history)
            profile.recent_performance = self._calc_recent_perf(match_history)
            profile.trend = self._determine_trend(match_history)
        if rank_history:
            profile.recent_performance["rank_trajectory"] = \
                self._rank_analyzer.analyze_trajectory(rank_history)
        profile.last_updated = datetime.now(timezone.utc).isoformat()
        profile.profile_hash = hashlib.sha256(f"{puuid}:{profile.last_updated}".encode()).hexdigest()[:16]
        with self._lock:
            self._cache[puuid] = (time.time(), profile)
        self._store_profile(profile)
        return profile

    def _extract_main_champions(self, matches):
        stats = defaultdict(lambda: {"games": 0, "wins": 0, "kda": 0.0})
        for m in matches:
            c = m.get("champion", "Unknown")
            stats[c]["games"] += 1
            if m.get("win"): stats[c]["wins"] += 1
            stats[c]["kda"] += m.get("kda_ratio", 0)
        result = []
        for name, s in sorted(stats.items(), key=lambda x: x[1]["games"], reverse=True)[:10]:
            g = s["games"]
            result.append({"name": name, "games": g, "wins": s["wins"],
                           "winrate": round(s["wins"]/max(g,1)*100,1),
                           "avg_kda": round(s["kda"]/max(g,1), 2)})
        return result

    def _extract_main_roles(self, matches):
        return [r for r, _ in Counter(m.get("role","UNKNOWN") for m in matches).most_common(3)]

    def _calc_recent_perf(self, matches):
        r = matches[:20]
        if not r: return {}
        n = len(r)
        w = sum(1 for m in r if m.get("win"))
        k = sum(m.get("kills",0) for m in r)
        d = sum(m.get("deaths",0) for m in r)
        a = sum(m.get("assists",0) for m in r)
        return {"games":n, "wins":w, "losses":n-w, "winrate":round(w/n*100,1),
                "avg_kills":round(k/n,1), "avg_deaths":round(d/n,1),
                "avg_assists":round(a/n,1), "avg_kda":round((k+a)/max(d,1),2)}

    def _determine_trend(self, matches):
        if len(matches) < 10: return PlayerTrend.STABLE
        r5 = matches[:5]; o5 = matches[5:10]
        rwr = sum(1 for m in r5 if m.get("win"))/5
        owr = sum(1 for m in o5 if m.get("win"))/5
        diff = rwr - owr
        rk = sum(m.get("kda_ratio",2) for m in r5)/5
        ok = sum(m.get("kda_ratio",2) for m in o5)/5
        if diff > 0.2 and rk > ok: return PlayerTrend.IMPROVING
        elif diff < -0.2 and rk < ok: return PlayerTrend.DECLINING
        return PlayerTrend.STABLE

    def compare_players(self, a: PlayerProfile, b: PlayerProfile) -> Dict:
        return self._comparison_engine.compare(a, b)

    def _store_profile(self, profile):
        try:
            conn = sqlite3.connect(str(self._db_path))
            conn.execute("INSERT OR REPLACE INTO player_profiles VALUES (?,?,?,?,?)",
                (profile.puuid, profile.summoner_name,
                 json.dumps(asdict(profile), default=str),
                 profile.last_updated, profile.profile_hash))
            conn.commit(); conn.close()
        except Exception as e:
            if self._logger: self._logger.error(f"Profile store error: {e}")

    def get_profile(self, puuid: str) -> Optional[PlayerProfile]:
        with self._lock:
            cached = self._cache.get(puuid)
            if cached: return cached[1]
        conn = sqlite3.connect(str(self._db_path))
        row = conn.execute(
            "SELECT profile_json FROM player_profiles WHERE puuid = ?", (puuid,)
        ).fetchone()
        conn.close()
        if row:
            data = json.loads(row[0])
            profile = PlayerProfile()
            profile.puuid = data.get("puuid", "")
            profile.summoner_name = data.get("summoner_name", "")
            profile.summoner_level = data.get("summoner_level", 0)
            profile.last_updated = data.get("last_updated", "")
            profile.profile_hash = data.get("profile_hash", "")
            with self._lock:
                self._cache[puuid] = (time.time(), profile)
            return profile
        return None

    def get_all_profiles(self, limit: int = 50) -> List[PlayerProfile]:
        conn = sqlite3.connect(str(self._db_path))
        rows = conn.execute(
            "SELECT profile_json FROM player_profiles ORDER BY updated_at DESC LIMIT ?",
            (limit,)
        ).fetchall()
        conn.close()
        profiles = []
        for row in rows:
            data = json.loads(row[0])
            p = PlayerProfile()
            p.puuid = data.get("puuid", "")
            p.summoner_name = data.get("summoner_name", "")
            p.summoner_level = data.get("summoner_level", 0)
            profiles.append(p)
        return profiles

    def invalidate_cache(self, puuid: str = None):
        with self._lock:
            if puuid:
                self._cache.pop(puuid, None)
            else:
                self._cache.clear()

    def export_profile_json(self, puuid: str) -> Optional[str]:
        profile = self.get_profile(puuid)
        if not profile:
            return None
        return json.dumps(asdict(profile), indent=2, default=str)


# ============================================================================
# Match History Aggregator
# ============================================================================

class MatchHistoryAggregator:
    """Aggregates match history data for profiling consumption."""

    def __init__(self, logger=None):
        self._logger = logger

    def aggregate(self, raw_matches: List[Dict]) -> List[Dict]:
        aggregated = []
        for match in raw_matches:
            participants = match.get("participants", [])
            stats = match.get("stats", {})
            if not stats and participants:
                stats = participants[0].get("stats", {})
            kills = stats.get("kills", 0)
            deaths = stats.get("deaths", 0)
            assists = stats.get("assists", 0)
            duration_sec = match.get("gameDuration", 1500)
            duration_min = max(duration_sec / 60, 1)
            total_cs = stats.get("totalMinionsKilled", 0) + stats.get("neutralMinionsKilled", 0)

            aggregated.append({
                "game_id": str(match.get("gameId", "")),
                "champion": match.get("championName", stats.get("championName", "Unknown")),
                "win": stats.get("win", False),
                "kills": kills,
                "deaths": deaths,
                "assists": assists,
                "kda_ratio": round((kills + assists) / max(deaths, 1), 2),
                "cs_per_min": round(total_cs / duration_min, 1),
                "vision_score": stats.get("visionScore", 0),
                "damage_share": stats.get("damageShare", 0),
                "gold_earned": stats.get("goldEarned", 0),
                "role": match.get("role", stats.get("role", "UNKNOWN")),
                "lane": match.get("lane", stats.get("lane", "UNKNOWN")),
                "duration_min": round(duration_min, 1),
                "early_gold_diff": stats.get("earlyGoldDiff", 0),
                "early_kills": stats.get("earlyKills", 0),
                "timestamp": match.get("gameCreation", 0),
                "queue_id": match.get("queueId", 0),
            })
        return aggregated

    def filter_ranked(self, matches: List[Dict]) -> List[Dict]:
        ranked_queues = {420, 440}
        return [m for m in matches if m.get("queue_id", 0) in ranked_queues]

    def filter_by_champion(self, matches: List[Dict], champion: str) -> List[Dict]:
        return [m for m in matches if m.get("champion", "").lower() == champion.lower()]

    def filter_recent(self, matches: List[Dict], days: int = 30) -> List[Dict]:
        cutoff = time.time() * 1000 - (days * 86400 * 1000)
        return [m for m in matches if m.get("timestamp", 0) > cutoff]


# ============================================================================
# Behavior Analyzer
# ============================================================================

class BehaviorAnalyzer:
    """Analyzes player behavior patterns over time windows."""

    def __init__(self, logger=None):
        self._logger = logger

    def analyze_session_pattern(self, matches: List[Dict]) -> Dict:
        if len(matches) < 2:
            return {"sessions": 0, "avg_session_length": 0}
        timestamps = sorted([m.get("timestamp", 0) for m in matches], reverse=True)
        sessions = []
        current_session = [timestamps[0]]
        session_gap_ms = 90 * 60 * 1000
        for i in range(1, len(timestamps)):
            gap = timestamps[i - 1] - timestamps[i]
            if gap < session_gap_ms:
                current_session.append(timestamps[i])
            else:
                sessions.append(current_session)
                current_session = [timestamps[i]]
        sessions.append(current_session)
        avg_len = sum(len(s) for s in sessions) / max(len(sessions), 1)
        return {
            "sessions": len(sessions),
            "avg_session_length": round(avg_len, 1),
            "longest_session": max(len(s) for s in sessions),
            "avg_gap_hours": round(
                sum(
                    (sessions[i][-1] - sessions[i + 1][0]) / 3600000
                    for i in range(len(sessions) - 1)
                ) / max(len(sessions) - 1, 1), 1
            ) if len(sessions) > 1 else 0,
        }

    def analyze_time_preference(self, matches: List[Dict]) -> Dict:
        from datetime import datetime as dt
        hours = []
        for m in matches:
            ts = m.get("timestamp", 0)
            if ts > 0:
                try:
                    h = dt.fromtimestamp(ts / 1000).hour
                    hours.append(h)
                except (OSError, ValueError):
                    pass
        if not hours:
            return {"peak_hour": -1, "distribution": {}}
        hour_counts = Counter(hours)
        peak = hour_counts.most_common(1)[0][0]
        morning = sum(hour_counts.get(h, 0) for h in range(6, 12))
        afternoon = sum(hour_counts.get(h, 0) for h in range(12, 18))
        evening = sum(hour_counts.get(h, 0) for h in range(18, 24))
        night = sum(hour_counts.get(h, 0) for h in list(range(0, 6)))
        return {
            "peak_hour": peak,
            "morning_pct": round(morning / max(len(hours), 1) * 100, 1),
            "afternoon_pct": round(afternoon / max(len(hours), 1) * 100, 1),
            "evening_pct": round(evening / max(len(hours), 1) * 100, 1),
            "night_pct": round(night / max(len(hours), 1) * 100, 1),
        }

    def detect_champion_pool_shift(self, matches: List[Dict], window: int = 20) -> Dict:
        if len(matches) < window * 2:
            return {"shift_detected": False}
        recent = Counter(m.get("champion", "") for m in matches[:window])
        older = Counter(m.get("champion", "") for m in matches[window:window * 2])
        recent_top = set(c for c, _ in recent.most_common(3))
        older_top = set(c for c, _ in older.most_common(3))
        new_picks = recent_top - older_top
        dropped = older_top - recent_top
        return {
            "shift_detected": bool(new_picks or dropped),
            "new_champions": list(new_picks),
            "dropped_champions": list(dropped),
            "recent_top3": [c for c, _ in recent.most_common(3)],
            "older_top3": [c for c, _ in older.most_common(3)],
        }


# ============================================================================
# Profile Exporter
# ============================================================================

class ProfileExporter:
    """Exports player profiles in various formats for downstream modules."""

    def __init__(self, logger=None):
        self._logger = logger

    def to_strategy_input(self, profile: PlayerProfile) -> Dict:
        return {
            "puuid": profile.puuid,
            "name": profile.summoner_name,
            "rank_tier": profile.rank_solo.tier,
            "rank_division": profile.rank_solo.division,
            "winrate": profile.rank_solo.winrate,
            "play_style": profile.play_style.style.value,
            "aggression": profile.play_style.aggression_score,
            "consistency": profile.play_style.consistency_score,
            "tilt_level": profile.tilt_indicator.level.value,
            "tilt_confidence": profile.tilt_indicator.confidence,
            "main_champions": [c.get("name", "") for c in profile.main_champions[:5]],
            "main_roles": profile.main_roles[:3],
            "trend": profile.trend.value if isinstance(profile.trend, PlayerTrend) else str(profile.trend),
        }

    def to_voice_summary(self, profile: PlayerProfile) -> str:
        parts = []
        parts.append(f"玩家 {profile.summoner_name}")
        if profile.rank_solo.tier != "UNRANKED":
            parts.append(f"段位 {profile.rank_solo.tier} {profile.rank_solo.division}")
            parts.append(f"胜率 {profile.rank_solo.winrate}%")
        style_map = {
            "aggressive": "激进型", "passive": "保守型",
            "balanced": "均衡型", "team_oriented": "团队型",
            "split_push": "分推型", "objective_focused": "目标型",
        }
        style_val = profile.play_style.style.value if isinstance(
            profile.play_style.style, PlayStyle) else str(profile.play_style.style)
        style_cn = style_map.get(style_val, style_val)
        parts.append(f"风格 {style_cn}")
        if profile.main_champions:
            top3 = [c.get("name", "") for c in profile.main_champions[:3]]
            parts.append(f"常用英雄 {' '.join(top3)}")
        tilt_val = profile.tilt_indicator.level.value if isinstance(
            profile.tilt_indicator.level, TiltLevel) else str(profile.tilt_indicator.level)
        if tilt_val not in ("none", "TiltLevel.NONE"):
            parts.append(f"心态状态 {tilt_val}")
        return "，".join(parts)

    def to_dashboard_card(self, profile: PlayerProfile) -> Dict:
        return {
            "type": "player_card",
            "name": profile.summoner_name,
            "level": profile.summoner_level,
            "rank": f"{profile.rank_solo.tier} {profile.rank_solo.division}",
            "lp": profile.rank_solo.lp,
            "winrate": profile.rank_solo.winrate,
            "style": profile.play_style.style.value if isinstance(
                profile.play_style.style, PlayStyle) else str(profile.play_style.style),
            "tilt": profile.tilt_indicator.level.value if isinstance(
                profile.tilt_indicator.level, TiltLevel) else str(profile.tilt_indicator.level),
            "champions": profile.main_champions[:5],
            "trend": profile.trend.value if isinstance(
                profile.trend, PlayerTrend) else str(profile.trend),
            "performance": profile.recent_performance,
        }


def _self_test():
    print("[M790] PlayerProfiler self-test...")
    classifier = PlayStyleClassifier()
    mock = [{"kills":10,"deaths":2,"assists":5,"cs_per_min":8.5,"vision_score":25,
             "damage_share":30,"champion":"Yasuo","win":True,"kda_ratio":7.5,
             "role":"MIDDLE","early_gold_diff":500,"early_kills":2,"duration_min":28}]*10
    style = classifier.classify(mock)
    assert isinstance(style.style, PlayStyle)
    detector = TiltDetector()
    tilt_m = [{"win":False,"deaths":8,"kda_ratio":0.5}]*4 + [{"win":True,"deaths":2,"kda_ratio":4.0}]*6
    tilt = detector.detect(tilt_m)
    assert tilt.loss_streak == 4
    profiler = PlayerProfiler(db_path=Path("/tmp/test_profiles.db"))
    profile = profiler.build_profile("test-puuid", {"displayName":"TestPlayer","summonerLevel":150},
        mock, {"queues":[{"tier":"GOLD","division":"II","leaguePoints":55,"wins":120,"losses":100,"queueType":"RANKED_SOLO_5x5"}]})
    assert profile.summoner_name == "TestPlayer"
    assert profile.rank_solo.tier == "GOLD"
    print(f"  Profile: {profile.summoner_name}, {profile.rank_solo.tier} {profile.rank_solo.division}")
    print(f"  Style: {profile.play_style.style.value}")
    print(f"  Tilt: {profile.tilt_indicator.level.value}")
    print("[M790] All tests passed.\n")
    return True

if __name__ == "__main__":
    _self_test()
