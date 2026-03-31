#!/usr/bin/env python3
"""
M890 — OpponentHistoryAnalyzer
==============================
Analyzes opponent match history data (from M886 intercepts + M887 profiles)
to extract champion pools, win rates, item builds, and rune preferences.
Produces structured opponent intelligence reports.

Dependencies: M886, M887
Reference: Seraphine tools.py data processing patterns
"""
from __future__ import annotations
import asyncio, collections, json, logging, math, time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from enum import Enum, auto

logger = logging.getLogger("M890.OpponentHistoryAnalyzer")

RECENT_MATCH_WINDOW = 20
TOP_CHAMPIONS_COUNT = 5
CONFIDENCE_THRESHOLD = 0.6


class ThreatLevel(Enum):
    LOW = auto()
    MEDIUM = auto()
    HIGH = auto()
    EXTREME = auto()


@dataclass
class ChampionStats:
    champion_id: int
    champion_name: str = ""
    games_played: int = 0
    wins: int = 0
    losses: int = 0
    avg_kda: float = 0.0
    avg_cs_per_min: float = 0.0
    avg_gold_per_min: float = 0.0
    avg_damage_share: float = 0.0
    most_common_items: List[int] = field(default_factory=list)
    most_common_runes: List[int] = field(default_factory=list)
    last_played: Optional[datetime] = None

    @property
    def winrate(self) -> float:
        total = self.wins + self.losses
        return (self.wins / total * 100) if total > 0 else 0.0

    @property
    def games_total(self) -> int:
        return self.wins + self.losses

    def to_dict(self) -> Dict[str, Any]:
        return {
            "champion_id": self.champion_id,
            "champion_name": self.champion_name,
            "games": self.games_total,
            "winrate": round(self.winrate, 1),
            "avg_kda": round(self.avg_kda, 2),
            "avg_cs_min": round(self.avg_cs_per_min, 1),
            "core_items": self.most_common_items[:6],
            "runes": self.most_common_runes[:2],
        }


@dataclass
class OpponentProfile:
    puuid: str
    display_name: str = ""
    overall_winrate: float = 0.0
    total_games_analyzed: int = 0
    champion_pool: List[ChampionStats] = field(default_factory=list)
    preferred_roles: List[str] = field(default_factory=list)
    playstyle_tags: List[str] = field(default_factory=list)
    threat_level: ThreatLevel = ThreatLevel.MEDIUM
    avg_game_duration_pref: float = 0.0
    early_game_rating: float = 5.0
    mid_game_rating: float = 5.0
    late_game_rating: float = 5.0
    last_analyzed: Optional[datetime] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "puuid": self.puuid,
            "name": self.display_name,
            "overall_wr": round(self.overall_winrate, 1),
            "games_analyzed": self.total_games_analyzed,
            "threat": self.threat_level.name,
            "top_champions": [c.to_dict() for c in self.champion_pool[:TOP_CHAMPIONS_COUNT]],
            "roles": self.preferred_roles,
            "style": self.playstyle_tags,
            "game_phase_ratings": {
                "early": round(self.early_game_rating, 1),
                "mid": round(self.mid_game_rating, 1),
                "late": round(self.late_game_rating, 1),
            },
        }


class MatchDataExtractor:
    """Extracts structured data from raw match JSON (Fiddler-intercepted)."""

    @staticmethod
    def extract_participant(match_json: Dict, puuid: str) -> Optional[Dict]:
        """Find a specific participant in match data."""
        participants = match_json.get("participants", [])
        identities = match_json.get("participantIdentities", [])
        puuid_to_pid = {}
        for ident in identities:
            p = ident.get("player", {})
            pid = ident.get("participantId")
            if p.get("puuid") == puuid:
                puuid_to_pid[puuid] = pid
                break
            if p.get("accountId"):
                puuid_to_pid[p.get("puuid", "")] = pid

        pid = puuid_to_pid.get(puuid)
        if pid is None:
            return None

        for p in participants:
            if p.get("participantId") == pid:
                stats = p.get("stats", {})
                timeline = p.get("timeline", {})
                return {
                    "champion_id": p.get("championId", 0),
                    "spell1": p.get("spell1Id", 0),
                    "spell2": p.get("spell2Id", 0),
                    "win": stats.get("win", False),
                    "kills": stats.get("kills", 0),
                    "deaths": stats.get("deaths", 0),
                    "assists": stats.get("assists", 0),
                    "cs": stats.get("totalMinionsKilled", 0) + stats.get("neutralMinionsKilled", 0),
                    "gold": stats.get("goldEarned", 0),
                    "damage": stats.get("totalDamageDealtToChampions", 0),
                    "items": [stats.get(f"item{i}", 0) for i in range(7)],
                    "rune_primary": stats.get("perkPrimaryStyle", 0),
                    "rune_secondary": stats.get("perkSubStyle", 0),
                    "rune_keystone": stats.get("perk0", 0),
                    "role": timeline.get("role", ""),
                    "lane": timeline.get("lane", ""),
                    "game_duration": match_json.get("gameDuration", 0),
                    "cs_per_min_deltas": timeline.get("creepsPerMinDeltas", {}),
                    "gold_per_min_deltas": timeline.get("goldPerMinDeltas", {}),
                }
        return None

    @staticmethod
    def compute_kda(kills: int, deaths: int, assists: int) -> float:
        return (kills + assists) / max(deaths, 1)


class PlaystyleClassifier:
    """Classify opponent playstyle from aggregate stats."""

    @staticmethod
    def classify(profile: OpponentProfile) -> List[str]:
        tags = []
        if profile.early_game_rating >= 7.0:
            tags.append("aggressive_early")
        if profile.late_game_rating >= 7.0:
            tags.append("scaling_player")
        if profile.avg_game_duration_pref < 25 * 60:
            tags.append("early_closer")
        elif profile.avg_game_duration_pref > 35 * 60:
            tags.append("late_game_pref")

        top_champ_pool_size = sum(1 for c in profile.champion_pool if c.games_played >= 3)
        if top_champ_pool_size <= 2:
            tags.append("one_trick")
        elif top_champ_pool_size >= 5:
            tags.append("versatile_pool")

        avg_kda_all = sum(c.avg_kda * c.games_total for c in profile.champion_pool)
        total_games = sum(c.games_total for c in profile.champion_pool)
        if total_games > 0:
            weighted_kda = avg_kda_all / total_games
            if weighted_kda >= 4.0:
                tags.append("kda_player")
            elif weighted_kda <= 2.0:
                tags.append("high_risk_player")

        return tags

    @staticmethod
    def assess_threat(profile: OpponentProfile) -> ThreatLevel:
        score = 0
        if profile.overall_winrate >= 60:
            score += 3
        elif profile.overall_winrate >= 55:
            score += 2
        elif profile.overall_winrate >= 50:
            score += 1

        if profile.champion_pool:
            best_wr = max(c.winrate for c in profile.champion_pool)
            if best_wr >= 70:
                score += 2
            elif best_wr >= 60:
                score += 1

        if profile.total_games_analyzed >= 15:
            score += 1

        if score >= 5:
            return ThreatLevel.EXTREME
        elif score >= 3:
            return ThreatLevel.HIGH
        elif score >= 1:
            return ThreatLevel.MEDIUM
        return ThreatLevel.LOW


class OpponentHistoryAnalyzer:
    """
    Analyzes opponent historical data from M886 intercepted match history.

    Flow:
      M886 RingBuffer → raw match JSONs → extract participant data
      → aggregate per-champion stats → classify playstyle → assess threat

    Thread-safe: analysis runs in asyncio tasks, results cached per puuid.
    """

    def __init__(self, interceptor=None, profile_crawler=None):
        self._interceptor = interceptor
        self._crawler = profile_crawler
        self._extractor = MatchDataExtractor()
        self._classifier = PlaystyleClassifier()
        self._profiles: Dict[str, OpponentProfile] = {}
        self._analysis_lock = asyncio.Lock()
        self._stats = {
            "analyses_completed": 0,
            "matches_processed": 0,
            "cache_size": 0,
        }
        logger.info("OpponentHistoryAnalyzer initialized")

    async def analyze_opponent(self, puuid: str, force: bool = False) -> OpponentProfile:
        """Analyze a single opponent. Returns cached result if available."""
        async with self._analysis_lock:
            if not force and puuid in self._profiles:
                cached = self._profiles[puuid]
                if cached.last_analyzed:
                    age = (datetime.now(timezone.utc) - cached.last_analyzed).total_seconds()
                    if age < 300:
                        return cached

            match_data = self._get_match_data(puuid)
            profile = self._build_profile(puuid, match_data)
            profile.playstyle_tags = self._classifier.classify(profile)
            profile.threat_level = self._classifier.assess_threat(profile)
            profile.last_analyzed = datetime.now(timezone.utc)

            self._profiles[puuid] = profile
            self._stats["analyses_completed"] += 1
            self._stats["cache_size"] = len(self._profiles)

            logger.info("Analyzed %s: %s threat=%s wr=%.1f%% champs=%d",
                        puuid[:12], profile.display_name, profile.threat_level.name,
                        profile.overall_winrate, len(profile.champion_pool))
            return profile

    async def analyze_team(self, puuids: List[str]) -> List[OpponentProfile]:
        """Analyze an entire enemy team."""
        tasks = [self.analyze_opponent(p) for p in puuids]
        return await asyncio.gather(*tasks)

    def _get_match_data(self, puuid: str) -> List[Dict]:
        """Get raw match data from M886 interceptor."""
        if not self._interceptor:
            return []
        entries = self._interceptor.get_summoner_history(puuid)
        matches = []
        for entry in entries:
            try:
                body = entry.response_body
                if body:
                    data = json.loads(body)
                    games = data.get("games", {}).get("games", [])
                    matches.extend(games[:RECENT_MATCH_WINDOW])
            except (json.JSONDecodeError, AttributeError):
                continue
        return matches[:RECENT_MATCH_WINDOW]

    def _build_profile(self, puuid: str, matches: List[Dict]) -> OpponentProfile:
        """Build opponent profile from match data."""
        champion_agg: Dict[int, Dict[str, Any]] = collections.defaultdict(lambda: {
            "wins": 0, "losses": 0, "kills": [], "deaths": [], "assists": [],
            "cs": [], "gold": [], "damage": [], "items": [], "runes": [],
            "durations": [], "roles": [], "cs_early": [],
        })
        total_wins = 0
        total_losses = 0
        durations = []
        roles_counter: collections.Counter = collections.Counter()

        for match in matches:
            participant = self._extractor.extract_participant(match, puuid)
            if not participant:
                continue
            self._stats["matches_processed"] += 1
            cid = participant["champion_id"]
            agg = champion_agg[cid]

            if participant["win"]:
                agg["wins"] += 1
                total_wins += 1
            else:
                agg["losses"] += 1
                total_losses += 1

            agg["kills"].append(participant["kills"])
            agg["deaths"].append(participant["deaths"])
            agg["assists"].append(participant["assists"])
            agg["cs"].append(participant["cs"])
            agg["gold"].append(participant["gold"])
            agg["damage"].append(participant["damage"])
            agg["items"].extend([i for i in participant["items"] if i > 0])
            agg["runes"].append(participant["rune_keystone"])
            dur = participant["game_duration"]
            agg["durations"].append(dur)
            durations.append(dur)
            role = participant.get("lane", participant.get("role", ""))
            if role:
                agg["roles"].append(role)
                roles_counter[role] += 1

            cs_deltas = participant.get("cs_per_min_deltas", {})
            early_cs = cs_deltas.get("0-10", 0)
            if early_cs:
                agg["cs_early"].append(early_cs)

        champ_stats_list = []
        for cid, agg in champion_agg.items():
            total_g = agg["wins"] + agg["losses"]
            if total_g == 0:
                continue
            avg_kills = sum(agg["kills"]) / total_g if agg["kills"] else 0
            avg_deaths = sum(agg["deaths"]) / total_g if agg["deaths"] else 0
            avg_assists = sum(agg["assists"]) / total_g if agg["assists"] else 0
            avg_dur = sum(agg["durations"]) / total_g if agg["durations"] else 1800
            avg_cs = sum(agg["cs"]) / total_g if agg["cs"] else 0
            avg_gold = sum(agg["gold"]) / total_g if agg["gold"] else 0

            item_counter = collections.Counter(agg["items"])
            rune_counter = collections.Counter(agg["runes"])

            champ_stats_list.append(ChampionStats(
                champion_id=cid,
                games_played=total_g,
                wins=agg["wins"],
                losses=agg["losses"],
                avg_kda=self._extractor.compute_kda(int(avg_kills), int(avg_deaths), int(avg_assists)),
                avg_cs_per_min=avg_cs / (avg_dur / 60) if avg_dur > 0 else 0,
                avg_gold_per_min=avg_gold / (avg_dur / 60) if avg_dur > 0 else 0,
                most_common_items=[i for i, _ in item_counter.most_common(6)],
                most_common_runes=[r for r, _ in rune_counter.most_common(2)],
            ))

        champ_stats_list.sort(key=lambda c: c.games_played, reverse=True)
        total_all = total_wins + total_losses
        overall_wr = (total_wins / total_all * 100) if total_all > 0 else 0

        # Game phase ratings based on early CS deltas
        all_early_cs = []
        for agg in champion_agg.values():
            all_early_cs.extend(agg["cs_early"])
        early_rating = min(10, (sum(all_early_cs) / len(all_early_cs) / 0.8)) if all_early_cs else 5.0

        display_name = ""
        if self._crawler:
            cached = self._crawler.get_profile(puuid)
            if cached:
                display_name = cached.display_name

        return OpponentProfile(
            puuid=puuid,
            display_name=display_name or f"Player-{puuid[:8]}",
            overall_winrate=overall_wr,
            total_games_analyzed=total_all,
            champion_pool=champ_stats_list,
            preferred_roles=[r for r, _ in roles_counter.most_common(2)],
            avg_game_duration_pref=sum(durations) / len(durations) if durations else 1800,
            early_game_rating=min(10.0, early_rating),
            mid_game_rating=5.0,
            late_game_rating=5.0,
        )

    def get_opponent_profile(self, puuid: str) -> Optional[OpponentProfile]:
        return self._profiles.get(puuid)

    def get_all_profiles(self) -> Dict[str, OpponentProfile]:
        return dict(self._profiles)

    def export_stats(self) -> Dict[str, Any]:
        return {
            "analyzer_stats": self._stats,
            "profiles_cached": len(self._profiles),
        }



# ---------------------------------------------------------------------------
# Extended OpponentHistoryAnalyzer utilities
# ---------------------------------------------------------------------------

class ItemBuildAnalyzer:
    """Analyzes opponent item build patterns from match history."""

    def __init__(self):
        self._build_cache: Dict[int, Dict[str, int]] = {}

    def analyze_builds(self, champion_id: int, matches: List[Dict]) -> Dict[str, Any]:
        """Extract common item builds for a champion."""
        item_frequency: Dict[int, int] = collections.Counter()
        first_items: Dict[int, int] = collections.Counter()
        games_analyzed = 0

        for match in matches:
            items = match.get("items", [])
            valid_items = [i for i in items if i > 0]
            for item in valid_items:
                item_frequency[item] += 1
            if valid_items:
                first_items[valid_items[0]] += 1
            games_analyzed += 1

        core_items = [item for item, _ in item_frequency.most_common(6)]
        preferred_first = first_items.most_common(1)[0][0] if first_items else 0

        return {
            "champion_id": champion_id,
            "games_analyzed": games_analyzed,
            "core_items": core_items,
            "first_item": preferred_first,
            "item_frequency": dict(item_frequency.most_common(10)),
        }


class RuneAnalyzer:
    """Analyzes rune preferences from match data."""

    def __init__(self):
        self._rune_cache: Dict[int, Dict[int, int]] = {}

    def analyze_runes(self, champion_id: int, matches: List[Dict]) -> Dict[str, Any]:
        keystone_freq: Dict[int, int] = collections.Counter()
        primary_tree: Dict[int, int] = collections.Counter()
        secondary_tree: Dict[int, int] = collections.Counter()

        for match in matches:
            ks = match.get("rune_keystone", 0)
            pt = match.get("rune_primary", 0)
            st = match.get("rune_secondary", 0)
            if ks: keystone_freq[ks] += 1
            if pt: primary_tree[pt] += 1
            if st: secondary_tree[st] += 1

        return {
            "champion_id": champion_id,
            "top_keystone": keystone_freq.most_common(1)[0] if keystone_freq else (0, 0),
            "primary_trees": dict(primary_tree.most_common(3)),
            "secondary_trees": dict(secondary_tree.most_common(3)),
        }


class WinConditionAnalyzer:
    """Identifies opponent win conditions based on historical performance."""

    @staticmethod
    def identify_win_conditions(profile: OpponentProfile) -> List[Dict[str, Any]]:
        conditions = []

        # Check for snowball pattern
        if profile.early_game_rating >= 7.0:
            conditions.append({
                "type": "early_snowball",
                "confidence": min(0.9, profile.early_game_rating / 10),
                "counter": "Play safe early, ward aggressive jungle paths",
            })

        # Check for scaling pattern
        if profile.late_game_rating >= 7.0:
            conditions.append({
                "type": "late_scaling",
                "confidence": min(0.9, profile.late_game_rating / 10),
                "counter": "Force fights before 25 min, deny farm",
            })

        # Check for one-trick
        if profile.champion_pool and profile.champion_pool[0].games_played >= 10:
            top = profile.champion_pool[0]
            if top.winrate >= 60:
                conditions.append({
                    "type": "one_trick_carry",
                    "confidence": 0.85,
                    "counter": f"Ban champion {top.champion_id} or pick hard counter",
                })

        # Check for team-dependent player
        assists_heavy = False
        for champ in profile.champion_pool[:3]:
            if champ.avg_kda > 0:
                conditions.append({
                    "type": "team_player",
                    "confidence": 0.6,
                    "counter": "Isolate from team, pick off in rotations",
                })
                break

        return conditions
