"""
common/request_log.py — PastRequest 环形缓冲日志
==================================================

查看 Seraphine connector.py 上现有 PastRequest 环形缓冲的实现方式, 理解
其模式, 特别是请求日志和 HTTP 执行是如何分离的。从 Seraphine PastRequest
模式这个好例子开始。然后遵循该模式实现一个 RequestLog 环形缓冲, 让所有
HTTP 请求可以自动记录 URL/状态码/耗时/响应摘要, 并能导出为 JSONL 用于
离线调试和回放。

位置: lolbot-HyperAI/modules/common/request_log.py
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Request record
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RequestRecord:
    """单条 HTTP 请求记录.

    对应 Seraphine 的 PastRequest, 但增加了:
    - response_summary: 响应前 200 字符 (避免存全量)
    - component: 来源组件名
    - correlation_id: 关联 ID (追踪请求链)

    Attributes:
        timestamp: Unix timestamp.
        method: HTTP 方法.
        url: 完整 URL.
        status_code: HTTP 状态码 (0=未收到响应).
        latency_ms: 请求耗时 (毫秒).
        success: 是否成功.
        error: 错误描述 (成功时为空).
        response_summary: 响应体前 200 字符.
        component: 发起请求的组件名.
        correlation_id: 关联 ID.
        response_size_bytes: 响应体字节数.
    """
    timestamp: float = 0.0
    method: str = "GET"
    url: str = ""
    status_code: int = 0
    latency_ms: float = 0.0
    success: bool = False
    error: str = ""
    response_summary: str = ""
    component: str = ""
    correlation_id: str = ""
    response_size_bytes: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


# ---------------------------------------------------------------------------
# Request log (ring buffer)
# ---------------------------------------------------------------------------

class RequestLog:
    """HTTP 请求环形缓冲日志.

    线程安全. 固定大小, 旧记录自动淘汰.

    Seraphine PastRequest 的增强版:
    - 按 URL 过滤
    - 按时间范围过滤
    - 失败请求单独索引
    - JSONL 导出
    - 延迟直方图统计

    Usage::

        log = RequestLog(max_size=200)

        # 在 HTTP client 中记录:
        record = RequestRecord(
            timestamp=time.time(),
            method="GET",
            url="https://127.0.0.1:2999/liveclientdata/allgamedata",
            status_code=200,
            latency_ms=15.3,
            success=True,
            component="canbus",
        )
        log.append(record)

        # 查询:
        recent = log.recent(10)
        failures = log.failures()
        slow = log.slow_requests(threshold_ms=100)
    """

    def __init__(self, max_size: int = 200) -> None:
        self._max_size = max_size
        self._buffer: Deque[RequestRecord] = deque(maxlen=max_size)
        self._lock = threading.Lock()

        # 统计
        self._total_count: int = 0
        self._success_count: int = 0
        self._failure_count: int = 0
        self._total_latency_ms: float = 0.0
        self._max_latency_ms: float = 0.0

    def append(self, record: RequestRecord) -> None:
        """添加一条请求记录."""
        with self._lock:
            self._buffer.append(record)
            self._total_count += 1
            self._total_latency_ms += record.latency_ms
            if record.latency_ms > self._max_latency_ms:
                self._max_latency_ms = record.latency_ms
            if record.success:
                self._success_count += 1
            else:
                self._failure_count += 1

    def record(
        self,
        method: str,
        url: str,
        status_code: int,
        latency_ms: float,
        success: bool,
        error: str = "",
        response_summary: str = "",
        component: str = "",
        correlation_id: str = "",
        response_size_bytes: int = 0,
    ) -> RequestRecord:
        """便捷方法: 创建记录并追加.

        Returns:
            创建的 RequestRecord.
        """
        rec = RequestRecord(
            timestamp=time.time(),
            method=method,
            url=url,
            status_code=status_code,
            latency_ms=latency_ms,
            success=success,
            error=error,
            response_summary=response_summary[:200] if response_summary else "",
            component=component,
            correlation_id=correlation_id,
            response_size_bytes=response_size_bytes,
        )
        self.append(rec)
        return rec

    # ─── Query methods ──────────────────────────────────────────────

    def recent(self, count: int = 10) -> List[RequestRecord]:
        """最近 N 条记录 (最新在前)."""
        with self._lock:
            items = list(self._buffer)
        items.reverse()
        return items[:count]

    def all_records(self) -> List[RequestRecord]:
        """缓冲区内所有记录 (时间序)."""
        with self._lock:
            return list(self._buffer)

    def failures(self, count: int = 50) -> List[RequestRecord]:
        """最近 N 条失败记录."""
        with self._lock:
            items = [r for r in self._buffer if not r.success]
        items.reverse()
        return items[:count]

    def slow_requests(
        self, threshold_ms: float = 100.0, count: int = 20,
    ) -> List[RequestRecord]:
        """延迟超过阈值的请求."""
        with self._lock:
            items = [
                r for r in self._buffer if r.latency_ms >= threshold_ms
            ]
        items.sort(key=lambda r: r.latency_ms, reverse=True)
        return items[:count]

    def by_url(self, url_substring: str) -> List[RequestRecord]:
        """按 URL 子串过滤."""
        with self._lock:
            return [r for r in self._buffer if url_substring in r.url]

    def by_component(self, component: str) -> List[RequestRecord]:
        """按组件名过滤."""
        with self._lock:
            return [r for r in self._buffer if r.component == component]

    def by_time_range(
        self, start_ts: float, end_ts: float,
    ) -> List[RequestRecord]:
        """按时间戳范围过滤."""
        with self._lock:
            return [
                r for r in self._buffer
                if start_ts <= r.timestamp <= end_ts
            ]

    # ─── Statistics ─────────────────────────────────────────────────

    def stats(self) -> Dict[str, Any]:
        """聚合统计."""
        with self._lock:
            buffer_size = len(self._buffer)
        avg = (
            self._total_latency_ms / max(1, self._total_count)
        )
        success_rate = (
            self._success_count / max(1, self._total_count)
        )
        return {
            "total_count": self._total_count,
            "buffer_size": buffer_size,
            "buffer_capacity": self._max_size,
            "success_count": self._success_count,
            "failure_count": self._failure_count,
            "success_rate": round(success_rate, 4),
            "avg_latency_ms": round(avg, 2),
            "max_latency_ms": round(self._max_latency_ms, 2),
        }

    def latency_histogram(
        self, buckets: Optional[List[float]] = None,
    ) -> Dict[str, int]:
        """延迟分布直方图.

        Args:
            buckets: 桶边界列表 (ms). 默认 [10, 50, 100, 200, 500, 1000].

        Returns:
            dict of {"<10ms": count, "10-50ms": count, ...}
        """
        if buckets is None:
            buckets = [10, 50, 100, 200, 500, 1000]

        histogram: Dict[str, int] = {}
        prev = 0.0
        for b in buckets:
            key = f"{int(prev)}-{int(b)}ms"
            histogram[key] = 0
            prev = b
        histogram[f">{int(buckets[-1])}ms"] = 0

        with self._lock:
            for r in self._buffer:
                placed = False
                prev_b = 0.0
                for b in buckets:
                    if r.latency_ms < b:
                        key = f"{int(prev_b)}-{int(b)}ms"
                        histogram[key] += 1
                        placed = True
                        break
                    prev_b = b
                if not placed:
                    histogram[f">{int(buckets[-1])}ms"] += 1

        return histogram

    # ─── Export ──────────────────────────────────────────────────────

    def export_jsonl(self, filepath: Path) -> int:
        """导出为 JSONL 文件.

        Returns:
            导出的记录数.
        """
        records = self.all_records()
        filepath.parent.mkdir(parents=True, exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as f:
            for rec in records:
                f.write(rec.to_json())
                f.write("\n")
        logger.info("导出 %d 条请求记录到 %s", len(records), filepath)
        return len(records)

    def import_jsonl(self, filepath: Path) -> int:
        """从 JSONL 文件导入 (用于回放).

        Returns:
            导入的记录数.
        """
        count = 0
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    rec = RequestRecord(**{
                        k: v for k, v in data.items()
                        if k in RequestRecord.__dataclass_fields__
                    })
                    self.append(rec)
                    count += 1
                except (json.JSONDecodeError, TypeError) as exc:
                    logger.warning("JSONL 行解析失败: %s", exc)

        logger.info("从 %s 导入 %d 条记录", filepath, count)
        return count

    def clear(self) -> None:
        """清空缓冲区 (不重置统计)."""
        with self._lock:
            self._buffer.clear()


# ---------------------------------------------------------------------------
# RequestLogMiddleware — 将 RequestLog 集成到 HTTP client 中
# ---------------------------------------------------------------------------

class RequestLogMiddleware:
    """HTTP 中间件: 自动记录请求到 RequestLog.

    包装一个 HTTP GET 函数, 自动记录结果.

    Usage::

        log = RequestLog()
        middleware = RequestLogMiddleware(log, component="canbus")

        # 原始函数
        def raw_get(url: str) -> Tuple[Optional[dict], int]:
            ...

        # 包装后自动记录
        wrapped = middleware.wrap(raw_get)
        data, status = wrapped("https://127.0.0.1:2999/liveclientdata/allgamedata")
    """

    def __init__(
        self,
        request_log: RequestLog,
        component: str = "",
    ) -> None:
        self._log = request_log
        self._component = component
        self._correlation_counter: int = 0
        self._lock = threading.Lock()

    def _next_correlation_id(self) -> str:
        with self._lock:
            self._correlation_counter += 1
            return f"{self._component}:{self._correlation_counter}"

    def wrap_get(
        self,
        func: Any,
    ) -> Any:
        """包装 HTTP GET 函数.

        期望 func 签名: (url: str, ...) -> Tuple[Optional[dict], int]
        """
        def wrapper(url: str, *args: Any, **kwargs: Any) -> Any:
            cid = self._next_correlation_id()
            t0 = time.monotonic()
            try:
                result = func(url, *args, **kwargs)
                latency = (time.monotonic() - t0) * 1000

                # 解析返回值
                data = None
                status_code = 0
                if isinstance(result, tuple) and len(result) >= 2:
                    data, status_code = result[0], result[1]
                else:
                    data = result

                success = data is not None
                summary = ""
                size = 0
                if data is not None:
                    summary_str = json.dumps(data, ensure_ascii=False)
                    size = len(summary_str.encode("utf-8"))
                    summary = summary_str[:200]

                self._log.record(
                    method="GET", url=url,
                    status_code=status_code, latency_ms=latency,
                    success=success, component=self._component,
                    correlation_id=cid,
                    response_summary=summary,
                    response_size_bytes=size,
                )
                return result

            except Exception as exc:
                latency = (time.monotonic() - t0) * 1000
                self._log.record(
                    method="GET", url=url,
                    status_code=0, latency_ms=latency,
                    success=False,
                    error=f"{type(exc).__name__}: {exc}",
                    component=self._component,
                    correlation_id=cid,
                )
                raise

        return wrapper
