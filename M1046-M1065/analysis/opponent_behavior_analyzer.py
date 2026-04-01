#!/usr/bin/env python3
"""
M1048: Opponent Behavior Analyzer
==================================
OperatorRL M1046-M1065 · 自部署 自环境反馈 自演化

Analyzes opponent historical match data to extract behavioral patterns,
tendencies, strengths, and weaknesses. Feeds the Strategy Engine (M1052)
with actionable intelligence.

Pattern: Read history/match_data_crawler.py (M1047) OpponentProfile
→ understand its data structures → implement behavioral analysis that
produces ThreatAssessment and PlaystyleProfile for each opponent.

Log-driven insight: 190 history_fetch events, 101 strategy_engine events
in test session → analyzer must process at ~2x fetch rate to avoid backlog.

Production Critique:
    1. User: Analysis must complete before first ban phase ends (~30s).
       With 5 opponents * ~100ms analysis each = 500ms total. Safe margin.
    2. System: Champion name resolution requires a static mapping (DDragon).
       If patch version mismatches, champion IDs may not resolve. Fallback:
       display raw champion ID with "unknown champion" label.
"""

import asyncio
import json
import math
import os
import sys
import time
from collections import defaultdict, Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, auto
from pathlib import Path
from typing import (Any, Dict, List, Optional, Set, Tuple, Union)

sys.path.insert(0, str(Path(__file__).parent.parent))
try:
    from evo_logging.evolution_logger import (
        EvolutionLogger, LogCategory, get_logger)
    from history.match_data_crawler import (
        OpponentProfile, MatchSummary, ChampionStats, RankedInfo)
except ImportError:
    pass


# ---------------------------------------------------------------------------
# Threat level classification
# ---------------------------------------------------------------------------

class ThreatLevel(Enum):
    """Threat assessment levels for opponents."""
    LOW = "low"           # Significantly below your rank, low win rate
    MODERATE = "moderate"  # Similar skill, average performance
    HIGH = "high"         # Above average, good win rate on current champ
    CRITICAL = "critical"  # Smurf-like stats, high mastery, on streak


class PlaystyleType(Enum):
    """Broad playstyle classifications."""
    AGGRESSIVE_LANER = "aggressive_laner"
    PASSIVE_FARMER = "passive_farmer"
    ROAMING_PLAYMAKER = "roaming_playmaker"
    TEAM_FIGHTER = "team_fighter"
    SPLIT_PUSHER = "split_pusher"
    VISION_CONTROLLER = "vision_controller"
    BURST_ASSASSIN = "burst_assassin"
    UTILITY_SUPPORT = "utility_support"
    TANK_ENGAGE = "tank_engage"
    BALANCED = "balanced"


TIER_VALUES = {
    'IRON': 0, 'BRONZE': 400, 'SILVER': 800, 'GOLD': 1200,
    'PLATINUM': 1600, 'EMERALD': 2000, 'DIAMOND': 2400,
    'MASTER': 2800, 'GRANDMASTER': 3200, 'CHALLENGER': 3600,
    'UNRANKED': 600,  # Assume low Silver for unranked
}
DIVISION_VALUES = {'I': 300, 'II': 200, 'III': 100, 'IV': 0, '': 0}


@dataclass
class LaneMatchup:
    """Analysis of an opponent's performance in a specific lane."""
    lane: str
    games: int
    win_rate: float
    avg_kda: float
    avg_cs_per_min: float
    avg_gold_diff_at_15: float = 0.0
    first_blood_rate: float = 0.0
    solo_kill_rate: float = 0.0
    preferred_champions: List[str] = field(default_factory=list)


@dataclass
class ThreatAssessment:
    """
    Comprehensive threat assessment for a single opponent.

    Consumed by the Strategy Engine to generate recommendations:
    - Ban suggestions (high threat + high presence = ban priority)
    - Lane assignments (avoid your weakest against their strongest)
    - In-game warnings (aggressive laner → play safe early)
    """
    puuid: str
    summoner_name: str
    threat_level: str
    threat_score: float        # 0-100, higher = more dangerous
    rank_estimate_mmr: int     # Estimated MMR from rank + performance
    current_form: str          # "hot_streak", "cold_streak", "neutral"
    current_form_score: float  # -1 (cold) to +1 (hot)
    primary_playstyle: str
    secondary_playstyle: Optional[str] = None
    lane_matchups: Dict[str, LaneMatchup] = field(default_factory=dict)
    champion_threat: Dict[str, float] = field(default_factory=dict)
    weaknesses: List[str] = field(default_factory=list)
    strengths: List[str] = field(default_factory=list)
    ban_priority: float = 0.0  # 0-10, higher = should ban
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = self.__dict__.copy()
        d['lane_matchups'] = {k: v.__dict__ for k, v in self.lane_matchups.items()}
        return d


class BehaviorAnalyzer:
    """
    Main analyzer: takes OpponentProfile → ThreatAssessment.

    Methods follow the analysis pipeline:
        1. estimate_mmr() → rank-based MMR estimate
        2. assess_current_form() → recent win/loss streak analysis
        3. classify_playstyle() → behavioral classification
        4. analyze_lane_performance() → per-lane breakdown
        5. identify_strengths_weaknesses() → SWOT-lite
        6. calculate_threat_score() → combined threat metric
        7. suggest_bans() → ban priority ranking
    """
    def __init__(self, our_mmr: int = 1200):
        self._our_mmr = our_mmr
        self._logger = get_logger()
        self._analysis_count = 0

    def analyze(self, profile: OpponentProfile) -> ThreatAssessment:
        """Full analysis pipeline for one opponent."""
        self._analysis_count += 1
        span_id = self._logger.start_span(f"analyze_{profile.puuid[:8]}")

        mmr = self._estimate_mmr(profile)
        form, form_score = self._assess_current_form(profile)
        primary, secondary = self._classify_playstyle(profile)
        lane_matchups = self._analyze_lane_performance(profile)
        strengths, weaknesses = self._identify_strengths_weaknesses(profile)
        champion_threat = self._assess_champion_threats(profile)
        threat_score = self._calculate_threat_score(
            mmr, form_score, profile, champion_threat)
        threat_level = self._score_to_level(threat_score)
        ban_priority = self._calculate_ban_priority(profile, champion_threat)

        notes = self._generate_notes(
            profile, threat_level, form, primary, weaknesses)

        assessment = ThreatAssessment(
            puuid=profile.puuid,
            summoner_name=profile.summoner_name,
            threat_level=threat_level.value,
            threat_score=round(threat_score, 1),
            rank_estimate_mmr=mmr,
            current_form=form,
            current_form_score=round(form_score, 2),
            primary_playstyle=primary.value,
            secondary_playstyle=secondary.value if secondary else None,
            lane_matchups=lane_matchups,
            champion_threat=champion_threat,
            weaknesses=weaknesses,
            strengths=strengths,
            ban_priority=round(ban_priority, 1),
            notes=notes,
        )

        self._logger.end_span(
            span_id, LogCategory.STRATEGY_ENGINE,
            f"Analyzed {profile.summoner_name}: "
            f"threat={threat_level.value} score={threat_score:.1f}",
            data={'mmr': mmr, 'form': form,
                  'playstyle': primary.value})
        return assessment

    def analyze_team(
        self, profiles: Dict[str, OpponentProfile]
    ) -> Dict[str, ThreatAssessment]:
        """Analyze entire enemy team."""
        return {puuid: self.analyze(p) for puuid, p in profiles.items()}

    def _estimate_mmr(self, profile: OpponentProfile) -> int:
        """Estimate MMR from ranked info and recent performance."""
        base_mmr = TIER_VALUES.get('UNRANKED', 600)
        if profile.ranked_solo:
            tier_val = TIER_VALUES.get(profile.ranked_solo.tier, 600)
            div_val = DIVISION_VALUES.get(profile.ranked_solo.division, 0)
            lp_bonus = profile.ranked_solo.lp * 0.5
            base_mmr = int(tier_val + div_val + lp_bonus)
            # Adjust by win rate (>55% → climbing, <45% → dropping)
            wr = profile.ranked_solo.win_rate
            if wr > 50:
                base_mmr += int((wr - 50) * 10)
            elif wr < 50:
                base_mmr -= int((50 - wr) * 8)
        # Adjust by recent performance
        if profile.recent_matches:
            recent = profile.recent_matches[:10]
            recent_wr = sum(1 for m in recent if m.win) / len(recent) * 100
            if recent_wr > 70:
                base_mmr += 150  # Possible smurf or hot streak
            elif recent_wr < 30:
                base_mmr -= 100  # Cold streak or tilted
        return max(0, base_mmr)

    def _assess_current_form(
        self, profile: OpponentProfile
    ) -> Tuple[str, float]:
        """Assess current form from recent match results."""
        matches = profile.recent_matches[:10]
        if not matches:
            return "neutral", 0.0
        # Weighted recent results (most recent = highest weight)
        score = 0.0
        for i, m in enumerate(matches):
            weight = 1.0 / (i + 1)  # Decay: 1, 0.5, 0.33, 0.25...
            if m.win:
                score += weight * 1.0
            else:
                score -= weight * 0.8
            # KDA bonus/penalty
            if m.kda > 4:
                score += weight * 0.3
            elif m.kda < 1:
                score -= weight * 0.2
        # Normalize to [-1, 1]
        max_possible = sum(1.3 / (i + 1) for i in range(len(matches)))
        normalized = score / max(max_possible, 0.001)
        normalized = max(-1.0, min(1.0, normalized))
        if normalized > 0.3:
            return "hot_streak", normalized
        elif normalized < -0.3:
            return "cold_streak", normalized
        return "neutral", normalized

    def _classify_playstyle(
        self, profile: OpponentProfile
    ) -> Tuple[PlaystyleType, Optional[PlaystyleType]]:
        """Classify playstyle from match statistics."""
        matches = profile.recent_matches[:20]
        if not matches:
            return PlaystyleType.BALANCED, None
        n = len(matches)
        avg_kills = sum(m.kills for m in matches) / n
        avg_deaths = sum(m.deaths for m in matches) / n
        avg_assists = sum(m.assists for m in matches) / n
        avg_cs_min = sum(m.cs_per_min for m in matches) / n
        avg_vision = sum(m.vision_score for m in matches) / n
        avg_dmg = sum(m.damage_dealt for m in matches) / n
        avg_dmg_taken = sum(m.damage_taken for m in matches) / n
        avg_duration = sum(m.game_duration_sec for m in matches) / n

        scores: Dict[PlaystyleType, float] = defaultdict(float)
        # Aggressive laner: high kills, high early damage
        if avg_kills > 7:
            scores[PlaystyleType.AGGRESSIVE_LANER] += 2.0
        if avg_deaths > 5:
            scores[PlaystyleType.AGGRESSIVE_LANER] += 0.5
        # Passive farmer: high CS, low kills, low deaths
        if avg_cs_min > 7.5:
            scores[PlaystyleType.PASSIVE_FARMER] += 2.0
        if avg_kills < 4 and avg_deaths < 4:
            scores[PlaystyleType.PASSIVE_FARMER] += 1.0
        # Team fighter: high assists relative to kills
        if avg_assists > avg_kills * 1.5:
            scores[PlaystyleType.TEAM_FIGHTER] += 2.0
        if avg_assists > 10:
            scores[PlaystyleType.TEAM_FIGHTER] += 1.0
        # Vision controller
        if avg_vision > 35:
            scores[PlaystyleType.VISION_CONTROLLER] += 2.5
        elif avg_vision > 25:
            scores[PlaystyleType.VISION_CONTROLLER] += 1.0
        # Burst assassin: high damage, high kills, moderate deaths
        if avg_dmg > 20000 and avg_kills > 8:
            scores[PlaystyleType.BURST_ASSASSIN] += 2.0
        # Tank/engage: high damage taken
        if avg_dmg_taken > 25000:
            scores[PlaystyleType.TANK_ENGAGE] += 2.0

        if not scores:
            return PlaystyleType.BALANCED, None

        sorted_styles = sorted(scores.items(), key=lambda x: -x[1])
        primary = sorted_styles[0][0]
        secondary = sorted_styles[1][0] if len(sorted_styles) > 1 and sorted_styles[1][1] > 1.0 else None
        return primary, secondary

    def _analyze_lane_performance(
        self, profile: OpponentProfile
    ) -> Dict[str, LaneMatchup]:
        """Break down performance by lane."""
        by_lane: Dict[str, List[MatchSummary]] = defaultdict(list)
        for m in profile.recent_matches:
            lane = m.lane if m.lane and m.lane != 'NONE' else 'UNKNOWN'
            by_lane[lane].append(m)

        result = {}
        for lane, matches in by_lane.items():
            n = len(matches)
            if n < 2:
                continue
            wins = sum(1 for m in matches if m.win)
            kdas = [m.kda for m in matches]
            cs_mins = [m.cs_per_min for m in matches]
            # Champion frequency in this lane
            champ_counts = Counter(m.champion_name for m in matches)
            top_champs = [c for c, _ in champ_counts.most_common(3)]

            result[lane] = LaneMatchup(
                lane=lane,
                games=n,
                win_rate=round(wins / n * 100, 1),
                avg_kda=round(sum(kdas) / n, 2),
                avg_cs_per_min=round(sum(cs_mins) / n, 1),
                preferred_champions=top_champs,
            )
        return result

    def _assess_champion_threats(
        self, profile: OpponentProfile
    ) -> Dict[str, float]:
        """Score each champion the opponent plays by threat level (0-10)."""
        threats = {}
        for name, stats in profile.champion_stats.items():
            score = 0.0
            # Win rate factor (max 4 points)
            score += min(stats.win_rate / 25, 4.0)
            # Games played factor (max 2 points)
            score += min(stats.games_played / 10, 2.0)
            # KDA factor (max 2 points)
            score += min(stats.kda / 3, 2.0)
            # Mastery factor (max 2 points)
            score += min(stats.mastery_level / 4, 2.0)
            threats[name] = round(min(score, 10.0), 1)
        return dict(sorted(threats.items(), key=lambda x: -x[1]))

    def _identify_strengths_weaknesses(
        self, profile: OpponentProfile
    ) -> Tuple[List[str], List[str]]:
        """Identify actionable strengths and weaknesses."""
        strengths = []
        weaknesses = []
        matches = profile.recent_matches[:10]
        if not matches:
            return strengths, weaknesses
        n = len(matches)
        avg_kda = sum(m.kda for m in matches) / n
        avg_cs_min = sum(m.cs_per_min for m in matches) / n
        avg_vision = sum(m.vision_score for m in matches) / n
        avg_deaths = sum(m.deaths for m in matches) / n
        wr = sum(1 for m in matches if m.win) / n * 100

        if avg_kda > 4:
            strengths.append(f"High KDA ({avg_kda:.1f}): mechanically strong")
        if avg_cs_min > 8:
            strengths.append(f"Excellent CS ({avg_cs_min:.1f}/min): strong laning")
        if avg_vision > 35:
            strengths.append("Vision-focused: hard to gank")
        if wr > 65:
            strengths.append(f"High win rate ({wr:.0f}%): may be smurfing")
        if profile.consistency_score > 0.7:
            strengths.append("Very consistent performance")

        if avg_deaths > 6:
            weaknesses.append(f"Dies frequently ({avg_deaths:.1f}/game): punish aggression")
        if avg_cs_min < 5:
            weaknesses.append(f"Low CS ({avg_cs_min:.1f}/min): weak laning phase")
        if avg_vision < 15:
            weaknesses.append("Poor vision: exploit blind spots")
        if profile.tilt_indicator > 0.6:
            weaknesses.append("Currently on losing streak: may be tilted")
        if wr < 40:
            weaknesses.append(f"Low recent win rate ({wr:.0f}%): struggling")
        if profile.consistency_score < 0.3:
            weaknesses.append("Inconsistent: feast-or-famine player")

        return strengths, weaknesses

    def _calculate_threat_score(
        self, mmr: int, form_score: float,
        profile: OpponentProfile,
        champion_threat: Dict[str, float]
    ) -> float:
        """
        Combined threat score (0-100).

        Formula:
            base = MMR difference factor (0-40)
            + form factor (0-20)
            + champion mastery factor (0-20)
            + consistency factor (0-10)
            + special flags (0-10)
        """
        # MMR difference: positive = they're higher
        mmr_diff = mmr - self._our_mmr
        mmr_factor = 20 + min(max(mmr_diff / 50, -20), 20)
        # Form factor
        form_factor = 10 + form_score * 10
        # Champion mastery: average of top 3 champion threats
        top_threats = sorted(champion_threat.values(), reverse=True)[:3]
        champ_factor = (sum(top_threats) / max(len(top_threats), 1)) * 2
        # Consistency
        consistency_factor = profile.consistency_score * 10
        # Special: smurf detection
        special = 0.0
        if profile.summoner_level and profile.summoner_level < 50 and mmr > 1600:
            special += 5.0  # Low level + high rank = possible smurf
        if profile.ranked_solo and profile.ranked_solo.win_rate > 65:
            special += 3.0

        total = mmr_factor + form_factor + champ_factor + consistency_factor + special
        return max(0, min(100, total))

    def _score_to_level(self, score: float) -> ThreatLevel:
        if score >= 75:
            return ThreatLevel.CRITICAL
        elif score >= 55:
            return ThreatLevel.HIGH
        elif score >= 35:
            return ThreatLevel.MODERATE
        return ThreatLevel.LOW

    def _calculate_ban_priority(
        self, profile: OpponentProfile,
        champion_threat: Dict[str, float]
    ) -> float:
        """Ban priority (0-10) based on champion pool and threat."""
        if not champion_threat:
            return 0.0
        top_champ_threat = max(champion_threat.values())
        pool_depth = len([t for t in champion_threat.values() if t > 5])
        # Narrow pool + high threat on #1 champ = high ban priority
        if pool_depth <= 2 and top_champ_threat > 7:
            return min(top_champ_threat + 2, 10.0)
        return min(top_champ_threat, 10.0)

    def _generate_notes(
        self, profile: OpponentProfile, threat: ThreatLevel,
        form: str, playstyle: PlaystyleType,
        weaknesses: List[str]
    ) -> List[str]:
        """Generate human-readable tactical notes."""
        notes = []
        name = profile.summoner_name or "Opponent"
        if threat == ThreatLevel.CRITICAL:
            notes.append(f"⚠ {name} is a critical threat — respect in lane")
        if form == "hot_streak":
            notes.append(f"🔥 {name} is on a winning streak, playing confidently")
        elif form == "cold_streak":
            notes.append(f"❄ {name} is on a losing streak, may be tilted")
        if playstyle == PlaystyleType.AGGRESSIVE_LANER:
            notes.append(f"⚔ {name} plays aggressively — ward river, track jungler")
        elif playstyle == PlaystyleType.PASSIVE_FARMER:
            notes.append(f"🌾 {name} farms safely — apply early pressure")
        if weaknesses:
            notes.append(f"💡 Key weakness: {weaknesses[0]}")
        return notes[:5]
