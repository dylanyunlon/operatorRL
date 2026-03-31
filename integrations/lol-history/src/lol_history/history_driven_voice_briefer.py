"""
HistoryDrivenVoiceBriefer — Generates voice-ready briefings from historical intel.

Architecture (拿来主义):
  pregame_voice_briefer.py — voice briefing generation
  realtime_voice_command_generator.py（M662）— TTS-optimized text output

Location: integrations/lol-history/src/lol_history/history_driven_voice_briefer.py

Design Notes (Knuth-level critique):
  User:
    - brief() produces concise, spoken-language briefings suitable for TTS.
    - Prioritizes actionable info first (threats, weaknesses, strategy).
  System:
    - Text is optimized for speech: short sentences, no abbreviations, natural phrasing.
    - Duration target: 30-60 seconds spoken at normal pace.
"""
from __future__ import annotations
import logging, time
from typing import Any, Callable, Dict, List, Optional
logger = logging.getLogger(__name__)
_EVOLUTION_KEY = "integrations.lol_history.history_driven_voice_briefer.v1"

_WORDS_PER_SECOND = 2.5  # average spoken pace


class HistoryDrivenVoiceBriefer:
    """Generates TTS-optimized briefings from historical intelligence.

    Public API: brief_pregame, brief_live_update, set_verbosity, get_stats
    """
    def __init__(self, max_duration_s: float = 45.0) -> None:
        self.evolution_callback: Optional[Callable] = None
        self._op_count = 0
        self._max_duration = max_duration_s
        self._max_words = int(max_duration_s * _WORDS_PER_SECOND)
        self._verbosity = "normal"  # brief, normal, detailed
        self._brief_count = 0

    def _fire(self, et, data):
        if self.evolution_callback: self.evolution_callback({"type": et, "key": _EVOLUTION_KEY, **data})

    def set_verbosity(self, level: str) -> Dict[str, Any]:
        """Set verbosity: 'brief' (15s), 'normal' (30s), 'detailed' (60s)."""
        self._op_count += 1
        if level in ("brief", "normal", "detailed"):
            self._verbosity = level
            multiplier = {"brief": 0.5, "normal": 1.0, "detailed": 2.0}[level]
            self._max_words = int(45 * _WORDS_PER_SECOND * multiplier)
        return {"status": "ok", "verbosity": self._verbosity, "max_words": self._max_words}

    def brief_pregame(self, intel: Dict[str, Any]) -> Dict[str, Any]:
        """Generate pregame voice briefing.

        Args:
            intel: Dict from PregameStrategySynthesizer.synthesize().
        """
        self._op_count += 1
        self._brief_count += 1

        lines = []

        # Opening
        lines.append("Here's your pregame briefing.")

        # Threat assessment
        sections = intel.get("sections", {})
        scout = sections.get("opponent_scout", {})
        profiles = scout.get("profiles", [])
        if profiles:
            top = profiles[0]
            name = top.get("name", "unknown player")
            threat = top.get("threat_score", 0)
            if threat > 0.7:
                lines.append(f"Watch out for {name}. High threat level.")
            elif threat > 0.5:
                lines.append(f"{name} is the main opponent to track.")

            weaknesses = top.get("weaknesses", [])
            if "on_losing_streak" in weaknesses:
                lines.append(f"{name} is on a losing streak. They might be tilted.")
            if "poor_vision" in weaknesses:
                lines.append(f"{name} has poor vision control. Exploit blind spots.")

        # Win prediction
        predictor = sections.get("matchup_predictor", {})
        wp = predictor.get("win_probability")
        if wp is not None:
            pct = int(wp * 100)
            if pct > 55:
                lines.append(f"We have a {pct} percent win chance. Play confident.")
            elif pct < 45:
                lines.append(f"Tough matchup at {pct} percent. We need early leads.")
            else:
                lines.append(f"Even matchup at {pct} percent.")

        # Tilt info
        tilt = sections.get("tilt_detector", {})
        most_tilted = tilt.get("most_tilted")
        if most_tilted and most_tilted.get("tilt_probability", 0) > 0.5:
            t_name = most_tilted.get("name", "an opponent")
            lines.append(f"{t_name} appears tilted. Apply early pressure there.")

        # Strategy
        strategies = intel.get("strategies", [])
        if strategies:
            lines.append("Key strategy. " + strategies[0] + ".")

        # Trim to word limit
        text = " ".join(lines)
        words = text.split()
        if len(words) > self._max_words:
            words = words[:self._max_words]
            text = " ".join(words) + "."

        est_duration = round(len(text.split()) / _WORDS_PER_SECOND, 1)

        result = {
            "status": "ok",
            "text": text,
            "word_count": len(text.split()),
            "estimated_duration_s": est_duration,
            "line_count": len(lines),
        }
        self._fire("pregame_briefed", {"words": len(text.split()), "duration": est_duration})
        return result

    def brief_live_update(self, event_type: str, context: Dict[str, Any]) -> Dict[str, Any]:
        """Generate a short live voice update during the game.

        Args:
            event_type: e.g. "power_spike", "opponent_deviation", "objective_timer".
            context: Event-specific data.
        """
        self._op_count += 1
        self._brief_count += 1

        text = ""
        if event_type == "power_spike":
            champ = context.get("champion_name", "the opponent")
            text = f"Careful. {champ} just hit a power spike."
        elif event_type == "opponent_deviation":
            name = context.get("name", "opponent")
            metric = context.get("metric", "behavior")
            direction = context.get("direction", "changed")
            text = f"{name} is playing {direction} than usual in {metric}. Adjust accordingly."
        elif event_type == "objective_timer":
            obj = context.get("objective", "objective")
            seconds = context.get("seconds_until", 60)
            text = f"{obj} spawning in {seconds} seconds. Position now."
        elif event_type == "tilt_alert":
            name = context.get("name", "opponent")
            text = f"{name} seems to be tilting. Keep the pressure on."
        else:
            text = f"Update: {event_type}."

        return {
            "status": "ok", "text": text,
            "word_count": len(text.split()),
            "estimated_duration_s": round(len(text.split()) / _WORDS_PER_SECOND, 1),
            "event_type": event_type,
        }

    def get_stats(self) -> Dict[str, Any]:
        self._op_count += 1
        return {"op_count": self._op_count, "brief_count": self._brief_count,
                "verbosity": self._verbosity, "max_words": self._max_words}
