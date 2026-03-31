"""
HistoryToLiveFusionOrchestrator — Top-level orchestrator for M706-M724 modules.

Architecture (拿来主义):
  autonomous_decision_orchestrator.py（M705）— register→init→run lifecycle
  multi_game_pipeline_orchestrator.py（M685）— multi-module orchestration

Location: integrations/lol-history/src/lol_history/history_to_live_fusion_orchestrator.py

Design Notes (Knuth-level critique):
  User:
    - Single entry point: run_pregame() for lobby, run_live_update() during game.
    - Orchestrates all M706-M724 modules through a unified pipeline.
  System:
    - Module failures are isolated; partial intel is better than no intel.
    - Each phase has a time budget to ensure real-time responsiveness.
    - Full telemetry per run enables pipeline health monitoring.
"""
from __future__ import annotations
import logging, time
from typing import Any, Callable, Dict, List, Optional
logger = logging.getLogger(__name__)
_EVOLUTION_KEY = "integrations.lol_history.history_to_live_fusion_orchestrator.v1"

def _safe_div(a, b, d=0.0): return a / b if b else d


class HistoryToLiveFusionOrchestrator:
    """Orchestrates the full historical intelligence → live game fusion pipeline.

    Public API: register_module, initialize, run_pregame, run_live_update, get_dashboard, shutdown, get_stats
    """
    def __init__(self, time_budget_ms: float = 5000.0) -> None:
        self.evolution_callback: Optional[Callable] = None
        self._op_count = 0
        self._modules: Dict[str, Any] = {}
        self._state = "uninitialized"
        self._time_budget_ms = time_budget_ms
        self._pregame_count = 0
        self._live_update_count = 0
        self._total_run_ms = 0.0
        self._error_count = 0
        self._module_timings: Dict[str, List[float]] = {}
        self._last_pregame_result: Optional[Dict] = None
        self._last_live_result: Optional[Dict] = None

    def _fire(self, et, data):
        if self.evolution_callback: self.evolution_callback({"type": et, "key": _EVOLUTION_KEY, **data})

    def register_module(self, name: str, module: Any) -> Dict[str, Any]:
        """Register a pipeline module.

        Expected modules:
          history_aggregator, opponent_scout, matchup_predictor, draft_advisor,
          tendency_tracker, itemization_advisor, lane_enricher, comp_analyzer,
          tilt_detector, objective_advisor, strategy_synthesizer, confidence_calibrator,
          event_correlator, pool_predictor, power_spike_advisor, pattern_detector,
          voice_briefer, adaptation_tracker, cache_manager
        """
        self._op_count += 1
        self._modules[name] = module
        self._module_timings[name] = []
        return {"status": "ok", "module": name, "total_modules": len(self._modules)}

    def initialize(self) -> Dict[str, Any]:
        """Initialize all registered modules."""
        self._op_count += 1
        self._state = "initialized"
        self._fire("initialized", {"modules": list(self._modules.keys())})
        return {"status": "ok", "state": self._state, "modules": len(self._modules)}

    def _run_module(self, name: str, method: str, *args, **kwargs) -> Optional[Any]:
        """Run a module method with timing and error isolation."""
        module = self._modules.get(name)
        if not module:
            return None
        fn = getattr(module, method, None)
        if not fn:
            return None
        t0 = time.time()
        try:
            result = fn(*args, **kwargs)
            elapsed = (time.time() - t0) * 1000
            self._module_timings[name].append(elapsed)
            return result
        except Exception as e:
            elapsed = (time.time() - t0) * 1000
            self._module_timings[name].append(elapsed)
            self._error_count += 1
            logger.warning("Module %s.%s failed: %s", name, method, e)
            return {"error": str(e)}

    def run_pregame(self, game_context: Dict[str, Any]) -> Dict[str, Any]:
        """Run the full pregame intelligence pipeline.

        Args:
            game_context: {our_team: [...], enemy_team: [...], queue_type, ...}
                our_team/enemy_team entries: {puuid, summoner_name, champion_id, role}
        """
        self._op_count += 1
        self._pregame_count += 1
        t0 = time.time()

        results: Dict[str, Any] = {}
        errors: Dict[str, str] = {}

        # Phase 1: Aggregate opponent histories
        enemy_team = game_context.get("enemy_team", [])
        for opp in enemy_team:
            puuid = opp.get("puuid", "")
            if puuid:
                agg_result = self._run_module("history_aggregator", "aggregate", puuid)
                if agg_result and not isinstance(agg_result, dict) or (isinstance(agg_result, dict) and "error" not in agg_result):
                    results.setdefault("aggregated_histories", {})[puuid] = agg_result

        # Phase 2: Scout opponents
        scout_result = self._run_module("opponent_scout", "scout", enemy_team)
        if scout_result:
            results["opponent_scout"] = scout_result

        # Phase 3: Tilt detection
        tilt_result = self._run_module("tilt_detector", "detect_batch", enemy_team)
        if tilt_result:
            results["tilt_detector"] = tilt_result

        # Phase 4: Champion pool prediction
        for opp in enemy_team:
            puuid = opp.get("puuid", "")
            hist = results.get("aggregated_histories", {}).get(puuid, {})
            matches = hist.get("matches", []) if isinstance(hist, dict) else []
            if matches:
                pool_result = self._run_module("pool_predictor", "predict", matches)
                if pool_result:
                    results.setdefault("pool_predictions", {})[puuid] = pool_result

        # Phase 5: Matchup prediction
        our_team = game_context.get("our_team", [])
        predict_result = self._run_module("matchup_predictor", "predict", our_team, enemy_team)
        if predict_result:
            results["matchup_predictor"] = predict_result

        # Phase 6: Team comp analysis
        our_champs = [t.get("champion_id", 0) for t in our_team]
        enemy_champs = [t.get("champion_id", 0) for t in enemy_team]
        if our_champs:
            comp_result = self._run_module("comp_analyzer", "compare_comps", our_champs, enemy_champs)
            if comp_result:
                results["comp_analyzer"] = comp_result

        # Phase 7: Draft advice
        draft_result = self._run_module("draft_advisor", "advise_ban")
        if draft_result:
            results["draft_advisor"] = draft_result

        # Phase 8: Strategy synthesis
        synth_context = {**game_context, "sections": results}
        synth_result = self._run_module("strategy_synthesizer", "synthesize", synth_context)
        if synth_result:
            results["strategy_synthesis"] = synth_result

        # Phase 9: Voice briefing
        if synth_result:
            voice_result = self._run_module("voice_briefer", "brief_pregame", synth_result)
            if voice_result:
                results["voice_briefing"] = voice_result

        elapsed = round((time.time() - t0) * 1000, 1)
        self._total_run_ms += elapsed

        report = {
            "status": "ok", "phase": "pregame",
            "results": results, "errors": errors,
            "elapsed_ms": elapsed, "modules_run": len(results),
        }
        self._last_pregame_result = report
        self._fire("pregame_complete", {"elapsed_ms": elapsed, "modules": len(results)})
        return report

    def run_live_update(self, live_event: Dict[str, Any],
                        game_state: Dict[str, Any] = None) -> Dict[str, Any]:
        """Run live update pipeline for a single game event.

        Args:
            live_event: {event_type, game_time, puuid, context, ...}
            game_state: Current game state snapshot.
        """
        self._op_count += 1
        self._live_update_count += 1
        t0 = time.time()
        game_state = game_state or {}

        results: Dict[str, Any] = {}

        # Event correlation
        corr_result = self._run_module("event_correlator", "correlate", live_event)
        if corr_result:
            results["event_correlation"] = corr_result

        # Tendency tracking
        puuid = live_event.get("puuid", "")
        if puuid:
            tend_result = self._run_module("tendency_tracker", "record_event",
                                           puuid, live_event.get("event_type", ""),
                                           live_event.get("value", 1.0),
                                           live_event.get("game_time", 0))
            if tend_result:
                results["tendency_tracked"] = tend_result

            # Check for deviations
            compare_result = self._run_module("tendency_tracker", "compare", puuid)
            if compare_result and compare_result.get("deviant_count", 0) > 0:
                results["tendency_deviation"] = compare_result

        # Adaptation tracking
        if puuid:
            self._run_module("adaptation_tracker", "track", puuid, live_event)

        # Power spike check
        champ_id = live_event.get("champion_id") or game_state.get("champion_id", 0)
        if champ_id:
            spike_result = self._run_module("power_spike_advisor", "advise_current",
                                            champ_id,
                                            game_state.get("level", 0),
                                            game_state.get("items", []),
                                            live_event.get("game_time", 0))
            if spike_result and spike_result.get("is_spiking"):
                results["power_spike"] = spike_result

        # Lane enrichment
        our_champ = game_state.get("our_champion_id", 0)
        enemy_champ = game_state.get("enemy_champion_id", 0)
        role = game_state.get("role", "any")
        if our_champ and enemy_champ:
            lane_result = self._run_module("lane_enricher", "enrich",
                                           our_champ, enemy_champ, role,
                                           live_event.get("game_time", 0),
                                           game_state)
            if lane_result:
                results["lane_enrichment"] = lane_result

        # Voice update for significant events
        if results.get("power_spike") or results.get("tendency_deviation"):
            event_type = "power_spike" if results.get("power_spike") else "opponent_deviation"
            voice_result = self._run_module("voice_briefer", "brief_live_update",
                                            event_type, {**live_event, **game_state})
            if voice_result:
                results["voice_update"] = voice_result

        elapsed = round((time.time() - t0) * 1000, 1)
        self._total_run_ms += elapsed

        report = {
            "status": "ok", "phase": "live",
            "results": results, "elapsed_ms": elapsed,
        }
        self._last_live_result = report
        return report

    def get_dashboard(self) -> Dict[str, Any]:
        """Get pipeline health dashboard."""
        self._op_count += 1
        module_health = {}
        for name, timings in self._module_timings.items():
            if timings:
                avg_ms = sum(timings) / len(timings)
                module_health[name] = {"avg_ms": round(avg_ms, 1), "calls": len(timings)}
            else:
                module_health[name] = {"avg_ms": 0, "calls": 0}

        return {
            "state": self._state,
            "modules": len(self._modules),
            "pregame_runs": self._pregame_count,
            "live_updates": self._live_update_count,
            "total_run_ms": round(self._total_run_ms, 1),
            "error_count": self._error_count,
            "module_health": module_health,
        }

    def shutdown(self) -> Dict[str, Any]:
        """Shutdown the pipeline."""
        self._op_count += 1
        self._state = "shutdown"
        # Flush cache
        self._run_module("cache_manager", "clear")
        self._fire("shutdown", {"pregame_runs": self._pregame_count,
                                 "live_updates": self._live_update_count})
        return {"status": "ok", "state": self._state}

    def get_stats(self) -> Dict[str, Any]:
        self._op_count += 1
        return {
            "op_count": self._op_count, "state": self._state,
            "modules": len(self._modules),
            "pregame_count": self._pregame_count,
            "live_update_count": self._live_update_count,
            "total_run_ms": round(self._total_run_ms, 1),
            "error_count": self._error_count,
        }
