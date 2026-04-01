#!/usr/bin/env python3
"""
M1054: Champ Select Advisor
============================
OperatorRL M1046-M1065 · 自部署 自环境反馈 自演化

Provides real-time ban/pick advice during champion select phase.
Integrates opponent historical data + meta analysis + personal mastery.
"""

import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).parent.parent))
try:
    from evo_logging.evolution_logger import get_logger, LogCategory
except ImportError:
    pass


@dataclass
class BanRecommendation:
    champion_name: str
    champion_id: int
    priority_score: float   # 0-10
    reason: str
    threat_owner: Optional[str] = None  # Which opponent plays this champ
    owner_win_rate: float = 0.0
    owner_games: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return self.__dict__


@dataclass
class PickRecommendation:
    champion_name: str
    champion_id: int
    role: str
    synergy_score: float     # 0-10 synergy with team
    counter_score: float     # 0-10 how well it counters enemy
    comfort_score: float     # 0-10 player's mastery
    overall_score: float = 0.0
    reasoning: str = ""

    def __post_init__(self):
        if self.overall_score == 0.0:
            self.overall_score = round(
                self.synergy_score * 0.2 + self.counter_score * 0.4
                + self.comfort_score * 0.4, 1)

    def to_dict(self) -> Dict[str, Any]:
        return self.__dict__


class ChampSelectAdvisor:
    """
    Ban/pick recommendation engine for champion select.

    Pipeline:
        1. On ChampSelect phase enter → fetch enemy profiles (M1047)
        2. Analyze threats (M1048) → rank ban targets
        3. After bans → suggest picks based on:
           - Counter-matchup data
           - Team synergy (comp analysis)
           - Player's champion mastery
        4. As picks progress → update recommendations dynamically

    Timing constraint: Ban phase = ~30s, Pick phase = ~30s each.
    All recommendations must be ready within 5s of phase start.
    """
    def __init__(self):
        self._logger = get_logger()
        self._current_bans: List[int] = []
        self._current_picks_ally: List[int] = []
        self._current_picks_enemy: List[int] = []
        self._ban_recommendations: List[BanRecommendation] = []
        self._pick_recommendations: List[PickRecommendation] = []

    def generate_ban_recommendations(
        self,
        threat_assessments: Dict[str, Any],
        already_banned: List[int] = None,
    ) -> List[BanRecommendation]:
        """Generate prioritized ban recommendations."""
        if already_banned is None:
            already_banned = []
        candidates = []
        for puuid, assessment in threat_assessments.items():
            if not isinstance(assessment, dict):
                continue
            summoner = assessment.get('summoner_name', '')
            for champ, threat_score in assessment.get('champion_threat', {}).items():
                if threat_score < 3.0:
                    continue
                candidates.append(BanRecommendation(
                    champion_name=champ,
                    champion_id=0,  # Resolve from DDragon
                    priority_score=round(threat_score, 1),
                    reason=(f"{summoner} has {threat_score:.1f}/10 threat on {champ}. "
                            f"Form: {assessment.get('current_form', 'neutral')}"),
                    threat_owner=summoner,
                    owner_win_rate=0.0,
                    owner_games=0,
                ))
        # Sort by priority, deduplicate by champion
        candidates.sort(key=lambda c: -c.priority_score)
        seen = set()
        result = []
        for c in candidates:
            if c.champion_name not in seen:
                seen.add(c.champion_name)
                result.append(c)
        self._ban_recommendations = result[:5]
        self._logger.info(
            LogCategory.STRATEGY_ENGINE,
            f"Generated {len(result)} ban recommendations",
            data={'top_ban': result[0].champion_name if result else 'none'})
        return self._ban_recommendations

    def generate_pick_recommendations(
        self,
        assigned_role: str,
        enemy_picks: List[str],
        ally_picks: List[str],
        player_mastery: Dict[str, float] = None,
    ) -> List[PickRecommendation]:
        """Generate pick suggestions for the player's assigned role."""
        if player_mastery is None:
            player_mastery = {}
        # Simple counter-logic: this would use a full counter matrix in production
        # For now, use player mastery as primary signal
        recs = []
        for champ, mastery in sorted(
            player_mastery.items(), key=lambda x: -x[1]
        )[:10]:
            if champ in ally_picks:
                continue
            counter = 5.0  # Default neutral counter score
            synergy = 5.0  # Default neutral synergy
            recs.append(PickRecommendation(
                champion_name=champ,
                champion_id=0,
                role=assigned_role,
                synergy_score=synergy,
                counter_score=counter,
                comfort_score=round(mastery, 1),
                reasoning=f"High mastery ({mastery:.1f}) on {champ}",
            ))
        recs.sort(key=lambda r: -r.overall_score)
        self._pick_recommendations = recs[:5]
        return self._pick_recommendations

    def get_current_recommendations(self) -> Dict[str, Any]:
        return {
            'bans': [b.to_dict() for b in self._ban_recommendations],
            'picks': [p.to_dict() for p in self._pick_recommendations],
        }


# ---------------------------------------------------------------------------
# Counter-Pick Knowledge Base
# ---------------------------------------------------------------------------

COUNTER_PICK_DB: Dict[str, Dict[str, Any]] = {
    "Yasuo": {
        "counters": ["Malphite", "Rammus", "Renekton", "Pantheon", "Annie"],
        "countered_by": ["Zed", "Akali", "Katarina"],
        "synergies": ["Malphite", "Diana", "Gragas", "Alistar", "Yone"],
        "tips": "Yasuo is vulnerable pre-level-3. Windwall has 30s cooldown.",
    },
    "Zed": {
        "counters": ["Lissandra", "Kayle", "Malzahar", "Zhonyas rush"],
        "countered_by": ["Yasuo", "Akali"],
        "synergies": ["Orianna", "Lulu"],
        "tips": "Zed ult returns him to shadow position. Zone the shadow.",
    },
    "Jinx": {
        "counters": ["Draven", "Lucian", "Tristana", "Caitlyn"],
        "countered_by": ["Kog'Maw", "Vayne"],
        "synergies": ["Thresh", "Lulu", "Nami", "Yuumi"],
        "tips": "Jinx has no escape. Hard engage supports dominate her lane.",
    },
    "Thresh": {
        "counters": ["Morgana", "Sivir", "Ezreal"],
        "countered_by": ["Nautilus", "Leona"],
        "synergies": ["Draven", "Lucian", "Kalista", "Jinx"],
        "tips": "Stand behind minions to block hook. Flay has limited range.",
    },
    "LeeSin": {
        "counters": ["Rammus", "Amumu", "Udyr", "Volibear"],
        "countered_by": ["Elise", "Nidalee"],
        "synergies": ["Yasuo", "Orianna"],
        "tips": "Lee Sin falls off hard after 25 minutes. Scale and outgroup.",
    },
    "Ahri": {
        "counters": ["Kassadin", "Fizz", "Zed", "LeBlanc"],
        "countered_by": ["Xerath", "Lux", "Viktor"],
        "synergies": ["Jarvan IV", "Amumu"],
        "tips": "Ahri's charm is her only hard CC. Side-step and trade after.",
    },
    "Darius": {
        "counters": ["Quinn", "Vayne", "Teemo", "Kennen", "Jayce"],
        "countered_by": ["Mordekaiser", "Illaoi"],
        "synergies": ["Yuumi", "Lulu"],
        "tips": "Don't take extended trades. Darius wins 5-stack all-ins.",
    },
}


class CounterPickEngine:
    """
    Recommends counter-picks based on opponent team composition.

    Uses the COUNTER_PICK_DB knowledge base plus historical winrate
    data from the player's own match history to suggest champions
    the player is both proficient with AND effective against opponents.

    Production critique:
        1. User: Recommendations balance counter-pick strength with
           player proficiency. A perfect counter the player can't play
           is worse than a neutral pick they main.
        2. System: We weight: 40% counter-pick effectiveness,
           40% player proficiency, 20% team synergy.
    """
    COUNTER_WEIGHT = 0.40
    PROFICIENCY_WEIGHT = 0.40
    SYNERGY_WEIGHT = 0.20

    def __init__(self):
        self._player_champion_stats: Dict[str, Dict] = {}
        self._my_team_champions: List[str] = []
        self._enemy_champions: List[str] = []

    def set_player_stats(self, stats: Dict[str, Dict]) -> None:
        """Set player's champion proficiency data."""
        self._player_champion_stats = stats

    def set_teams(
        self, my_team: List[str], enemy_team: List[str]
    ) -> None:
        self._my_team_champions = my_team
        self._enemy_champions = enemy_team

    def get_counter_score(self, champion: str) -> float:
        """How well does this champion counter the enemy team?"""
        score = 0.0
        info = COUNTER_PICK_DB.get(champion, {})
        counters = info.get("counters", [])
        for enemy in self._enemy_champions:
            enemy_info = COUNTER_PICK_DB.get(enemy, {})
            enemy_countered_by = enemy_info.get("countered_by", [])
            if champion in counters or champion in enemy_countered_by:
                score += 0.3
            if enemy in info.get("countered_by", []):
                score -= 0.2
        return min(1.0, max(0.0, score))

    def get_proficiency_score(self, champion: str) -> float:
        """How proficient is the player with this champion?"""
        stats = self._player_champion_stats.get(champion, {})
        games = stats.get("games_played", 0)
        winrate = stats.get("winrate", 50.0)
        if games == 0:
            return 0.0
        # Normalize: 50+ games = 1.0 proficiency, scaled by winrate
        game_factor = min(1.0, games / 50.0)
        wr_factor = (winrate - 30) / 40  # 30% = 0, 70% = 1
        wr_factor = max(0.0, min(1.0, wr_factor))
        return game_factor * 0.5 + wr_factor * 0.5

    def get_synergy_score(self, champion: str) -> float:
        """How well does this champion synergize with our team?"""
        info = COUNTER_PICK_DB.get(champion, {})
        synergies = info.get("synergies", [])
        score = 0.0
        for ally in self._my_team_champions:
            if ally in synergies:
                score += 0.3
            ally_info = COUNTER_PICK_DB.get(ally, {})
            if champion in ally_info.get("synergies", []):
                score += 0.2
        return min(1.0, score)

    def recommend(
        self, available_champions: Optional[List[str]] = None,
        top_n: int = 5
    ) -> List[Dict[str, Any]]:
        """
        Generate ranked champion recommendations.

        Returns list of {champion, score, counter_score,
        proficiency_score, synergy_score, reasoning}.
        """
        if available_champions is None:
            available_champions = list(COUNTER_PICK_DB.keys())

        # Exclude already-picked champions
        picked = set(self._my_team_champions + self._enemy_champions)
        candidates = [c for c in available_champions if c not in picked]

        scored = []
        for champ in candidates:
            counter = self.get_counter_score(champ)
            proficiency = self.get_proficiency_score(champ)
            synergy = self.get_synergy_score(champ)
            total = (
                counter * self.COUNTER_WEIGHT
                + proficiency * self.PROFICIENCY_WEIGHT
                + synergy * self.SYNERGY_WEIGHT
            )
            reasons = []
            if counter > 0.3:
                reasons.append("Strong counter-pick")
            if proficiency > 0.5:
                reasons.append("High proficiency")
            if synergy > 0.3:
                reasons.append("Good team synergy")
            tips = COUNTER_PICK_DB.get(champ, {}).get("tips", "")
            scored.append({
                'champion': champ,
                'total_score': round(total, 3),
                'counter_score': round(counter, 3),
                'proficiency_score': round(proficiency, 3),
                'synergy_score': round(synergy, 3),
                'reasoning': "; ".join(reasons) if reasons else "Balanced pick",
                'tips': tips,
            })

        scored.sort(key=lambda x: x['total_score'], reverse=True)
        return scored[:top_n]


class BanRecommendationEngine:
    """
    Recommends champion bans based on opponent history and meta.

    Factors:
        - Opponent's most played / highest winrate champions
        - Current meta ban priorities
        - Team composition vulnerability
    """
    def __init__(self):
        self._opponent_profiles: List[Dict] = []
        self._meta_ban_priorities: List[str] = []

    def set_opponents(self, profiles: List[Dict]) -> None:
        self._opponent_profiles = profiles

    def set_meta_priorities(self, champions: List[str]) -> None:
        self._meta_ban_priorities = champions

    def recommend_bans(self, num_bans: int = 5) -> List[Dict]:
        """Generate ban recommendations."""
        candidates: Dict[str, float] = {}

        # From opponent profiles: ban their mains
        for profile in self._opponent_profiles:
            main_champs = profile.get('main_champions', [])
            for i, champ in enumerate(main_champs[:3]):
                name = champ.get('champion_name', '')
                if name:
                    wr = champ.get('winrate', 50)
                    games = champ.get('games_played', 0)
                    # Higher score for first main with high winrate
                    score = (1.0 - i * 0.2) * (wr / 100) * min(1.0, games / 20)
                    candidates[name] = candidates.get(name, 0) + score

        # From meta priorities
        for i, champ in enumerate(self._meta_ban_priorities[:10]):
            meta_score = (1.0 - i * 0.08) * 0.5
            candidates[champ] = candidates.get(champ, 0) + meta_score

        # Sort and return
        sorted_bans = sorted(
            candidates.items(), key=lambda x: x[1], reverse=True)
        return [
            {'champion': name, 'ban_score': round(score, 3),
             'reason': 'opponent_main' if score > 0.5 else 'meta_priority'}
            for name, score in sorted_bans[:num_bans]
        ]


class DraftPhaseStateMachine:
    """
    Tracks the champion select draft state machine.

    Blue side: Ban1, Ban2, Ban3, Pick1, Pick2, Pick3 ...
    Red side:  Ban1, Ban2, Ban3, Pick1, Pick2, Pick3 ...

    Provides real-time recommendations at each phase.
    """
    def __init__(self, side: str = "blue"):
        self._side = side
        self._phase_index = 0
        self._my_bans: List[str] = []
        self._enemy_bans: List[str] = []
        self._my_picks: List[str] = []
        self._enemy_picks: List[str] = []
        self._counter_engine = CounterPickEngine()
        self._ban_engine = BanRecommendationEngine()

    def process_action(
        self, action_type: str, champion: str, is_ally: bool
    ) -> None:
        """Process a draft action."""
        if action_type == 'ban':
            if is_ally:
                self._my_bans.append(champion)
            else:
                self._enemy_bans.append(champion)
        elif action_type == 'pick':
            if is_ally:
                self._my_picks.append(champion)
            else:
                self._enemy_picks.append(champion)
        self._phase_index += 1
        # Update counter engine
        self._counter_engine.set_teams(
            self._my_picks, self._enemy_picks)

    def get_current_recommendation(self) -> Dict:
        """Get recommendation for current draft phase."""
        all_banned = set(self._my_bans + self._enemy_bans)
        all_picked = set(self._my_picks + self._enemy_picks)
        unavailable = all_banned | all_picked

        if self._phase_index < 6:  # Ban phase
            return {
                'phase': 'ban',
                'phase_index': self._phase_index,
                'recommendations': self._ban_engine.recommend_bans(),
            }
        else:  # Pick phase
            available = [c for c in COUNTER_PICK_DB
                        if c not in unavailable]
            return {
                'phase': 'pick',
                'phase_index': self._phase_index,
                'recommendations': self._counter_engine.recommend(
                    available_champions=available),
            }

    def get_draft_summary(self) -> Dict:
        return {
            'side': self._side,
            'phase_index': self._phase_index,
            'my_bans': self._my_bans,
            'enemy_bans': self._enemy_bans,
            'my_picks': self._my_picks,
            'enemy_picks': self._enemy_picks,
        }
