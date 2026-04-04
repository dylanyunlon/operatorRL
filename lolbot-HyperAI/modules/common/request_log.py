"""
common/request_log.py — Seraphine-style PastRequest ring buffer
================================================================
lolbot-HyperAI · modules/common

查看 Seraphine connector.py 上现有 PastRequest 日志的实现方式, 理解其模式,
特别是所有请求如何自动记录到环形缓冲区用于调试回放。从 Seraphine 的
PastRequest 类 这个好例子开始。然后遵循该模式实现一个 RequestLog, 让
所有 HTTP/API 调用可以自动记录请求/响应/耗时, 并能按时间窗口或状态码
过滤, 支持 JSON 导出用于离线分析。

功能清单:
1. RequestRecord — 单次请求记录 (method, url, status, latency, body)
2. RequestLog — 线程安全环形缓冲 (max_size, auto-eviction)
3. @log_request — 自动记录装饰器 (同步+异步)
4. RequestFilter — 按状态码/URL/时间过滤
5. export_jsonl — 导出为 JSONL 格式

位置: lolbot-HyperAI/modules/common/request_log.py
"""

from __future__ import annotations

import asyncio
import functools
import json
import logging
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import (
    Any, Callable, Deque, Dict, Iterator, List, Optional,
    Tuple, TypeVar,
)

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])


# ---------------------------------------------------------------------------
# Request record
# ---------------------------------------------------------------------------

@dataclass
class RequestRecord:
    """Single request/response log entry.

    Captures everything needed for debugging and replay:
    - Request: method, url, headers (sanitized), body snippet
    - Response: status_code, body snippet, headers
    - Timing: start_time, latency_ms
    - Error: exception type and message if failed
    """
    request_id: int = 0
    timestamp: float = 0.0
    method: str = "GET"
    url: str = ""
    request_headers: Dict[str, str] = field(default_factory=dict)
    request_body: str = ""
    status_code: int = 0
    response_body: str = ""
    response_headers: Dict[str, str] = field(default_factory=dict)
    latency_ms: float = 0.0
    success: bool = True
    error_type: str = ""
    error_message: str = ""
    source: str = ""
    tags: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Export as dict (for JSON serialization)."""
        return {
            "id": self.request_id,
            "ts": round(self.timestamp, 3),
            "method": self.method,
            "url": self.url,
            "status": self.status_code,
            "latency_ms": round(self.latency_ms, 2),
            "success": self.success,
            "error": self.error_type if not self.success else "",
            "source": self.source,
        }

    def to_detail_dict(self) -> Dict[str, Any]:
        """Export full details (for debugging)."""
        d = asdict(self)
        d["timestamp"] = round(self.timestamp, 3)
        d["latency_ms"] = round(self.latency_ms, 2)
        return d

    @property
    def is_error(self) -> bool:
        return not self.success or self.status_code >= 400

    @property
    def is_slow(self) -> bool:
        """Request took more than 1 second."""
        return self.latency_ms > 1000.0

    def summary(self) -> str:
        """One-line summary for logs."""
        status = self.status_code if self.success else f"ERR:{self.error_type}"
        return (
            f"[{self.source}] {self.method} {self.url} "
            f"-> {status} ({self.latency_ms:.0f}ms)"
        )


# ---------------------------------------------------------------------------
# Sanitization helpers
# ---------------------------------------------------------------------------

_SENSITIVE_HEADERS = frozenset({
    "authorization", "x-riot-token", "cookie",
    "x-api-key", "x-auth-token",
})

_MAX_BODY_SNIPPET = 2048


def _sanitize_headers(
    headers: Optional[Dict[str, str]],
) -> Dict[str, str]:
    """Remove sensitive header values."""
    if not headers:
        return {}
    sanitized: Dict[str, str] = {}
    for k, v in headers.items():
        if k.lower() in _SENSITIVE_HEADERS:
            sanitized[k] = "***"
        else:
            sanitized[k] = v
    return sanitized


def _truncate_body(body: Any) -> str:
    """Truncate body to a snippet for logging."""
    if body is None:
        return ""
    if isinstance(body, bytes):
        try:
            body = body.decode("utf-8", errors="replace")
        except Exception:
            return f"<binary {len(body)} bytes>"
    s = str(body)
    if len(s) > _MAX_BODY_SNIPPET:
        return s[:_MAX_BODY_SNIPPET] + f"... ({len(s)} chars total)"
    return s


# ---------------------------------------------------------------------------
# RequestLog — thread-safe ring buffer
# ---------------------------------------------------------------------------

class RequestLog:
    """Thread-safe ring buffer of RequestRecords.

    Keeps the last `max_size` request records for debugging.
    Auto-evicts oldest entries when full.

    Usage::

        log = RequestLog(max_size=1000, source="lcu")
        log.record("GET", "/liveclientdata/allgamedata",
                    status_code=200, latency_ms=45.2)

        # Query
        errors = log.filter(success=False)
        slow = log.filter(min_latency_ms=500)
        recent = log.recent(10)

        # Export
        log.export_jsonl(Path("debug_log.jsonl"))
    """

    def __init__(
        self,
        max_size: int = 1000,
        source: str = "",
    ) -> None:
        self._max_size = max_size
        self._source = source
        self._buffer: Deque[RequestRecord] = deque(maxlen=max_size)
        self._counter = 0
        self._lock = threading.Lock()
        self._total_logged = 0
        self._total_errors = 0

    def record(
        self,
        method: str = "GET",
        url: str = "",
        status_code: int = 0,
        latency_ms: float = 0.0,
        success: bool = True,
        request_headers: Optional[Dict[str, str]] = None,
        request_body: Any = None,
        response_body: Any = None,
        response_headers: Optional[Dict[str, str]] = None,
        error_type: str = "",
        error_message: str = "",
        source: str = "",
        tags: Optional[Dict[str, str]] = None,
    ) -> RequestRecord:
        """Record a request/response pair.

        Returns the created RequestRecord.
        """
        with self._lock:
            self._counter += 1
            self._total_logged += 1
            if not success or status_code >= 400:
                self._total_errors += 1

            rec = RequestRecord(
                request_id=self._counter,
                timestamp=time.time(),
                method=method,
                url=url,
                request_headers=_sanitize_headers(request_headers),
                request_body=_truncate_body(request_body),
                status_code=status_code,
                response_body=_truncate_body(response_body),
                response_headers=_sanitize_headers(response_headers),
                latency_ms=latency_ms,
                success=success,
                error_type=error_type,
                error_message=str(error_message)[:500] if error_message else "",
                source=source or self._source,
                tags=tags or {},
            )
            self._buffer.append(rec)
            return rec

    def recent(self, count: int = 10) -> List[RequestRecord]:
        """Get most recent N records."""
        with self._lock:
            items = list(self._buffer)
            return items[-count:] if count < len(items) else items

    def filter(
        self,
        success: Optional[bool] = None,
        min_latency_ms: Optional[float] = None,
        max_latency_ms: Optional[float] = None,
        status_code: Optional[int] = None,
        url_contains: Optional[str] = None,
        source: Optional[str] = None,
        since_s: Optional[float] = None,
        limit: int = 100,
    ) -> List[RequestRecord]:
        """Filter records by criteria."""
        now = time.time()
        results: List[RequestRecord] = []

        with self._lock:
            for rec in reversed(self._buffer):
                if len(results) >= limit:
                    break
                if success is not None and rec.success != success:
                    continue
                if min_latency_ms and rec.latency_ms < min_latency_ms:
                    continue
                if max_latency_ms and rec.latency_ms > max_latency_ms:
                    continue
                if status_code and rec.status_code != status_code:
                    continue
                if url_contains and url_contains not in rec.url:
                    continue
                if source and rec.source != source:
                    continue
                if since_s and (now - rec.timestamp) > since_s:
                    continue
                results.append(rec)

        results.reverse()
        return results

    def errors(self, limit: int = 50) -> List[RequestRecord]:
        """Get recent error records."""
        return self.filter(success=False, limit=limit)

    def slow_requests(
        self, threshold_ms: float = 500.0, limit: int = 50,
    ) -> List[RequestRecord]:
        """Get recent slow requests."""
        return self.filter(min_latency_ms=threshold_ms, limit=limit)

    def clear(self) -> None:
        """Clear all records."""
        with self._lock:
            self._buffer.clear()

    def stats(self) -> Dict[str, Any]:
        """Summary statistics."""
        with self._lock:
            records = list(self._buffer)

        if not records:
            return {
                "total_logged": self._total_logged,
                "total_errors": self._total_errors,
                "buffer_size": 0,
                "max_size": self._max_size,
            }

        latencies = [r.latency_ms for r in records]
        error_count = sum(1 for r in records if r.is_error)

        return {
            "total_logged": self._total_logged,
            "total_errors": self._total_errors,
            "buffer_size": len(records),
            "max_size": self._max_size,
            "avg_latency_ms": round(
                sum(latencies) / len(latencies), 2
            ),
            "max_latency_ms": round(max(latencies), 2),
            "error_rate": round(
                error_count / len(records), 3
            ) if records else 0.0,
            "oldest_age_s": round(
                time.time() - records[0].timestamp, 1
            ),
        }

    def export_jsonl(self, path: Path) -> int:
        """Export all records to JSONL file.

        Returns number of records exported.
        """
        with self._lock:
            records = list(self._buffer)

        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            for rec in records:
                f.write(json.dumps(rec.to_dict()) + "\n")
        return len(records)

    def __len__(self) -> int:
        with self._lock:
            return len(self._buffer)

    def __iter__(self) -> Iterator[RequestRecord]:
        with self._lock:
            return iter(list(self._buffer))


# ---------------------------------------------------------------------------
# @log_request decorator
# ---------------------------------------------------------------------------

def log_request(
    request_log: Optional[RequestLog] = None,
    source: str = "",
    log_attr: str = "_request_log",
) -> Callable[[F], F]:
    """Auto-log HTTP requests made by the decorated function.

    The decorator wraps the function call, measuring latency and
    recording the result to a RequestLog instance.

    The RequestLog is resolved in order:
    1. Explicit `request_log` parameter
    2. `self.<log_attr>` attribute (for methods)
    3. Creates a module-level default log

    The decorated function should return a tuple of:
        (url, status_code, response_body) or just the result.

    Usage::

        class LCUConnector:
            def __init__(self):
                self._request_log = RequestLog(source="lcu")

            @log_request(source="lcu")
            def _http_get(self, url):
                resp = urllib.request.urlopen(url)
                return resp.read()
    """
    _default_log = request_log

    def decorator(func: F) -> F:
        is_async = asyncio.iscoroutinefunction(func)

        def _get_log(obj: Any) -> RequestLog:
            if _default_log is not None:
                return _default_log
            if obj is not None and hasattr(obj, log_attr):
                return getattr(obj, log_attr)
            return RequestLog(max_size=100, source=source or "auto")

        if is_async:
            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                obj = args[0] if args else None
                rlog = _get_log(obj)
                url = kwargs.get("url", "")
                if not url and len(args) > 1:
                    url = str(args[1]) if args[1] else ""
                method = kwargs.get("method", "GET")
                start = time.monotonic()
                try:
                    result = await func(*args, **kwargs)
                    latency = (time.monotonic() - start) * 1000
                    rlog.record(
                        method=method, url=url, status_code=200,
                        latency_ms=latency, success=True,
                        source=source,
                    )
                    return result
                except Exception as exc:
                    latency = (time.monotonic() - start) * 1000
                    rlog.record(
                        method=method, url=url, status_code=0,
                        latency_ms=latency, success=False,
                        error_type=type(exc).__name__,
                        error_message=str(exc), source=source,
                    )
                    raise

            return async_wrapper  # type: ignore[return-value]

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            obj = args[0] if args else None
            rlog = _get_log(obj)
            url = kwargs.get("url", "")
            if not url and len(args) > 1:
                url = str(args[1]) if args[1] else ""
            method = kwargs.get("method", "GET")
            start = time.monotonic()
            try:
                result = func(*args, **kwargs)
                latency = (time.monotonic() - start) * 1000
                rlog.record(
                    method=method, url=url, status_code=200,
                    latency_ms=latency, success=True,
                    source=source,
                )
                return result
            except Exception as exc:
                latency = (time.monotonic() - start) * 1000
                rlog.record(
                    method=method, url=url, status_code=0,
                    latency_ms=latency, success=False,
                    error_type=type(exc).__name__,
                    error_message=str(exc), source=source,
                )
                raise

        return sync_wrapper  # type: ignore[return-value]

    return decorator


# ---------------------------------------------------------------------------
# RequestAnalytics — aggregate statistics (Claude11 addition)
# ---------------------------------------------------------------------------

class RequestAnalytics:
    """Compute aggregate stats from a RequestLog."""

    def __init__(self, log: "RequestLog") -> None:
        self._log = log

    def summary(self, window_s: float = 300.0) -> Dict[str, Any]:
        records = self._log.recent(1000)
        now = time.time(); cutoff = now - window_s
        iw = [r for r in records if r.timestamp >= cutoff]
        if not iw:
            return {"window_s": window_s, "total": 0, "success_rate": 0.0, "avg_latency_ms": 0.0}
        total = len(iw); ok = sum(1 for r in iw if r.success)
        lats = [r.latency_ms for r in iw]
        return {
            "window_s": window_s, "total": total,
            "success_rate": round(ok/total, 4),
            "avg_latency_ms": round(sum(lats)/total, 2),
            "p95_latency_ms": round(sorted(lats)[int(len(lats)*0.95)], 2) if lats else 0,
            "errors": total - ok,
        }

    def error_breakdown(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for r in self._log.recent(500):
            if not r.success and r.error_type:
                counts[r.error_type] = counts.get(r.error_type, 0) + 1
        return counts
