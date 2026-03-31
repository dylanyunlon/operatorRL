"""
PregameStrategySynthesizer — Synthesizes all historical intel into a pregame briefing.

Architecture (拿来主义):
  pregame_scout.py — pregame scouting aggregation
  pregame_voice_briefer.py — briefing generation patterns

Location: integrations/lol-history/src/lol_history/pregame_strategy_synthesizer.py

Design Notes (Knuth-level critique):
  User:
    - synthesize() produces a structured pregame briefing from all intel modules.
    - Briefing includes: threat ranking, lane matchup summary, comp analysis, tilt info.
  System:
    - Each intel source is fetched independently with error isolation.
    - Briefing format is structured for both display and TTS conversion.
"""
from __future__ import annotations
import logging, time
from typing import Any, Callable, Dict, List, Optional
logger = logging.getLogger(__name__)
_EVOLUTION_KEY = "integrations.lol_history.pregame_strategy_synthesizer.v1"

def _safe_div(a, b, d=0.0): return a / b if b else d


class PregameStrategySynthesizer:
    """Synthesizes pregame strategy briefing from all historical intel sources.

    Public API: register_intel_source, synthesize, get_last_briefing, get_stats
    """
    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable] = None
        self._op_count = 0
        self._intel_sources: Dict[str, Callable] = {}
        self._last_briefing: Optional[Dict] = None
        self._synth_count = 0

    def _fire(self, et, data):
        if self.evolution_callback: self.evolution_callback({"type": et, "key": _EVOLUTION_KEY, **data})

    def register_intel_source(self, name: str, fetch_fn: Callable) -> Dict[str, Any]:
        """Register an intel source.

        Args:
            name: Source name (e.g. "opponent_scout", "matchup_predictor", "tilt_detector").
            fetch_fn: Callable(game_context: Dict) -> Dict with intel data.
        """
        self._op_count += 1
        self._intel_sources[name] = fetch_fn
        return {"status": "ok", "source": name, "total_sources": len(self._intel_sources)}

    def synthesize(self, game_context: Dict[str, Any]) -> Dict[str, Any]:
        """Synthesize a full pregame briefing.

        Args:
            game_context: Dict with our_team, enemy_team, map, queue_type, etc.

        Returns:
            Structured briefing with sections from each intel source.
        """
        self._op_count += 1
        self._synth_count += 1
        t0 = time.time()

        sections: Dict[str, Any] = {}
        errors: Dict[str, str] = {}
        warnings: List[str] = []

        for source_name, fetch_fn in self._intel_sources.items():
            try:
                intel = fetch_fn(game_context)
                sections[source_name] = intel
            except Exception as e:
                errors[source_name] = str(e)
                warnings.append(f"{source_name} unavailable: {e}")

        # Generate summary from available sections
        summary_lines = []

        # Threat summary
        scout_data = sections.get("opponent_scout", {})
        if scout_data.get("profiles"):
            top_threat = scout_data["profiles"][0]
            summary_lines.append(
                f"Top threat: {top_threat.get('name', '?')} "
                f"(threat={top_threat.get('threat_score', 0):.2f})")

        # Win prediction
        predictor_data = sections.get("matchup_predictor", {})
        win_prob = predictor_data.get("win_probability")
        if win_prob is not None:
            summary_lines.append(f"Predicted win rate: {win_prob:.1%}")

        # Tilt info
        tilt_data = sections.get("tilt_detector", {})
        most_tilted = tilt_data.get("most_tilted")
        if most_tilted and most_tilted.get("tilt_probability", 0) > 0.3:
            summary_lines.append(
                f"Tilted opponent: {most_tilted.get('name', '?')} "
                f"({most_tilted.get('tilt_state', '?')})")

        # Comp analysis
        comp_data = sections.get("comp_analyzer", {})
        archetype = comp_data.get("archetype")
        if archetype:
            summary_lines.append(f"Our comp archetype: {archetype}")

        # Key strategy recommendations
        strategies = []
        if most_tilted and most_tilted.get("tilt_probability", 0) > 0.5:
            strategies.append(f"Pressure {most_tilted.get('name', 'tilted opponent')} early")
        if win_prob and win_prob > 0.55:
            strategies.append("Play standard; advantage from draft")
        elif win_prob and win_prob < 0.45:
            strategies.append("Look for early leads; draft disadvantage")

        elapsed = round((time.time() - t0) * 1000, 1)

        briefing = {
            "status": "ok",
            "summary": " | ".join(summary_lines) if summary_lines else "Insufficient data for briefing",
            "summary_lines": summary_lines,
            "strategies": strategies,
            "sections": sections,
            "errors": errors,
            "warnings": warnings,
            "elapsed_ms": elapsed,
            "sources_used": len(sections),
            "sources_failed": len(errors),
        }

        self._last_briefing = briefing
        self._fire("synthesized", {"sources": len(sections), "elapsed_ms": elapsed})
        return briefing

    def get_last_briefing(self) -> Optional[Dict[str, Any]]:
        self._op_count += 1
        return self._last_briefing

    def get_stats(self) -> Dict[str, Any]:
        self._op_count += 1
        return {"op_count": self._op_count, "synth_count": self._synth_count,
                "intel_sources": len(self._intel_sources)}
