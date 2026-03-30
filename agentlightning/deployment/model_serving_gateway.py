"""
Model Serving Gateway — Routes inference requests to model instances.

Provides a unified gateway that routes incoming inference requests to
the appropriate model version based on deployment config, load balancing,
and A/B routing rules.

Location: agentlightning/deployment/model_serving_gateway.py

Reference (拿来主义):
  查看 agentlightning/runner/game_runner.py 上现有 GameRunner 的
  register_game/start_game 方式, 理解其模式, 特别是 launcher注册
  如何与session routing分离。
  从 agentlightning/adapter/base.py 这个好例子开始 — 它的统一适配器
  接口展示了如何将异构后端统一为单一调用方式。
  遵循该模式实现 ModelServingGateway, 让所有推理请求通过统一入口,
  并能根据版本/负载/路由规则分发到正确的模型实例.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "agentlightning.deployment.model_serving_gateway.v1"


class ModelEndpoint:
    """Registered model serving endpoint."""

    __slots__ = (
        "name", "version", "serve_fn", "status",
        "request_count", "error_count", "total_latency_ms",
    )

    def __init__(self, name: str, version: str, serve_fn: Callable) -> None:
        self.name = name
        self.version = version
        self.serve_fn = serve_fn
        self.status: str = "active"
        self.request_count: int = 0
        self.error_count: int = 0
        self.total_latency_ms: float = 0.0

    @property
    def avg_latency_ms(self) -> float:
        if self.request_count == 0:
            return 0.0
        return self.total_latency_ms / self.request_count

    @property
    def error_rate(self) -> float:
        if self.request_count == 0:
            return 0.0
        return self.error_count / self.request_count

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name, "version": self.version,
            "status": self.status, "request_count": self.request_count,
            "error_count": self.error_count,
            "avg_latency_ms": round(self.avg_latency_ms, 3),
            "error_rate": round(self.error_rate, 4),
        }


class ModelServingGateway:
    """Unified model serving gateway.

    Attributes:
        default_version: Default version to route to.
        evolution_callback: Optional callback for evolution events.
    """

    def __init__(self, default_version: str = "latest") -> None:
        self.default_version = default_version
        self._endpoints: Dict[str, ModelEndpoint] = {}
        self._routing_rules: Dict[str, str] = {}  # game → version
        self._stats = {"total_requests": 0, "total_errors": 0}
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def register_endpoint(
        self, name: str, version: str,
        serve_fn: Callable[[Dict[str, Any]], Dict[str, Any]],
    ) -> None:
        key = f"{name}:{version}"
        self._endpoints[key] = ModelEndpoint(name, version, serve_fn)

    def unregister_endpoint(self, name: str, version: str) -> bool:
        key = f"{name}:{version}"
        if key in self._endpoints:
            del self._endpoints[key]
            return True
        return False

    def set_routing_rule(self, game: str, version: str) -> None:
        self._routing_rules[game] = version

    def serve(
        self, request: Dict[str, Any],
        model_name: Optional[str] = None,
        version: Optional[str] = None,
        game: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Serve an inference request.

        Args:
            request: Input data dict.
            model_name: Target model name.
            version: Target version (overrides routing rules).
            game: Game identifier (for routing rule lookup).

        Returns:
            Model output dict.

        Raises:
            KeyError: If no matching endpoint found.
        """
        self._stats["total_requests"] += 1

        # Resolve version
        resolved_version = version
        if resolved_version is None and game in self._routing_rules:
            resolved_version = self._routing_rules[game]
        if resolved_version is None:
            resolved_version = self.default_version

        # Find endpoint
        target_name = model_name or "default"
        key = f"{target_name}:{resolved_version}"
        if key not in self._endpoints:
            # Try any endpoint with matching version
            for k, ep in self._endpoints.items():
                if ep.version == resolved_version and ep.status == "active":
                    key = k
                    break
            else:
                raise KeyError(f"No endpoint for {target_name}:{resolved_version}")

        endpoint = self._endpoints[key]
        start = time.monotonic()
        try:
            result = endpoint.serve_fn(request)
            latency = (time.monotonic() - start) * 1000.0
            endpoint.request_count += 1
            endpoint.total_latency_ms += latency
            result["_served_by"] = key
            result["_latency_ms"] = round(latency, 3)
            return result
        except Exception as exc:
            endpoint.request_count += 1
            endpoint.error_count += 1
            self._stats["total_errors"] += 1
            raise

    def list_endpoints(self) -> List[Dict[str, Any]]:
        return [ep.to_dict() for ep in self._endpoints.values()]

    def get_stats(self) -> Dict[str, Any]:
        return {
            **self._stats,
            "endpoint_count": len(self._endpoints),
            "routing_rules": dict(self._routing_rules),
        }

    def _fire_evolution(self, event_type: str, payload: Dict[str, Any]) -> None:
        if self.evolution_callback is not None:
            try:
                self.evolution_callback({
                    "source": _EVOLUTION_KEY, "type": event_type,
                    "timestamp": time.time(), "payload": payload,
                })
            except Exception as exc:
                logger.warning("Evolution callback error: %s", exc)
