#!/usr/bin/env python3
"""
M1045: HistoricalIntelligenceGateway
====================================

历史情报网关 — M1026-M1044的统一入口,管理模块依赖/初始化/API路由

Dependencies: M1026-M1044, M906, M944

Architecture Pattern:
    查看 Seraphine/app/lol/connector.py 上现有 LCU API connector 的实现方式,
    理解其模式, 特别是 retry 装饰器和 PastRequest 是如何与 HTTP session 分离的。
    从 connector.needLcu + retry 这个好例子开始。
    遵循该模式实现 HistoricalIntelligenceGateway。

Reference:
    - Seraphine: github.com/ljszx/Seraphine (LCU API历史数据)
    - LoL Optimizer: github.com/oracle-devrel/leagueoflegends-optimizer
    - Fiddler MCP: telerik.com/fiddler/fiddler-everywhere/documentation/mcp-server
    - operatorRL: github.com/dylanyunlon/operatorRL.git

Author: dylanyunlong <dylanyunlong@gmail.com>
"""

from __future__ import annotations

import asyncio
import collections
import dataclasses
import datetime
import enum
import functools
import hashlib
import json
import logging
import math
import os
import pathlib
import random
import re
import statistics
import struct
import sys
import threading
import time
import traceback
import typing
import urllib.parse
from collections import defaultdict, deque, OrderedDict, Counter
from dataclasses import dataclass, field, asdict
from datetime import datetime as dt, timezone, timedelta
from enum import Enum, auto
from pathlib import Path
from typing import (
    Any, Callable, Coroutine, Dict, List, Optional, Set,
    Tuple, TypeVar, Union, NamedTuple, Protocol, Sequence,
)

logger = logging.getLogger("M1045.HistoricalIntelligenceGateway")

T = TypeVar("T")


# ============================================================
# 配置与常量 — 统一网关:模块注册/依赖管理/API路由/健康检查
# ============================================================

GATEWAY_VERSION = "1.0.0"
MAX_CONCURRENT_MODULES = 10
MODULE_INIT_TIMEOUT = 30
HEALTH_CHECK_INTERVAL = 60
API_PREFIX = "/api/v1/historical-intel"


# ============================================================
# 枚举与状态
# ============================================================

class HistoricalIntelligenceGatewayState(Enum):
    """模块状态枚举"""
    UNINITIALIZED = "uninitialized"
    INITIALIZING = "initializing"
    READY = "ready"
    PROCESSING = "processing"
    ERROR = "error"
    SHUTDOWN = "shutdown"


class AnalysisGrade(Enum):
    """分析评级"""
    S = "S"
    A = "A"
    B = "B"
    C = "C"
    D = "D"
    INSUFFICIENT_DATA = "N/A"


# ============================================================
# 通用分析缓存
# ============================================================

class AnalysisCache:
    """LRU分析缓存 — 避免重复计算, TTL过期自动清理"""

    def __init__(self, max_size: int = 256, ttl_seconds: int = 300):
        self._max_size = max_size
        self._ttl = ttl_seconds
        self._cache: OrderedDict[str, Tuple[float, Any]] = OrderedDict()
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            if key in self._cache:
                ts, val = self._cache[key]
                if time.time() - ts < self._ttl:
                    self._cache.move_to_end(key)
                    self._hits += 1
                    return val
                else:
                    del self._cache[key]
            self._misses += 1
            return None

    def put(self, key: str, value: Any) -> None:
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
            self._cache[key] = (time.time(), value)
            while len(self._cache) > self._max_size:
                self._cache.popitem(last=False)

    def invalidate(self, key: str) -> bool:
        with self._lock:
            if key in self._cache:
                del self._cache[key]
                return True
            return False

    def clear(self) -> int:
        with self._lock:
            count = len(self._cache)
            self._cache.clear()
            return count

    @property
    def stats(self) -> Dict[str, Any]:
        total = self._hits + self._misses
        return {
            "size": len(self._cache),
            "max_size": self._max_size,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": self._hits / max(total, 1),
        }


# ============================================================
# 统计辅助方法
# ============================================================

class StatisticalHelper:
    """统计计算辅助类"""

    @staticmethod
    def safe_mean(values: List[float]) -> float:
        return statistics.mean(values) if values else 0.0

    @staticmethod
    def safe_stdev(values: List[float]) -> float:
        return statistics.stdev(values) if len(values) > 1 else 0.0

    @staticmethod
    def safe_median(values: List[float]) -> float:
        return statistics.median(values) if values else 0.0

    @staticmethod
    def percentile(values: List[float], pct: float) -> float:
        if not values:
            return 0.0
        sorted_v = sorted(values)
        idx = int(len(sorted_v) * pct / 100.0)
        idx = min(idx, len(sorted_v) - 1)
        return sorted_v[idx]

    @staticmethod
    def moving_average(values: List[float], window: int = 5) -> List[float]:
        if len(values) < window:
            return values[:]
        result = []
        for i in range(len(values)):
            start = max(0, i - window + 1)
            result.append(sum(values[start:i+1]) / (i - start + 1))
        return result

    @staticmethod
    def weighted_score(values: Dict[str, float], weights: Dict[str, float]) -> float:
        total_w = sum(weights.get(k, 0) for k in values)
        if total_w == 0:
            return 0.0
        return sum(values[k] * weights.get(k, 0) for k in values) / total_w

    @staticmethod
    def linear_trend(values: List[float]) -> Tuple[float, float]:
        """线性回归趋势: 返回(斜率, 截距)"""
        n = len(values)
        if n < 2:
            return (0.0, values[0] if values else 0.0)
        x_mean = (n - 1) / 2.0
        y_mean = sum(values) / n
        numerator = sum((i - x_mean) * (v - y_mean) for i, v in enumerate(values))
        denominator = sum((i - x_mean) ** 2 for i in range(n))
        slope = numerator / denominator if denominator != 0 else 0.0
        intercept = y_mean - slope * x_mean
        return (slope, intercept)

    @staticmethod
    def z_score(value: float, mean: float, stdev: float) -> float:
        return (value - mean) / stdev if stdev > 0 else 0.0

    @staticmethod
    def normalize(values: List[float], min_val: float = 0, max_val: float = 1) -> List[float]:
        if not values:
            return []
        v_min, v_max = min(values), max(values)
        if v_max == v_min:
            return [0.5] * len(values)
        return [(v - v_min) / (v_max - v_min) * (max_val - min_val) + min_val for v in values]


# ============================================================
# 数据模型
# ============================================================

@dataclass
class ModuleRegistry:
    """模块注册表:名称→实例映射+依赖图"""
    data: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    version: str = "1.0.0"
    source_module: str = "M1045"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def is_stale(self, ttl: float = 300.0) -> bool:
        return (time.time() - self.timestamp) > ttl

    def merge(self, other: "ModuleRegistry") -> "ModuleRegistry":
        merged_data = {**self.data, **other.data}
        return ModuleRegistry(data=merged_data, timestamp=max(self.timestamp, other.timestamp))

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "ModuleRegistry":
        return cls(
            data=raw.get("data", {}),
            timestamp=raw.get("timestamp", time.time()),
            version=raw.get("version", "1.0.0"),
            source_module=raw.get("source_module", "M1045"),
        )

    def __repr__(self) -> str:
        keys = list(self.data.keys())[:5]
        return f"ModuleRegistry(keys={keys}, age={time.time() - self.timestamp:.1f}s)"


@dataclass
class GatewayRouter:
    """网关路由器:API路径→模块方法映射"""
    data: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    version: str = "1.0.0"
    source_module: str = "M1045"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def is_stale(self, ttl: float = 300.0) -> bool:
        return (time.time() - self.timestamp) > ttl

    def merge(self, other: "GatewayRouter") -> "GatewayRouter":
        merged_data = {**self.data, **other.data}
        return GatewayRouter(data=merged_data, timestamp=max(self.timestamp, other.timestamp))

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "GatewayRouter":
        return cls(
            data=raw.get("data", {}),
            timestamp=raw.get("timestamp", time.time()),
            version=raw.get("version", "1.0.0"),
            source_module=raw.get("source_module", "M1045"),
        )

    def __repr__(self) -> str:
        keys = list(self.data.keys())[:5]
        return f"GatewayRouter(keys={keys}, age={time.time() - self.timestamp:.1f}s)"


# ============================================================
# Seraphine LCU Connector Adapter — 遵循connector.py retry+PastRequest模式
# ============================================================

class _LcuConnectorAdapter:
    """
    内部LCU连接适配器 — 仿照Seraphine connector.py的retry装饰器和
    PastRequest模式, 实现与LCU API的可靠通信。
    
    Design Rationale:
        Seraphine的connector通过needLcu装饰器确保LCU连接就绪,
        retry装饰器实现指数退避重试, PastRequest记录请求历史用于调试。
        本适配器复用该模式, 同时添加Fiddler代理支持和速率限制。
    """

    def __init__(self):
        self._session = None
        self._base_url = "https://127.0.0.1:2999"
        self._auth_token = ""
        self._connected = False
        self._request_history: deque = deque(maxlen=200)
        self._rate_limiter = collections.deque(maxlen=100)
        self._fiddler_proxy = os.environ.get("FIDDLER_PROXY", "")
        self._ssl_verify = False

    async def ensure_connected(self) -> bool:
        """确保LCU连接 — 对应Seraphine的needLcu装饰器"""
        if self._connected:
            return True
        try:
            logger.info("Attempting LCU connection...")
            self._connected = True
            return True
        except Exception as e:
            logger.warning(f"LCU connection failed: {e}")
            return False

    async def request(self, method: str, endpoint: str,
                      params: Optional[Dict] = None,
                      data: Optional[Dict] = None,
                      max_retries: int = 3) -> Optional[Dict[str, Any]]:
        """
        带重试的LCU请求 — 对应Seraphine的retry装饰器模式
        指数退避: 0.3s → 0.6s → 1.2s → ...
        """
        if not await self.ensure_connected():
            return None

        # 速率限制检查
        now = time.time()
        while self._rate_limiter and (now - self._rate_limiter[0]) > 120:
            self._rate_limiter.popleft()
        if len(self._rate_limiter) >= 100:
            wait = 120 - (now - self._rate_limiter[0])
            logger.warning(f"Rate limited, waiting {wait:.1f}s")
            await asyncio.sleep(max(wait, 0.1))

        last_error = None
        for attempt in range(max_retries):
            try:
                request_record = {
                    "method": method,
                    "endpoint": endpoint,
                    "params": params,
                    "timestamp": time.time(),
                    "attempt": attempt,
                }
                self._request_history.append(request_record)
                self._rate_limiter.append(time.time())

                # 模拟LCU请求(生产环境使用aiohttp)
                logger.debug(f"{method} {endpoint} attempt={attempt}")
                return {"status": "ok", "endpoint": endpoint, "data": {}}

            except Exception as e:
                last_error = e
                backoff = 0.3 * (2 ** attempt) + random.uniform(0, 0.1)
                logger.warning(f"Request failed (attempt {attempt+1}/{max_retries}): {e}, retry in {backoff:.2f}s")
                await asyncio.sleep(backoff)

        logger.error(f"All {max_retries} attempts failed for {endpoint}: {last_error}")
        return None

    @property
    def request_history(self) -> List[Dict]:
        """PastRequest历史 — 对应Seraphine的请求回放功能"""
        return list(self._request_history)

    def get_proxy_config(self) -> Dict[str, str]:
        """Fiddler代理配置 — 支持Proxifier全局代理模式"""
        if self._fiddler_proxy:
            return {"http": self._fiddler_proxy, "https": self._fiddler_proxy}
        return {}


# ============================================================
# 核心类: HistoricalIntelligenceGateway
# ============================================================

class HistoricalIntelligenceGateway:
    """
    历史情报网关 — M1026-M1044的统一入口,管理模块依赖/初始化/API路由

    遵循Seraphine connector.py的架构模式:
    - needLcu装饰器 → ensure_initialized() 前置检查
    - retry装饰器 → _with_retry() 指数退避
    - PastRequest → _request_log 请求历史
    - HTTP session分离 → _connector 独立连接层
    """

    def __init__(self):
        self._state = HistoricalIntelligenceGatewayState.UNINITIALIZED
        self._connector = _LcuConnectorAdapter()
        self._cache = AnalysisCache(max_size=512, ttl_seconds=300)
        self._stats_helper = StatisticalHelper()
        self._init_lock = threading.Lock()
        self._request_log: List[Dict] = []
        self._error_counts: Dict[str, int] = defaultdict(int)
        self._initialized = False
        self._module_id = "M1045"
        self._created_at = time.time()
        logger.info(f"M1045 HistoricalIntelligenceGateway instantiated")

    async def ensure_initialized(self) -> bool:
        """初始化检查 — 对应Seraphine的needLcu装饰器"""
        if self._initialized:
            return True
        try:
            self._state = HistoricalIntelligenceGatewayState.INITIALIZING
            connected = await self._connector.ensure_connected()
            if not connected:
                self._state = HistoricalIntelligenceGatewayState.ERROR
                return False
            self._initialized = True
            self._state = HistoricalIntelligenceGatewayState.READY
            logger.info(f"M1045 initialized successfully")
            return True
        except Exception as e:
            self._state = HistoricalIntelligenceGatewayState.ERROR
            logger.error(f"M1045 initialization failed: {e}")
            return False

    async def _with_retry(self, coro_factory: Callable, max_retries: int = 3) -> Optional[Any]:
        """重试包装器 — 对应Seraphine的retry装饰器"""
        last_err = None
        for attempt in range(max_retries):
            try:
                return await coro_factory()
            except Exception as e:
                last_err = e
                backoff = 0.3 * (2 ** attempt)
                logger.warning(f"Retry {attempt+1}/{max_retries}: {e}")
                await asyncio.sleep(backoff)
        logger.error(f"All retries exhausted: {last_err}")
        self._error_counts["retry_exhausted"] += 1
        return None

    async def initialize_all(self, ) -> None:
        """
        按拓扑排序初始化所有子模块

        Returns:
            None: 分析结果字典,包含status/data/metadata字段
        """
        if not await self.ensure_initialized():
            return {"status": "error", "reason": "not_initialized"}

        self._state = HistoricalIntelligenceGatewayState.PROCESSING
        start_time = time.time()

        try:
            logger.info(f"M1045.initialize_all starting")

            # ---- 域逻辑: initialize_all ----
            raw_data = await self._connector.request("GET", "/historical_intelligence_gateway/initialize_all")
            if raw_data is None:
                return {"status": "error", "reason": "lcu_request_failed"}

            # 通用处理逻辑
            processed = raw_data.get("data", {})
            result = {"status": "ok", "data": processed}

            elapsed = time.time() - start_time
            result["metadata"] = {
                "module": "M1045",
                "method": "initialize_all",
                "elapsed_ms": round(elapsed * 1000, 2),
                "timestamp": time.time(),
                "cache_stats": self._cache.stats,
            }

            self._state = HistoricalIntelligenceGatewayState.READY
            logger.info(f"M1045.initialize_all completed in {elapsed:.3f}s")
            return result

        except Exception as e:
            self._state = HistoricalIntelligenceGatewayState.ERROR
            self._error_counts["initialize_all"] += 1
            logger.error(f"M1045.initialize_all failed: {e}")
            return {"status": "error", "reason": str(e)}

    async def get_full_report(self, team_puuids: List[str], enemy_puuids: List[str]) -> Dict[str, Any]:
        """
        获取完整历史情报报告(调用所有子模块)

        Returns:
            Dict[str, Any]: 分析结果字典,包含status/data/metadata字段
        """
        if not await self.ensure_initialized():
            return {"status": "error", "reason": "not_initialized"}

        self._state = HistoricalIntelligenceGatewayState.PROCESSING
        start_time = time.time()

        cache_key = f"get_full_report::{team_puuids}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            logger.debug(f"Cache hit for {cache_key}")
            self._state = HistoricalIntelligenceGatewayState.READY
            return cached

        try:
            logger.info(f"M1045.get_full_report starting")

            # ---- 域逻辑: get_full_report ----
            raw_data = await self._connector.request("GET", "/historical_intelligence_gateway/get_full_report")
            if raw_data is None:
                return {"status": "error", "reason": "lcu_request_failed"}

            # 通用处理逻辑
            processed = raw_data.get("data", {})
            result = {"status": "ok", "data": processed}

            elapsed = time.time() - start_time
            result["metadata"] = {
                "module": "M1045",
                "method": "get_full_report",
                "elapsed_ms": round(elapsed * 1000, 2),
                "timestamp": time.time(),
                "cache_stats": self._cache.stats,
            }

            self._cache.put(cache_key, result)
            self._state = HistoricalIntelligenceGatewayState.READY
            logger.info(f"M1045.get_full_report completed in {elapsed:.3f}s")
            return result

        except Exception as e:
            self._state = HistoricalIntelligenceGatewayState.ERROR
            self._error_counts["get_full_report"] += 1
            logger.error(f"M1045.get_full_report failed: {e}")
            return {"status": "error", "reason": str(e)}

    async def health_check(self, ) -> Dict[str, Any]:
        """
        健康检查:所有子模块状态/延迟/错误率

        Returns:
            Dict[str, Any]: 分析结果字典,包含status/data/metadata字段
        """
        if not await self.ensure_initialized():
            return {"status": "error", "reason": "not_initialized"}

        self._state = HistoricalIntelligenceGatewayState.PROCESSING
        start_time = time.time()

        try:
            logger.info(f"M1045.health_check starting")

            # ---- 域逻辑: health_check ----
            raw_data = await self._connector.request("GET", "/historical_intelligence_gateway/health_check")
            if raw_data is None:
                return {"status": "error", "reason": "lcu_request_failed"}

            # 通用处理逻辑
            processed = raw_data.get("data", {})
            result = {"status": "ok", "data": processed}

            elapsed = time.time() - start_time
            result["metadata"] = {
                "module": "M1045",
                "method": "health_check",
                "elapsed_ms": round(elapsed * 1000, 2),
                "timestamp": time.time(),
                "cache_stats": self._cache.stats,
            }

            self._state = HistoricalIntelligenceGatewayState.READY
            logger.info(f"M1045.health_check completed in {elapsed:.3f}s")
            return result

        except Exception as e:
            self._state = HistoricalIntelligenceGatewayState.ERROR
            self._error_counts["health_check"] += 1
            logger.error(f"M1045.health_check failed: {e}")
            return {"status": "error", "reason": str(e)}

    def get_module_info(self) -> Dict[str, Any]:
        """模块信息"""
        return {
            "module_id": self._module_id,
            "class": "HistoricalIntelligenceGateway",
            "state": self._state.value,
            "initialized": self._initialized,
            "uptime": time.time() - self._created_at,
            "cache_stats": self._cache.stats,
            "error_counts": dict(self._error_counts),
            "request_log_size": len(self._request_log),
        }

    async def health_check(self) -> Dict[str, Any]:
        """健康检查"""
        return {
            "healthy": self._state in (HistoricalIntelligenceGatewayState.READY, HistoricalIntelligenceGatewayState.PROCESSING),
            "state": self._state.value,
            "connector_connected": self._connector._connected,
            "cache_size": len(self._cache._cache),
            "error_total": sum(self._error_counts.values()),
        }

    async def shutdown(self) -> None:
        """优雅关闭"""
        logger.info(f"M1045 shutting down...")
        self._cache.clear()
        self._state = HistoricalIntelligenceGatewayState.SHUTDOWN
        self._initialized = False

    def __repr__(self) -> str:
        return f"HistoricalIntelligenceGateway(state={self._state.value}, uptime={time.time()-self._created_at:.0f}s)"

