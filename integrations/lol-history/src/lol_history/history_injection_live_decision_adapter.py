"""
HistoryInjectionLiveDecisionAdapter — Adapts historical intel for live decision injection.

Architecture (拿来主义):
  live_coaching_history_adapter.py（M576）— coaching context injection
  seraphine_inference_bridge.py（M581）— Seraphine data → inference pipeline bridge
  Seraphine/app/lol/tools.py — parseGames data structuring for downstream consumption

Location: integrations/lol-history/src/lol_history/history_injection_live_decision_adapter.py

Design Notes (Knuth-level critique):
  User:
    - Seamless: live decision engine receives enriched context without knowing source.
    - Prioritized injection: most relevant historical intel injected first when
      context window is limited.
  System:
    - Adapter pattern: transforms history module outputs into decision engine input format.
    - Context budget: limits injected tokens/features to prevent decision engine overload.
    - Staleness detection: historical data older than threshold gets lower priority.
"""
from __future__ import annotations
import logging, time
from typing import Any, Callable, Dict, List, Optional
logger = logging.getLogger(__name__)
_EVOLUTION_KEY = "integrations.lol_history.history_injection_live_decision_adapter.v1"

def _safe_div(a, b, d=0.0): return a / b if b else d


class HistoryInjectionLiveDecisionAdapter:
    """Adapts historical intelligence for injection into live decision pipeline.

    Public API: inject, set_context_budget, register_intel_provider,
                get_injection_summary, flush, get_stats
    """
    def __init__(self, context_budget: int = 50, staleness_threshold: float = 3600.0
                 ) -> None:
        self.evolution_callback: Optional[Callable] = None
        self._op_count = 0
        self._inject_count = 0
        self._context_budget = context_budget  # max features to inject
        self._staleness_threshold = staleness_threshold
        self._providers: Dict[str, Any] = {}
        self._last_injection: Optional[Dict[str, Any]] = None
        self._injection_history: List[Dict[str, Any]] = []

    def _fire(self, et, data):
        if self.evolution_callback:
            self.evolution_callback({"type": et, "key": _EVOLUTION_KEY, **data})

    def register_intel_provider(self, name: str, provider: Any,
                                 priority: int = 5) -> Dict[str, Any]:
        """Register a historical intel provider with priority (1=highest, 10=lowest)."""
        self._op_count += 1
        self._providers[name] = {"provider": provider, "priority": priority}
        return {"status": "ok", "provider": name, "priority": priority,
                "total_providers": len(self._providers)}

    def set_context_budget(self, budget: int) -> Dict[str, Any]:
        """Set maximum number of features to inject per decision cycle."""
        self._op_count += 1
        self._context_budget = budget
        return {"status": "ok", "context_budget": budget}

    def inject(self, game_state: Dict[str, Any],
                player_puuids: List[str] = None) -> Dict[str, Any]:
        """Inject historical intelligence into a live game state for decision making."""
        self._op_count += 1
        self._inject_count += 1
        player_puuids = player_puuids or []
        now = time.time()
        injected_features: List[Dict[str, Any]] = []
        errors = []
        # Collect from all providers, sorted by priority
        sorted_providers = sorted(
            self._providers.items(),
            key=lambda x: x[1].get("priority", 5))
        for prov_name, prov_info in sorted_providers:
            if len(injected_features) >= self._context_budget:
                break
            provider = prov_info["provider"]
            try:
                if hasattr(provider, "get_intel_for_injection"):
                    intel = provider.get_intel_for_injection(
                        game_state=game_state, puuids=player_puuids)
                elif hasattr(provider, "get_stats"):
                    intel = {"source": prov_name, "stats": provider.get_stats()}
                else:
                    continue
                if not intel:
                    continue
                # Check staleness
                intel_time = intel.get("timestamp", intel.get("_cached_at", now))
                age = now - intel_time if intel_time < now else 0
                staleness = min(1.0, age / self._staleness_threshold) if self._staleness_threshold else 0
                feature = {
                    "source": prov_name,
                    "priority": prov_info["priority"],
                    "data": intel,
                    "staleness": round(staleness, 3),
                    "freshness_score": round(1.0 - staleness, 3),
                }
                injected_features.append(feature)
            except Exception as e:
                errors.append({"source": prov_name, "error": str(e)})
                logger.debug("Provider %s failed: %s", prov_name, e)
        # Sort injected features by freshness * priority
        injected_features.sort(
            key=lambda f: f["freshness_score"] / max(f["priority"], 1),
            reverse=True)
        # Trim to budget
        injected_features = injected_features[:self._context_budget]
        injection = {
            "timestamp": now,
            "features_injected": len(injected_features),
            "features": injected_features,
            "errors": errors,
            "budget_used": len(injected_features),
            "budget_total": self._context_budget,
        }
        self._last_injection = injection
        self._injection_history.append(injection)
        if len(self._injection_history) > 200:
            self._injection_history = self._injection_history[-100:]
        self._fire("injected", {"features": len(injected_features),
                                 "errors": len(errors)})
        return {"status": "ok", "injection": injection}

    def get_injection_summary(self) -> Dict[str, Any]:
        """Get summary of last injection."""
        self._op_count += 1
        if not self._last_injection:
            return {"status": "ok", "last_injection": None}
        return {"status": "ok", "last_injection": self._last_injection}

    def flush(self) -> Dict[str, Any]:
        """Clear injection cache for new game."""
        self._op_count += 1
        self._last_injection = None
        return {"status": "ok", "flushed": True}

    def get_stats(self) -> Dict[str, Any]:
        avg_features = 0.0
        if self._injection_history:
            avg_features = sum(
                i["features_injected"] for i in self._injection_history
            ) / len(self._injection_history)
        return {"inject_count": self._inject_count,
                "providers": len(self._providers),
                "context_budget": self._context_budget,
                "avg_features_per_injection": round(avg_features, 1),
                "total_ops": self._op_count}
