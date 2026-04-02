"""
CyberService — Apollo-style RPC request-response within CyberNode.
====================================================================

Maps Apollo's ``cyber::Service`` / ``cyber::Client`` to Python.
Provides synchronous and async typed request-response communication
between components, complementing the pub-sub model in CyberNode.

Architecture position:
    cyber/service/service.py   ← YOU ARE HERE
    ├─ Used by: modules/monitor/ (health-check RPC)
    ├─ Used by: modules/dreamview/ (dashboard data queries)
    ├─ Used by: launch/dag_launcher.py (component introspection)
    └─ Integrates with: cyber/node/node.py (same process bus)

Apollo reference:
    cyber/service/service.h  — Service<Request, Response>
    cyber/service/client.h   — Client<Request, Response>

Design notes:
    - Typed request/response via Python generics
    - Timeout support with configurable default
    - Thread-safe: multiple clients can call concurrently
    - Global service registry for name-based discovery
    - Built-in latency tracking per service endpoint
    - No external dependencies beyond stdlib
"""

from __future__ import annotations

import logging
import statistics
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import (
    Any, Callable, Deque, Dict, Generic, List,
    Optional, TypeVar,
)

logger = logging.getLogger(__name__)

Req = TypeVar("Req")
Res = TypeVar("Res")

_DEFAULT_TIMEOUT_S: float = 5.0
_MAX_PENDING_REQUESTS: int = 256
_LATENCY_WINDOW: int = 200
_REGISTRY_LOCK = threading.Lock()
_SERVICE_REGISTRY: Dict[str, "ServiceBase"] = {}


class ServiceState(Enum):
    IDLE = auto()
    REGISTERED = auto()
    SERVING = auto()
    CLOSED = auto()


class ServiceError(Exception):
    pass

class ServiceTimeoutError(ServiceError):
    pass

class ServiceNotFoundError(ServiceError):
    pass

class ServiceBusyError(ServiceError):
    pass


@dataclass
class ServiceCallStats:
    """Per-service latency and call-count statistics."""
    _latencies: Deque[float] = field(
        default_factory=lambda: deque(maxlen=_LATENCY_WINDOW)
    )
    total_calls: int = 0
    total_errors: int = 0
    total_timeouts: int = 0

    def record(self, latency_ms: float, success: bool) -> None:
        self._latencies.append(latency_ms)
        self.total_calls += 1
        if not success:
            self.total_errors += 1

    def record_timeout(self) -> None:
        self.total_timeouts += 1
        self.total_calls += 1

    @property
    def mean_ms(self) -> float:
        return statistics.mean(self._latencies) if self._latencies else 0.0

    @property
    def p95_ms(self) -> float:
        if len(self._latencies) < 20:
            return max(self._latencies) if self._latencies else 0.0
        s = sorted(self._latencies)
        return s[int(len(s) * 0.95)]

    def snapshot(self) -> Dict[str, Any]:
        return {
            "total_calls": self.total_calls,
            "total_errors": self.total_errors,
            "total_timeouts": self.total_timeouts,
            "mean_ms": round(self.mean_ms, 2),
            "p95_ms": round(self.p95_ms, 2),
        }


class ServiceBase:
    """Base class for services. Use ``Service`` or ``create_service``."""

    def __init__(self, name: str) -> None:
        self._name = name
        self._state = ServiceState.IDLE
        self._stats = ServiceCallStats()

    @property
    def name(self) -> str:
        return self._name

    @property
    def state(self) -> ServiceState:
        return self._state

    @property
    def stats(self) -> ServiceCallStats:
        return self._stats

    def status(self) -> Dict[str, Any]:
        return {"name": self._name, "state": self._state.name,
                **self._stats.snapshot()}


class Service(ServiceBase, Generic[Req, Res]):
    """Named RPC service that handles typed requests and returns responses.

    Usage (server side)::

        def handle_health(request: dict) -> dict:
            return {"status": "ok", "uptime": get_uptime()}

        svc = Service("health_check", handler=handle_health)
        svc.register()
        svc.start()

    Usage (client side)::

        client = ServiceClient("health_check")
        response = client.call({"component": "perception"})

    Thread safety: handler is serialized via a lock to prevent
    concurrent handler execution within a single service.
    """

    def __init__(
        self,
        name: str,
        handler: Optional[Callable[[Req], Res]] = None,
        max_pending: int = _MAX_PENDING_REQUESTS,
    ) -> None:
        super().__init__(name)
        self._handler = handler
        self._max_pending = max_pending
        self._handler_lock = threading.Lock()
        self._pending_count: int = 0

    def set_handler(self, handler: Callable[[Req], Res]) -> None:
        self._handler = handler

    def register(self) -> None:
        with _REGISTRY_LOCK:
            if self._name in _SERVICE_REGISTRY:
                logger.warning("Service '%s' already registered, replacing",
                               self._name)
            _SERVICE_REGISTRY[self._name] = self
            self._state = ServiceState.REGISTERED
            logger.info("Service registered: %s", self._name)

    def unregister(self) -> None:
        with _REGISTRY_LOCK:
            _SERVICE_REGISTRY.pop(self._name, None)
            self._state = ServiceState.CLOSED

    def start(self) -> None:
        if self._handler is None:
            raise ServiceError(f"Service '{self._name}' has no handler set")
        self._state = ServiceState.SERVING

    def stop(self) -> None:
        self._state = ServiceState.REGISTERED

    def handle_request(self, request: Req) -> Res:
        """Process a request synchronously (called by ServiceClient).

        Raises:
            ServiceError: If the service is not in SERVING state.
            ServiceBusyError: If too many concurrent requests.
        """
        if self._state != ServiceState.SERVING:
            raise ServiceError(
                f"Service '{self._name}' not serving (state={self._state.name})"
            )
        if self._pending_count >= self._max_pending:
            raise ServiceBusyError(
                f"Service '{self._name}' has {self._pending_count} pending"
            )

        self._pending_count += 1
        start = time.monotonic()
        try:
            with self._handler_lock:
                result = self._handler(request)
            elapsed_ms = (time.monotonic() - start) * 1000
            self._stats.record(elapsed_ms, True)
            return result
        except ServiceError:
            raise
        except Exception as exc:
            elapsed_ms = (time.monotonic() - start) * 1000
            self._stats.record(elapsed_ms, False)
            raise ServiceError(
                f"Handler error in '{self._name}': {exc}"
            ) from exc
        finally:
            self._pending_count -= 1

    def status(self) -> Dict[str, Any]:
        return {
            "name": self._name,
            "state": self._state.name,
            "pending_count": self._pending_count,
            **self._stats.snapshot(),
        }


class ServiceClient(Generic[Req, Res]):
    """Client for calling a named RPC service.

    Usage::

        client = ServiceClient("health_check")
        try:
            result = client.call({"check": "all"}, timeout=2.0)
        except ServiceTimeoutError:
            handle_timeout()
    """

    def __init__(
        self,
        service_name: str,
        default_timeout: float = _DEFAULT_TIMEOUT_S,
    ) -> None:
        self._service_name = service_name
        self._default_timeout = default_timeout
        self._stats = ServiceCallStats()

    def call(self, request: Req, timeout: Optional[float] = None) -> Res:
        """Synchronous call with timeout enforcement.

        Raises:
            ServiceNotFoundError, ServiceTimeoutError, ServiceError
        """
        effective_timeout = timeout or self._default_timeout

        with _REGISTRY_LOCK:
            service = _SERVICE_REGISTRY.get(self._service_name)
        if service is None:
            raise ServiceNotFoundError(
                f"Service '{self._service_name}' not found"
            )
        if not isinstance(service, Service):
            raise ServiceError(
                f"'{self._service_name}' is not a Service instance"
            )

        result_holder: List[Any] = [None]
        error_holder: List[Optional[Exception]] = [None]
        finished = threading.Event()

        def _invoke():
            try:
                result_holder[0] = service.handle_request(request)
            except Exception as exc:
                error_holder[0] = exc
            finally:
                finished.set()

        t = threading.Thread(target=_invoke, daemon=True,
                             name=f"svc-call-{self._service_name}")
        t.start()

        start = time.monotonic()
        if not finished.wait(timeout=effective_timeout):
            self._stats.record_timeout()
            raise ServiceTimeoutError(
                f"'{self._service_name}' timed out after {effective_timeout}s"
            )

        elapsed_ms = (time.monotonic() - start) * 1000
        if error_holder[0] is not None:
            self._stats.record(elapsed_ms, False)
            raise error_holder[0]

        self._stats.record(elapsed_ms, True)
        return result_holder[0]

    def call_async(
        self,
        request: Req,
        callback: Callable[[Optional[Res], Optional[Exception]], None],
        timeout: Optional[float] = None,
    ) -> str:
        """Non-blocking call; ``callback(response, error)`` on completion."""
        req_id = uuid.uuid4().hex[:8]

        def _worker():
            try:
                resp = self.call(request, timeout=timeout)
                callback(resp, None)
            except Exception as exc:
                callback(None, exc)

        threading.Thread(target=_worker, daemon=True,
                         name=f"svc-async-{req_id}").start()
        return req_id

    @property
    def service_name(self) -> str:
        return self._service_name

    def stats_dict(self) -> Dict[str, Any]:
        return {"service_name": self._service_name,
                **self._stats.snapshot()}


# ─── Registry helpers ────────────────────────────────────────────────────────

def list_services() -> List[Dict[str, Any]]:
    with _REGISTRY_LOCK:
        return [svc.status() for svc in _SERVICE_REGISTRY.values()]

def get_service(name: str) -> Optional[ServiceBase]:
    with _REGISTRY_LOCK:
        return _SERVICE_REGISTRY.get(name)

def clear_registry() -> int:
    with _REGISTRY_LOCK:
        count = len(_SERVICE_REGISTRY)
        _SERVICE_REGISTRY.clear()
        return count

def create_service(
    name: str,
    handler: Callable[[Any], Any],
    auto_start: bool = True,
) -> Service:
    """Convenience: create, register, and optionally start a service."""
    svc = Service(name, handler=handler)
    svc.register()
    if auto_start:
        svc.start()
    return svc
