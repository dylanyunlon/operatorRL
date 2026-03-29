"""
Seraphine Integration Orchestrator — Unified orchestration of all Seraphine modules.
Location: integrations/lol/src/lol_agent/seraphine_integration_orchestrator.py
Reference: Seraphine/app/view/main_window.py module lifecycle + lol_agent_orchestrator.py pattern
"""
from __future__ import annotations
import logging, time
from typing import Any, Callable, Optional
logger = logging.getLogger(__name__)
_EVOLUTION_KEY: str = "integrations.lol.seraphine_integration_orchestrator.v1"

class SeraphineIntegrationOrchestrator:
    def __init__(self) -> None:
        self._modules: dict[str, Any] = {}
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def register_module(self, name: str, module: Any) -> None:
        self._modules[name] = module

    def unregister_module(self, name: str) -> None:
        self._modules.pop(name, None)

    def list_modules(self) -> list[str]:
        return sorted(self._modules.keys())

    def health_check(self) -> dict[str, dict[str, Any]]:
        report: dict[str, dict[str, Any]] = {}
        for name, mod in self._modules.items():
            try:
                if hasattr(mod, "health_check"):
                    report[name] = mod.health_check()
                else:
                    report[name] = {"status": "ok", "note": "no health_check method"}
            except Exception as exc:
                report[name] = {"status": "error", "error": str(exc)}
        return report

    def run_pregame(self, opponents: list[dict[str, Any]] | None = None, my_champion: str = "") -> dict[str, Any]:
        if opponents is None:
            opponents = []
        return {"report": "pregame", "opponents_count": len(opponents),
                "my_champion": my_champion, "modules_used": self.list_modules()}

    def run_ingame(self, game_state: dict[str, Any] | None = None) -> dict[str, Any]:
        if game_state is None:
            game_state = {}
        return {"report": "ingame", "state_keys": list(game_state.keys()), "modules_used": self.list_modules()}

    def run_postgame(self, match_result: dict[str, Any] | None = None) -> dict[str, Any]:
        if match_result is None:
            match_result = {}
        return {"report": "postgame", "result_keys": list(match_result.keys()), "modules_used": self.list_modules()}

    def get_stats(self) -> dict[str, Any]:
        return {"module_count": len(self._modules), "modules": self.list_modules()}

    def _fire_evolution(self, data: dict[str, Any]) -> None:
        if self.evolution_callback is None:
            return
        enriched = {"module": "seraphine_integration_orchestrator", "timestamp": time.time(), **data}
        try:
            self.evolution_callback(enriched)
        except Exception as exc:
            logger.warning("Evolution callback error: %s", exc)
