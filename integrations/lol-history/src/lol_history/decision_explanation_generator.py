"""
DecisionExplanationGenerator — Generates human-readable decision explanations.

Architecture (拿来主义):
  realtime_voice_command_generator.py（M662）— inference→voice text
  protocol_anomaly_coaching_translator.py（M654）— anomaly→advice translation

Location: integrations/lol-history/src/lol_history/decision_explanation_generator.py
"""
from __future__ import annotations
import logging, time
from typing import Any, Callable, Dict, List, Optional
logger = logging.getLogger(__name__)
_EVOLUTION_KEY = "integrations.lol_history.decision_explanation_generator.v1"

class DecisionExplanationGenerator:
    """Generates explanations for decisions at brief/normal/detailed levels.

    Public API: explain, set_verbosity, register_template, get_stats
    """
    def __init__(self, verbosity: str = "normal") -> None:
        self.evolution_callback: Optional[Callable] = None
        self._op_count = 0
        self._verbosity = verbosity
        self._templates: Dict[str, Dict[str, str]] = self._default_templates()
        self._explain_count = 0

    def _fire(self, et, data):
        if self.evolution_callback: self.evolution_callback({"type": et, "key": _EVOLUTION_KEY, **data})

    def _default_templates(self):
        return {
            "push": {"brief": "Push {lane}.", "normal": "Push {lane} — {reason}. Risk: {risk}.",
                     "detailed": "Recommend pushing {lane}. Reasoning: {reason}. Risk level: {risk}. Factors: {factors}."},
            "retreat": {"brief": "Retreat!", "normal": "Retreat — {reason}. Risk: {risk}.",
                        "detailed": "Recommend retreat. Reasoning: {reason}. Current risk: {risk}. Factors: {factors}."},
            "farm": {"brief": "Farm.", "normal": "Farm {area} — {reason}.",
                     "detailed": "Focus on farming in {area}. Reasoning: {reason}. Expected gold/min: {gold_rate}."},
            "objective": {"brief": "Take {objective}!", "normal": "Contest {objective} — {reason}.",
                          "detailed": "Contest {objective}. Reasoning: {reason}. Team readiness: {readiness}. Risk: {risk}."},
            "default": {"brief": "{intent}.", "normal": "{intent} — {reason}.",
                        "detailed": "{intent}. Reasoning: {reason}. Factors: {factors}. Risk: {risk}."},
        }

    def set_verbosity(self, level: str) -> Dict[str, Any]:
        self._op_count += 1
        if level not in ("brief", "normal", "detailed"): return {"status": "error", "reason": "invalid verbosity"}
        self._verbosity = level
        return {"status": "ok", "verbosity": level}

    def register_template(self, intent: str, templates: Dict[str, str]) -> Dict[str, Any]:
        self._op_count += 1
        self._templates[intent] = templates
        return {"status": "ok", "intent": intent}

    def explain(self, decision: Dict[str, Any], verbosity: str = None) -> Dict[str, Any]:
        self._op_count += 1
        self._explain_count += 1
        v = verbosity or self._verbosity
        intent = decision.get("intent", "default")
        templates = self._templates.get(intent, self._templates["default"])
        template = templates.get(v, templates.get("normal", "{intent}"))
        defaults = {"intent": intent, "reason": "analysis indicates", "risk": "medium",
                    "lane": "mid", "area": "jungle", "factors": "multiple", "objective": "dragon",
                    "readiness": "ready", "gold_rate": "0"}
        params = {**defaults, **decision}
        try: text = template.format_map(params)
        except (KeyError, ValueError): text = f"{intent}: {decision.get('reason', 'no reason')}"
        self._fire("explained", {"intent": intent, "verbosity": v})
        return {"status": "ok", "text": text, "intent": intent, "verbosity": v}

    def get_stats(self) -> Dict[str, Any]:
        return {"explanations": self._explain_count, "verbosity": self._verbosity,
                "templates": list(self._templates.keys()), "total_ops": self._op_count}

