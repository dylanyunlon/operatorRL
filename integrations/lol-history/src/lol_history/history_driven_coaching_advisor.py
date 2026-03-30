"""
HistoryDrivenCoachingAdvisor — Generate coaching tips from historical performance data.

Architecture (拿来主义):
  查看 **real_time_coaching_engine.py** 的教练模式——它基于live state + historical context
  生成structured advice。从 **decision_engine.py** 的advantage→suggestion转换开始。
  实现 **HistoryDrivenCoachingAdvisor**，让 **lol_agent_orchestrator** 可以 **在pregame/
  ingame/postgame三个阶段基于历史数据生成个性化教练建议**。

Location: integrations/lol-history/src/lol_history/history_driven_coaching_advisor.py

Design Notes (Knuth-level critique):
  User:
    - Pregame advice considers streak, fatigue, matchup history.
    - Ingame advice adapts to current state + historical patterns.
    - Postgame advice identifies specific areas for improvement.
    - Tips are constructive and encouraging even after losses.
  System:
    - Tip generation is rule-based (no external model dependency).
    - Priority ranking ensures most impactful advice appears first.
    - Empty profiles produce safe generic advice (never crashes).
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "integrations.lol_history.history_driven_coaching_advisor.v1"

# Thresholds for generating specific advice
LOW_WINRATE = 0.45
HIGH_WINRATE = 0.60
LOW_KDA = 2.0
LOW_CS_PER_MIN = 6.0
LOW_VISION = 15
LOW_KP = 0.4
LOSS_STREAK_THRESHOLD = 3
FATIGUE_GAMES = 8
FATIGUE_MINUTES = 240


class HistoryDrivenCoachingAdvisor:
    """Generate coaching tips from historical performance data.

    Public API
    ----------
    generate_pregame_advice(profile) -> dict
    generate_ingame_advice(state, history) -> dict
    generate_postgame_advice(result) -> dict
    """

    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable[[Dict[str, Any]], None]] = None
        self._advice_count: int = 0

    def generate_pregame_advice(self, profile: Dict[str, Any]) -> Dict[str, Any]:
        """Generate advice before a game starts.

        Parameters
        ----------
        profile : dict
            Player profile with winrate, streak, session_games, etc.
        """
        tips: List[str] = []

        winrate = profile.get("winrate", 0.5)
        streak = profile.get("streak", {})
        streak_type = streak.get("type", "none")
        streak_len = streak.get("length", 0)
        session_games = profile.get("session_games", 0)
        session_minutes = profile.get("session_minutes", 0)

        # Fatigue check
        if session_games >= FATIGUE_GAMES or session_minutes >= FATIGUE_MINUTES:
            tips.append("You have been playing for a while. Consider taking a break to avoid fatigue and maintain focus.")

        # Streak analysis
        if streak_type == "loss" and streak_len >= LOSS_STREAK_THRESHOLD:
            tips.append("You are on a losing streak. Take a short rest to reset your mental state before queueing again.")
        elif streak_type == "win" and streak_len >= 3:
            tips.append("Great momentum! You are on a win streak. Stay focused and keep up the good work.")

        # Winrate advice
        if winrate < LOW_WINRATE and profile:
            tips.append("Your recent winrate is below average. Focus on fundamentals: CS, map awareness, and safe positioning.")
        elif winrate >= HIGH_WINRATE:
            tips.append("Strong performance recently. Consider trying new champions or strategies to expand your pool.")

        # Best role suggestion
        best_role = profile.get("best_role")
        if best_role:
            tips.append(f"Your strongest role is {best_role}. Prioritize it for the best chance of winning.")

        # Default if no tips generated
        if not tips:
            tips.append("Stay positive, focus on objectives, and communicate with your team.")

        self._advice_count += 1
        self._fire("pregame_advice", {"tip_count": len(tips)})
        return {"tips": tips, "phase": "pregame"}

    def generate_ingame_advice(
        self,
        state: Dict[str, Any],
        history: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Generate advice during an active game.

        Parameters
        ----------
        state : dict
            Current game state (game_time, gold_diff, kills, deaths, cs, etc.).
        history : dict
            Historical context (matchup_winrate, comeback_rate, etc.).
        """
        tips: List[str] = []

        gold_diff = state.get("gold_diff", 0)
        game_time = state.get("game_time", 0)
        deaths = state.get("deaths", 0)
        matchup_wr = history.get("matchup_winrate", 0.5)
        comeback_rate = history.get("comeback_rate", 0.0)

        if gold_diff < -3000 and game_time < 900:
            tips.append("You are behind early. Play safe, focus on CS under tower, and wait for jungle ganks.")
            if comeback_rate > 0.3:
                tips.append("Historically you have a decent comeback rate. Stay calm and look for opportunities.")

        if deaths >= 3 and game_time < 600:
            tips.append("Multiple early deaths detected. Hug tower and avoid risky trades until you catch up.")

        if matchup_wr < 0.4:
            opp = state.get("opponent_champion", "your opponent")
            tips.append(f"You historically struggle against {opp}. Play cautiously and seek help from teammates.")

        if gold_diff > 3000:
            tips.append("You have a significant gold lead. Press your advantage with objectives and vision control.")

        if not tips:
            tips.append("Keep farming, maintain vision, and look for favorable trades.")

        self._advice_count += 1
        return {"tips": tips, "phase": "ingame"}

    def generate_postgame_advice(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """Generate improvement advice after a game.

        Parameters
        ----------
        result : dict
            Post-game stats: won, kda, cs_per_min, vision_score, kill_participation, duration.
        """
        areas: List[Dict[str, Any]] = []

        kda = result.get("kda", 0.0)
        cs_per_min = result.get("cs_per_min", 0.0)
        vision = result.get("vision_score", 0)
        kp = result.get("kill_participation", 0.0)
        won = result.get("won", False)
        duration = result.get("duration_minutes", 30)

        # Priority-ranked improvement areas
        if kda < LOW_KDA:
            areas.append({
                "area": "survivability",
                "priority": 1,
                "tip": "Focus on reducing deaths. Position more carefully in fights and avoid overextending.",
            })

        if cs_per_min < LOW_CS_PER_MIN:
            areas.append({
                "area": "farming",
                "priority": 2,
                "tip": "Your CS per minute is below average. Practice last-hitting and minimize roaming without purpose.",
            })

        if vision < LOW_VISION:
            areas.append({
                "area": "vision",
                "priority": 3,
                "tip": "Ward more consistently. Vision control prevents ganks and enables better team plays.",
            })

        if kp < LOW_KP:
            areas.append({
                "area": "team_participation",
                "priority": 4,
                "tip": "Your kill participation is low. Look to join team fights earlier and respond to pings.",
            })

        areas.sort(key=lambda x: x["priority"])

        summary = "Well played!" if won else "Tough game, but every loss is a learning opportunity."

        self._advice_count += 1
        self._fire("postgame_advice", {"areas_count": len(areas), "won": won})
        return {
            "summary": summary,
            "areas_to_improve": areas,
            "phase": "postgame",
        }

    def to_dict(self) -> Dict[str, Any]:
        return {"advice_count": self._advice_count}

    def _fire(self, event_type: str, data: Dict[str, Any]) -> None:
        if self.evolution_callback:
            self.evolution_callback({
                "type": event_type,
                "key": _EVOLUTION_KEY,
                "timestamp": time.time(),
                **data,
            })
