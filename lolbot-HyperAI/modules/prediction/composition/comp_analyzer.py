"""
modules/prediction/composition/comp_analyzer.py — Team composition analysis.
==============================================================================
Claude19 · Feeds into PredictionComponent for composition-aware win prediction

Classifies team compositions by archetype (teamfight, pick, siege, split,
poke) and evaluates relative win condition matchups. This allows prediction
to adjust win probability based on team comp suitability to current game state.

Apollo analogy: prediction/evaluator/vehicle_on_lane_evaluator.cc evaluates
how well the vehicle matches the current road — we evaluate how well
the team comp matches the current game phase.

File location: lolbot-HyperAI/modules/prediction/composition/comp_analyzer.py
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class CompArchetype(Enum):
    """Team composition archetype."""
    TEAMFIGHT = auto()   # 5v5 specialists (Malphite, Orianna, etc.)
    PICK = auto()        # Catch/assassination (Blitzcrank, Zed, etc.)
    SIEGE = auto()       # Poke and take towers (Ziggs, Jayce, etc.)
    SPLIT_PUSH = auto()  # 1-3-1 or 4-1 (Fiora, Tryndamere, etc.)
    POKE = auto()        # Long range poke (Xerath, Lux, etc.)
    DIVE = auto()        # Hard engage onto backline (Diana, Jarvan)
    PROTECT = auto()     # Protect the carry (Lulu, Kog'Maw)
    BALANCED = auto()    # No dominant archetype


# Champion → archetype affinity scores (0.0 – 1.0)
# This is a representative sample; production would load from config
_CHAMPION_ARCHETYPES: Dict[str, Dict[str, float]] = {
    # Teamfight specialists
    "Malphite": {"teamfight": 0.9, "dive": 0.6},
    "Orianna": {"teamfight": 0.9, "poke": 0.5},
    "Amumu": {"teamfight": 0.9},
    "Kennen": {"teamfight": 0.8, "split": 0.4},
    "MissFortune": {"teamfight": 0.8},
    "Zyra": {"teamfight": 0.6, "poke": 0.5},
    "Wukong": {"teamfight": 0.8, "dive": 0.5},
    # Pick specialists
    "Blitzcrank": {"pick": 0.9},
    "Thresh": {"pick": 0.7, "teamfight": 0.4},
    "Zed": {"pick": 0.8, "split": 0.5},
    "LeBlanc": {"pick": 0.8},
    "Pyke": {"pick": 0.9},
    "Ahri": {"pick": 0.7, "poke": 0.4},
    # Siege / Poke
    "Ziggs": {"siege": 0.9, "poke": 0.7},
    "Jayce": {"siege": 0.7, "poke": 0.6},
    "Xerath": {"poke": 0.9, "siege": 0.5},
    "Lux": {"poke": 0.7, "pick": 0.3},
    "Varus": {"poke": 0.6, "siege": 0.5},
    "Caitlyn": {"siege": 0.7},
    # Split push
    "Fiora": {"split": 0.9},
    "Tryndamere": {"split": 0.9},
    "Jax": {"split": 0.8, "teamfight": 0.3},
    "Camille": {"split": 0.7, "pick": 0.5},
    "Sion": {"split": 0.5, "teamfight": 0.6},
    # Dive
    "Diana": {"dive": 0.9, "teamfight": 0.6},
    "JarvanIV": {"dive": 0.8, "teamfight": 0.6},
    "Leona": {"dive": 0.8},
    "Nautilus": {"dive": 0.7, "teamfight": 0.5},
    "Vi": {"dive": 0.8, "pick": 0.5},
    # Protect the carry
    "Lulu": {"protect": 0.9},
    "Janna": {"protect": 0.8, "poke": 0.3},
    "KogMaw": {"protect": 0.7, "siege": 0.4},
    "Jinx": {"protect": 0.6, "teamfight": 0.5},
    "Yuumi": {"protect": 0.9},
    "Soraka": {"protect": 0.8},
}

# Archetype name → key
_ARCHETYPE_KEYS = {
    "teamfight": CompArchetype.TEAMFIGHT,
    "pick": CompArchetype.PICK,
    "siege": CompArchetype.SIEGE,
    "split": CompArchetype.SPLIT_PUSH,
    "poke": CompArchetype.POKE,
    "dive": CompArchetype.DIVE,
    "protect": CompArchetype.PROTECT,
}

# Phase suitability: how well each archetype scales with game phase
# Positive = this archetype is favored in this phase
_PHASE_SUITABILITY = {
    CompArchetype.TEAMFIGHT: {"EARLY": -0.1, "MID": 0.1, "LATE": 0.15},
    CompArchetype.PICK: {"EARLY": 0.1, "MID": 0.15, "LATE": -0.05},
    CompArchetype.SIEGE: {"EARLY": -0.1, "MID": 0.15, "LATE": 0.1},
    CompArchetype.SPLIT_PUSH: {"EARLY": -0.15, "MID": 0.1, "LATE": 0.2},
    CompArchetype.POKE: {"EARLY": 0.05, "MID": 0.1, "LATE": -0.05},
    CompArchetype.DIVE: {"EARLY": 0.15, "MID": 0.1, "LATE": -0.1},
    CompArchetype.PROTECT: {"EARLY": -0.15, "MID": 0.0, "LATE": 0.2},
    CompArchetype.BALANCED: {"EARLY": 0.0, "MID": 0.0, "LATE": 0.0},
}


@dataclass
class CompProfile:
    """Profile of a team composition."""
    champions: List[str]
    archetype_scores: Dict[str, float] = field(default_factory=dict)
    primary_archetype: CompArchetype = CompArchetype.BALANCED
    secondary_archetype: Optional[CompArchetype] = None
    phase_advantages: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "champions": self.champions,
            "primary": self.primary_archetype.name,
            "secondary": (
                self.secondary_archetype.name
                if self.secondary_archetype else None
            ),
            "archetype_scores": {
                k: round(v, 3) for k, v in self.archetype_scores.items()
            },
            "phase_advantages": {
                k: round(v, 3) for k, v in self.phase_advantages.items()
            },
        }


@dataclass
class CompAnalysisReport:
    """Comparison of two team compositions."""
    blue_profile: CompProfile
    red_profile: CompProfile
    current_phase: str = "EARLY"
    blue_phase_advantage: float = 0.0
    archetype_matchup: str = ""
    win_condition_blue: str = ""
    win_condition_red: str = ""
    comp_adjustment: float = 0.0  # +/- adjustment to win probability

    def to_dict(self) -> Dict[str, Any]:
        return {
            "blue": self.blue_profile.to_dict(),
            "red": self.red_profile.to_dict(),
            "current_phase": self.current_phase,
            "blue_phase_advantage": round(self.blue_phase_advantage, 3),
            "matchup": self.archetype_matchup,
            "win_condition_blue": self.win_condition_blue,
            "win_condition_red": self.win_condition_red,
            "comp_adjustment": round(self.comp_adjustment, 4),
        }


class CompAnalyzer:
    """Analyzes team compositions and their phase-dependent strengths.

    Usage::
        analyzer = CompAnalyzer()
        report = analyzer.analyze(
            blue_champions=["Malphite", "Amumu", "Orianna", "MissFortune", "Leona"],
            red_champions=["Fiora", "LeeSin", "Zed", "Caitlyn", "Thresh"],
            current_phase="MID",
        )
        print(f"Comp adj: {report.comp_adjustment:+.3f}")
    """

    def __init__(self) -> None:
        self._analysis_count: int = 0
        self._cached_blue: Optional[CompProfile] = None
        self._cached_red: Optional[CompProfile] = None
        self._last_blue_champs: Optional[Tuple[str, ...]] = None
        self._last_red_champs: Optional[Tuple[str, ...]] = None

    def analyze(
        self,
        blue_champions: List[str],
        red_champions: List[str],
        current_phase: str = "EARLY",
    ) -> CompAnalysisReport:
        """Analyze both team compositions and produce matchup report."""
        self._analysis_count += 1

        # Cache profiles if champs haven't changed
        blue_key = tuple(sorted(blue_champions))
        red_key = tuple(sorted(red_champions))

        if blue_key != self._last_blue_champs or self._cached_blue is None:
            self._cached_blue = self._profile_team(blue_champions)
            self._last_blue_champs = blue_key
        if red_key != self._last_red_champs or self._cached_red is None:
            self._cached_red = self._profile_team(red_champions)
            self._last_red_champs = red_key

        blue = self._cached_blue
        red = self._cached_red

        # Phase advantage comparison
        blue_pa = blue.phase_advantages.get(current_phase, 0.0)
        red_pa = red.phase_advantages.get(current_phase, 0.0)
        phase_advantage = blue_pa - red_pa

        # Archetype matchup
        matchup = self._describe_matchup(blue.primary_archetype, red.primary_archetype)

        # Win conditions
        wc_blue = self._win_condition(blue.primary_archetype)
        wc_red = self._win_condition(red.primary_archetype)

        # Comp adjustment to win probability
        comp_adjustment = phase_advantage * 0.5  # Scale to ±5%

        return CompAnalysisReport(
            blue_profile=blue,
            red_profile=red,
            current_phase=current_phase,
            blue_phase_advantage=phase_advantage,
            archetype_matchup=matchup,
            win_condition_blue=wc_blue,
            win_condition_red=wc_red,
            comp_adjustment=comp_adjustment,
        )

    def _profile_team(self, champions: List[str]) -> CompProfile:
        """Build a team composition profile."""
        archetype_sums: Dict[str, float] = {}

        for champ in champions:
            affinities = _CHAMPION_ARCHETYPES.get(champ, {})
            for arch_key, score in affinities.items():
                archetype_sums[arch_key] = archetype_sums.get(arch_key, 0.0) + score

        # Normalize (5 players max contribution per archetype)
        for key in archetype_sums:
            archetype_sums[key] /= 5.0

        # Find primary and secondary
        sorted_archs = sorted(archetype_sums.items(), key=lambda x: x[1], reverse=True)

        primary = CompArchetype.BALANCED
        secondary = None

        if sorted_archs and sorted_archs[0][1] >= 0.2:
            primary = _ARCHETYPE_KEYS.get(sorted_archs[0][0], CompArchetype.BALANCED)
        if len(sorted_archs) >= 2 and sorted_archs[1][1] >= 0.15:
            secondary = _ARCHETYPE_KEYS.get(sorted_archs[1][0])

        # Phase advantages
        phase_advs = {}
        for phase in ("EARLY", "MID", "LATE"):
            base = _PHASE_SUITABILITY.get(primary, {}).get(phase, 0.0)
            if secondary:
                base += _PHASE_SUITABILITY.get(secondary, {}).get(phase, 0.0) * 0.5
            phase_advs[phase] = base

        return CompProfile(
            champions=champions,
            archetype_scores=archetype_sums,
            primary_archetype=primary,
            secondary_archetype=secondary,
            phase_advantages=phase_advs,
        )

    def _describe_matchup(self, blue: CompArchetype, red: CompArchetype) -> str:
        """Describe the archetype matchup in natural language."""
        if blue == red:
            return f"Mirror {blue.name} matchup — execution decides"

        matchup_map = {
            (CompArchetype.TEAMFIGHT, CompArchetype.SPLIT_PUSH): "Teamfight vs Split — blue wants 5v5, red wants side lanes",
            (CompArchetype.TEAMFIGHT, CompArchetype.POKE): "Teamfight vs Poke — blue must hard engage, red kites",
            (CompArchetype.PICK, CompArchetype.TEAMFIGHT): "Pick vs Teamfight — blue catches before grouping",
            (CompArchetype.SIEGE, CompArchetype.DIVE): "Siege vs Dive — blue pokes towers, red forces fights",
            (CompArchetype.PROTECT, CompArchetype.DIVE): "Protect vs Dive — blue peels, red targets carry",
        }

        key = (blue, red)
        if key in matchup_map:
            return matchup_map[key]
        key_rev = (red, blue)
        if key_rev in matchup_map:
            # Reverse the description
            return matchup_map[key_rev].replace("blue", "TEMP").replace("red", "blue").replace("TEMP", "red")

        return f"{blue.name} vs {red.name}"

    def _win_condition(self, archetype: CompArchetype) -> str:
        conditions = {
            CompArchetype.TEAMFIGHT: "Group for 5v5 fights around objectives",
            CompArchetype.PICK: "Find isolated targets with vision control",
            CompArchetype.SIEGE: "Poke and take towers methodically",
            CompArchetype.SPLIT_PUSH: "Apply side lane pressure, avoid 5v5",
            CompArchetype.POKE: "Maintain distance and chip health before fights",
            CompArchetype.DIVE: "Hard engage onto enemy carries",
            CompArchetype.PROTECT: "Peel for carries, scale to late game",
            CompArchetype.BALANCED: "Adapt to what the game gives you",
        }
        return conditions.get(archetype, "Play to your strengths")

    def stats(self) -> Dict[str, Any]:
        return {"analysis_count": self._analysis_count}


# ═══════════════════════════════════════════════════════════════════════════
# Claude20: Extended comp analyzer with meta awareness and voice summary
# ═══════════════════════════════════════════════════════════════════════════


class CompAnalyzerV2(CompAnalyzer):
    """Extended composition analyzer with voice and meta features.

    Claude20: Adds voice-friendly summaries, team scaling curves,
    and fight-style recommendations. All existing CompAnalyzer
    methods preserved.
    """

    def __init__(self) -> None:
        super().__init__()
        self._last_voice_time: float = 0.0
        self._voice_cooldown_s: float = 120.0

    def generate_voice_summary(
        self,
        report: CompAnalysisReport,
        game_time: float,
    ) -> Optional[str]:
        """Generate a TTS-friendly comp summary.

        Claude20: Called once at game start or on phase transition.
        """
        if game_time - self._last_voice_time < self._voice_cooldown_s:
            return None

        blue = report.blue_profile.primary_archetype.name.lower().replace("_", " ")
        red = report.red_profile.primary_archetype.name.lower().replace("_", " ")

        if report.blue_phase_advantage > 0.05:
            advantage_text = "We have the comp advantage in this phase."
        elif report.blue_phase_advantage < -0.05:
            advantage_text = "Enemy comp is favored in this phase."
        else:
            advantage_text = "Compositions are evenly matched this phase."

        text = (
            f"Our comp is {blue}, theirs is {red}. "
            f"{advantage_text} "
            f"Our win condition: {report.win_condition_blue}."
        )

        self._last_voice_time = game_time
        return text

    def estimate_scaling_curve(
        self, profile: CompProfile,
    ) -> Dict[str, float]:
        """Estimate team strength at each game phase.

        Claude20: Returns normalized strength score per phase.
        Used by prediction to weight time-based factors.
        """
        base_strength = 0.5
        curve = {}
        for phase in ("EARLY", "MID", "LATE"):
            adj = profile.phase_advantages.get(phase, 0.0)
            curve[phase] = round(base_strength + adj, 3)
        return curve

    def recommend_fight_style(
        self, report: CompAnalysisReport,
    ) -> str:
        """Recommend how the team should approach fights.

        Claude20: Translates comp archetype matchup into
        actionable fight-style advice.
        """
        our_arch = report.blue_profile.primary_archetype
        their_arch = report.red_profile.primary_archetype

        if our_arch == CompArchetype.TEAMFIGHT:
            if their_arch == CompArchetype.SPLIT_PUSH:
                return "Force 5v5 — don't let them split. Group and push."
            return "Look for big teamfights around objectives."

        if our_arch == CompArchetype.PICK:
            return "Set up vision and look for isolated targets. Avoid 5v5."

        if our_arch == CompArchetype.SIEGE:
            return "Poke before fights. Take towers with numbers advantage."

        if our_arch == CompArchetype.SPLIT_PUSH:
            if their_arch == CompArchetype.TEAMFIGHT:
                return "Split the map. Avoid 5v5 at all costs."
            return "Apply side lane pressure. Force them to respond."

        if our_arch == CompArchetype.POKE:
            return "Keep distance. Chip health before engaging."

        if our_arch == CompArchetype.DIVE:
            return "Hard engage onto their carries. Flash in and burst."

        if our_arch == CompArchetype.PROTECT:
            return "Peel for your carry. Let them scale and do damage safely."

        return "Play to your strengths and adapt."

    def full_analysis(
        self,
        blue_champions: List[str],
        red_champions: List[str],
        current_phase: str = "EARLY",
        game_time: float = 0.0,
    ) -> Dict[str, Any]:
        """Full analysis with voice, scaling, and fight style.

        Claude20: One-call comprehensive comp analysis.
        """
        report = self.analyze(blue_champions, red_champions, current_phase)
        voice = self.generate_voice_summary(report, game_time)
        blue_curve = self.estimate_scaling_curve(report.blue_profile)
        red_curve = self.estimate_scaling_curve(report.red_profile)
        fight_style = self.recommend_fight_style(report)

        return {
            "report": report.to_dict(),
            "voice": voice,
            "blue_scaling": blue_curve,
            "red_scaling": red_curve,
            "fight_style": fight_style,
        }

    def extended_stats(self) -> Dict[str, Any]:
        base = self.stats()
        base["last_voice_time"] = self._last_voice_time
        return base
