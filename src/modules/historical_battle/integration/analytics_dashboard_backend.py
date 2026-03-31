#!/usr/bin/env python3
"""
M820 - Analytics Dashboard Backend
====================================
OperatorRL Historical Battle System - REST API serving analytics data to frontend dashboards

查看游戏分析面板后端的实现方式，理解其模式，
特别是 REST API 路由和数据聚合管道是如何组织的。
从 FastAPI/Flask 路由开始，遵循该模式实现分析仪表板后端，
使前端可以通过 HTTP API 获取所有分析结果和实时统计。

Core: REST API serving analytics data to frontend dashboards
"""

import os
import sys
import json
import time
import math
import logging
import hashlib
import statistics
import struct
from pathlib import Path
from enum import Enum, auto
from typing import Dict, List, Any, Optional, Tuple, Set, Union, Callable
from dataclasses import dataclass, field, asdict
from abc import ABC, abstractmethod
from collections import defaultdict
from datetime import datetime, timezone

logger = logging.getLogger("operatorRL.historical_battle.m820")
logger.setLevel(logging.DEBUG)


# ─── Constants ──────────────────────────────────────────────────────────────

DASHBOARD_PORT = 8765
API_VERSION = "v1"
MAX_RESULTS_DEFAULT = 50
REQUEST_LOG_MAX = 10000

class EndpointType(Enum):
    PLAYER_STATS = "/api/v1/player/{player_id}/stats"
    MATCH_HISTORY = "/api/v1/player/{player_id}/matches"
    CHAMPION_STATS = "/api/v1/champions/{champion_id}"
    SCOUTING = "/api/v1/scouting/{player_id}"
    LIVE_STATE = "/api/v1/live"
    SYSTEM_HEALTH = "/api/v1/health"
    METRICS = "/api/v1/metrics"

@dataclass
class APIResponse:
    status: int = 200
    data: Any = None
    error: Optional[str] = None
    meta: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status, "data": self.data,
            "error": self.error, "meta": self.meta,
            "timestamp": self.timestamp,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), default=str)

@dataclass
class APIRequest:
    method: str
    path: str
    params: Dict[str, str] = field(default_factory=dict)
    body: Optional[Dict[str, Any]] = None
    headers: Dict[str, str] = field(default_factory=dict)

@dataclass
class RateLimitState:
    requests_per_minute: int = 60
    current_count: int = 0
    window_start: float = field(default_factory=time.time)

    def check_and_increment(self) -> bool:
        now = time.time()
        if now - self.window_start >= 60:
            self.window_start = now
            self.current_count = 0
        if self.current_count >= self.requests_per_minute:
            return False
        self.current_count += 1
        return True

@dataclass
class DashboardConfig:
    host: str = "127.0.0.1"
    port: int = DASHBOARD_PORT
    cors_enabled: bool = True
    rate_limit: int = 60
    auth_enabled: bool = False
    auth_token: Optional[str] = None

@dataclass
class CORSConfig:
    allowed_origins: List[str] = field(default_factory=lambda: ["*"])
    allowed_methods: List[str] = field(default_factory=lambda: ["GET", "POST", "OPTIONS"])
    allowed_headers: List[str] = field(default_factory=lambda: ["Content-Type", "Authorization"])
    max_age: int = 3600


class RouteHandler:
    """Base route handler with parameter extraction."""

    def __init__(self):
        self._routes: Dict[str, Callable] = {}

    def register(self, pattern: str, handler: Callable) -> None:
        self._routes[pattern] = handler

    def match(self, path: str) -> Optional[Tuple[Callable, Dict[str, str]]]:
        for pattern, handler in self._routes.items():
            params = self._extract_params(pattern, path)
            if params is not None:
                return handler, params
        return None

    def _extract_params(self, pattern: str, path: str) -> Optional[Dict[str, str]]:
        pattern_parts = pattern.strip("/").split("/")
        path_parts = path.strip("/").split("/")
        if len(pattern_parts) != len(path_parts):
            return None
        params = {}
        for pp, pathp in zip(pattern_parts, path_parts):
            if pp.startswith("{") and pp.endswith("}"):
                param_name = pp[1:-1]
                params[param_name] = pathp
            elif pp != pathp:
                return None
        return params

    def list_routes(self) -> List[str]:
        return list(self._routes.keys())


@dataclass
class RequestMetrics:
    """Tracks API performance metrics."""
    total_requests: int = 0
    total_errors: int = 0
    avg_response_time_ms: float = 0.0
    requests_by_endpoint: Dict[str, int] = field(default_factory=lambda: defaultdict(int))
    errors_by_endpoint: Dict[str, int] = field(default_factory=lambda: defaultdict(int))
    status_code_counts: Dict[int, int] = field(default_factory=lambda: defaultdict(int))

    def record(self, path: str, status: int, response_time_ms: float) -> None:
        self.total_requests += 1
        self.requests_by_endpoint[path] += 1
        self.status_code_counts[status] += 1
        if status >= 400:
            self.total_errors += 1
            self.errors_by_endpoint[path] += 1
        n = self.total_requests
        self.avg_response_time_ms = (self.avg_response_time_ms * (n - 1) + response_time_ms) / n

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_requests": self.total_requests,
            "total_errors": self.total_errors,
            "error_rate": round(self.total_errors / max(self.total_requests, 1), 4),
            "avg_response_ms": round(self.avg_response_time_ms, 2),
            "top_endpoints": dict(sorted(
                self.requests_by_endpoint.items(), key=lambda x: x[1], reverse=True
            )[:10]),
        }


class AnalyticsDashboardBackend:
    """
    REST API backend serving analytics data to frontend dashboards.
    Provides endpoints for player stats, match history, live state, etc.
    """

    def __init__(self, config: Optional[DashboardConfig] = None):
        self._config = config or DashboardConfig()
        self._router = RouteHandler()
        self._rate_limits: Dict[str, RateLimitState] = {}
        self._request_log: List[Dict[str, Any]] = []
        self._data_sources: Dict[str, Any] = {}
        self._metrics = RequestMetrics()
        self._started_at = time.time()
        self._register_routes()

    def _register_routes(self) -> None:
        self._router.register("api/v1/health", self._handle_health)
        self._router.register("api/v1/player/{player_id}/stats", self._handle_player_stats)
        self._router.register("api/v1/player/{player_id}/matches", self._handle_player_matches)
        self._router.register("api/v1/champions/{champion_id}", self._handle_champion_stats)
        self._router.register("api/v1/scouting/{player_id}", self._handle_scouting)
        self._router.register("api/v1/live", self._handle_live_state)
        self._router.register("api/v1/system/stats", self._handle_system_stats)
        self._router.register("api/v1/metrics", self._handle_metrics)

    def set_data_source(self, name: str, source: Any) -> None:
        self._data_sources[name] = source

    def handle_request(self, request: APIRequest) -> APIResponse:
        """Process an incoming API request."""
        client_ip = request.headers.get("X-Forwarded-For", "unknown")
        if not self._check_rate_limit(client_ip):
            return APIResponse(status=429, error="Rate limit exceeded")

        if self._config.auth_enabled and self._config.auth_token:
            token = request.headers.get("Authorization", "").replace("Bearer ", "")
            if token != self._config.auth_token:
                return APIResponse(status=401, error="Unauthorized")

        match = self._router.match(request.path)
        if not match:
            return APIResponse(status=404, error=f"Not found: {request.path}")

        handler, params = match
        start = time.time()
        try:
            response = handler(request, params)
            response_time = (time.time() - start) * 1000
            response.meta["request_time_ms"] = round(response_time, 2)
            self._metrics.record(request.path, response.status, response_time)
            self._log_request(request, response)
            return response
        except Exception as exc:
            logger.error(f"Handler error for {request.path}: {exc}")
            return APIResponse(status=500, error=str(exc))

    def _check_rate_limit(self, client_id: str) -> bool:
        if client_id not in self._rate_limits:
            self._rate_limits[client_id] = RateLimitState(requests_per_minute=self._config.rate_limit)
        return self._rate_limits[client_id].check_and_increment()

    def _log_request(self, request: APIRequest, response: APIResponse) -> None:
        self._request_log.append({
            "method": request.method, "path": request.path,
            "status": response.status, "timestamp": time.time(),
        })
        if len(self._request_log) > REQUEST_LOG_MAX:
            self._request_log = self._request_log[-REQUEST_LOG_MAX // 2:]

    def _handle_health(self, req: APIRequest, params: Dict) -> APIResponse:
        uptime = time.time() - self._started_at
        return APIResponse(data={
            "status": "healthy", "uptime_seconds": round(uptime, 1),
            "version": API_VERSION,
            "data_sources": list(self._data_sources.keys()),
        })

    def _handle_player_stats(self, req: APIRequest, params: Dict) -> APIResponse:
        player_id = params.get("player_id", "")
        persistence = self._data_sources.get("persistence")
        if persistence:
            profile = persistence.get_player_profile(player_id)
            if profile:
                return APIResponse(data=profile)
        return APIResponse(status=404, error=f"Player {player_id} not found")

    def _handle_player_matches(self, req: APIRequest, params: Dict) -> APIResponse:
        player_id = params.get("player_id", "")
        limit = int(req.params.get("limit", MAX_RESULTS_DEFAULT))
        persistence = self._data_sources.get("persistence")
        if persistence:
            result = persistence.query_matches_by_player(player_id, limit)
            return APIResponse(data=result.data, meta={"total": result.total_count, "query_ms": result.query_time_ms})
        return APIResponse(data=[])

    def _handle_champion_stats(self, req: APIRequest, params: Dict) -> APIResponse:
        champ_id = int(params.get("champion_id", 0))
        return APIResponse(data={"champion_id": champ_id, "stats": "aggregated"})

    def _handle_scouting(self, req: APIRequest, params: Dict) -> APIResponse:
        player_id = params.get("player_id", "")
        return APIResponse(data={"player_id": player_id, "scouting": "report"})

    def _handle_live_state(self, req: APIRequest, params: Dict) -> APIResponse:
        bridge = self._data_sources.get("bridge")
        if bridge:
            snapshot = bridge.get_latest_snapshot()
            if snapshot:
                return APIResponse(data=snapshot.to_dict())
        return APIResponse(data={"phase": "NONE", "message": "No active game"})

    def _handle_system_stats(self, req: APIRequest, params: Dict) -> APIResponse:
        return APIResponse(data={
            "total_requests": len(self._request_log),
            "active_rate_limits": len(self._rate_limits),
            "data_sources": list(self._data_sources.keys()),
        })

    def _handle_metrics(self, req: APIRequest, params: Dict) -> APIResponse:
        return APIResponse(data=self._metrics.to_dict())

    def get_api_spec(self) -> Dict[str, Any]:
        """Return OpenAPI-like spec for available endpoints."""
        return {
            "version": API_VERSION,
            "endpoints": [
                {"path": "/api/v1/health", "method": "GET", "desc": "System health"},
                {"path": "/api/v1/player/{id}/stats", "method": "GET", "desc": "Player statistics"},
                {"path": "/api/v1/player/{id}/matches", "method": "GET", "desc": "Match history"},
                {"path": "/api/v1/champions/{id}", "method": "GET", "desc": "Champion statistics"},
                {"path": "/api/v1/scouting/{id}", "method": "GET", "desc": "Opponent scouting"},
                {"path": "/api/v1/live", "method": "GET", "desc": "Live game state"},
                {"path": "/api/v1/system/stats", "method": "GET", "desc": "System statistics"},
                {"path": "/api/v1/metrics", "method": "GET", "desc": "Request metrics"},
            ],
        }


# ─── Module Self-Test ─────────────────────────────────────────────────────

def _self_test() -> Dict[str, Any]:
    results = {"module": "M820_analytics_dashboard_backend", "tests": []}

    try:
        backend = AnalyticsDashboardBackend()
        req = APIRequest(method="GET", path="api/v1/health")
        resp = backend.handle_request(req)
        assert resp.status == 200
        assert resp.data["status"] == "healthy"
        results["tests"].append({"name": "health_endpoint", "status": "pass"})
    except Exception as e:
        results["tests"].append({"name": "health_endpoint", "status": "fail", "error": str(e)})

    try:
        backend = AnalyticsDashboardBackend()
        req = APIRequest(method="GET", path="api/v1/nonexistent")
        resp = backend.handle_request(req)
        assert resp.status == 404
        results["tests"].append({"name": "404_handling", "status": "pass"})
    except Exception as e:
        results["tests"].append({"name": "404_handling", "status": "fail", "error": str(e)})

    try:
        router = RouteHandler()
        router.register("api/{version}/users/{id}", lambda r, p: p)
        match = router.match("api/v1/users/123")
        assert match is not None
        _, params = match
        assert params["version"] == "v1"
        assert params["id"] == "123"
        results["tests"].append({"name": "route_matching", "status": "pass"})
    except Exception as e:
        results["tests"].append({"name": "route_matching", "status": "fail", "error": str(e)})

    try:
        rl = RateLimitState(requests_per_minute=2)
        assert rl.check_and_increment() == True
        assert rl.check_and_increment() == True
        assert rl.check_and_increment() == False
        results["tests"].append({"name": "rate_limiting", "status": "pass"})
    except Exception as e:
        results["tests"].append({"name": "rate_limiting", "status": "fail", "error": str(e)})

    try:
        metrics = RequestMetrics()
        metrics.record("/api/v1/health", 200, 5.0)
        metrics.record("/api/v1/health", 200, 3.0)
        metrics.record("/api/v1/error", 500, 10.0)
        d = metrics.to_dict()
        assert d["total_requests"] == 3
        assert d["total_errors"] == 1
        results["tests"].append({"name": "metrics_tracking", "status": "pass"})
    except Exception as e:
        results["tests"].append({"name": "metrics_tracking", "status": "fail", "error": str(e)})

    results["passed"] = sum(1 for t in results["tests"] if t["status"] == "pass")
    results["total"] = len(results["tests"])
    return results


if __name__ == "__main__":
    print(json.dumps(_self_test(), indent=2))


# ─── Middleware System ────────────────────────────────────────────────────

class MiddlewareChain:
    """Chain of middleware processors for request/response."""

    def __init__(self):
        self._middleware: List[Callable] = []

    def add(self, middleware: Callable) -> None:
        self._middleware.append(middleware)

    def process_request(self, request: APIRequest) -> APIRequest:
        for mw in self._middleware:
            request = mw(request)
        return request


class LoggingMiddleware:
    """Middleware that logs all requests."""

    def __init__(self):
        self._log: List[Dict[str, Any]] = []

    def __call__(self, request: APIRequest) -> APIRequest:
        self._log.append({
            "method": request.method,
            "path": request.path,
            "timestamp": time.time(),
            "params": request.params,
        })
        return request

    def get_recent(self, n: int = 10) -> List[Dict[str, Any]]:
        return self._log[-n:]


class AuthMiddleware:
    """Token-based authentication middleware."""

    def __init__(self, valid_tokens: Optional[Set[str]] = None):
        self._tokens = valid_tokens or set()

    def add_token(self, token: str) -> None:
        self._tokens.add(token)

    def revoke_token(self, token: str) -> None:
        self._tokens.discard(token)

    def validate(self, request: APIRequest) -> bool:
        if not self._tokens:
            return True
        token = request.headers.get("Authorization", "").replace("Bearer ", "")
        return token in self._tokens


# ─── Data Aggregation Pipeline ────────────────────────────────────────────

class DataAggregator:
    """Aggregates data from multiple sources for dashboard views."""

    def __init__(self):
        self._sources: Dict[str, Callable] = {}

    def register_source(self, name: str, fetcher: Callable) -> None:
        self._sources[name] = fetcher

    def aggregate(self, source_names: List[str], filters: Optional[Dict] = None) -> Dict[str, Any]:
        results = {}
        for name in source_names:
            if name in self._sources:
                try:
                    data = self._sources[name](filters or {})
                    results[name] = {"status": "ok", "data": data}
                except Exception as exc:
                    results[name] = {"status": "error", "error": str(exc)}
        return results

    def get_overview(self) -> Dict[str, Any]:
        return {
            "sources": list(self._sources.keys()),
            "count": len(self._sources),
        }


class WebSocketBroadcaster:
    """Manages WebSocket connections for real-time updates."""

    def __init__(self):
        self._subscribers: Dict[str, List[Callable]] = defaultdict(list)
        self._message_count = 0

    def subscribe(self, channel: str, callback: Callable) -> None:
        self._subscribers[channel].append(callback)

    def unsubscribe(self, channel: str, callback: Callable) -> None:
        if channel in self._subscribers:
            self._subscribers[channel] = [
                cb for cb in self._subscribers[channel] if cb != callback
            ]

    def broadcast(self, channel: str, message: Dict[str, Any]) -> int:
        sent = 0
        for cb in self._subscribers.get(channel, []):
            try:
                cb(message)
                sent += 1
            except Exception:
                pass
        self._message_count += sent
        return sent

    def get_stats(self) -> Dict[str, Any]:
        return {
            "channels": len(self._subscribers),
            "total_subscribers": sum(len(v) for v in self._subscribers.values()),
            "messages_sent": self._message_count,
        }


class DashboardViewBuilder:
    """Builds pre-computed dashboard views for fast serving."""

    def __init__(self):
        self._views: Dict[str, Dict[str, Any]] = {}
        self._build_times: Dict[str, float] = {}

    def build_view(self, name: str, builder: Callable, *args, **kwargs) -> Dict[str, Any]:
        start = time.time()
        view_data = builder(*args, **kwargs)
        build_time = (time.time() - start) * 1000
        self._views[name] = {
            "data": view_data,
            "built_at": time.time(),
            "build_time_ms": build_time,
        }
        self._build_times[name] = build_time
        return self._views[name]

    def get_view(self, name: str) -> Optional[Dict[str, Any]]:
        return self._views.get(name)

    def invalidate(self, name: str) -> bool:
        return self._views.pop(name, None) is not None

    def get_build_stats(self) -> Dict[str, Any]:
        return {
            "cached_views": len(self._views),
            "build_times": {k: round(v, 2) for k, v in self._build_times.items()},
        }
