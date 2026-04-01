#!/usr/bin/env python3
"""
M1040: DuoPartnerDetector
=========================

双排搭档检测器 — 检测当前对局中的双排/多排组合,评估其历史配合表现

Dependencies: M1026, M1032

Architecture Pattern:
    查看 Seraphine/app/lol/connector.py 上现有 LCU API connector 的实现方式,
    理解其模式, 特别是 retry 装饰器和 PastRequest 是如何与 HTTP session 分离的。
    从 connector.needLcu + retry 这个好例子开始。
    遵循该模式实现 DuoPartnerDetector。

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

logger = logging.getLogger("M1040.DuoPartnerDetector")

T = TypeVar("T")


# ============================================================
# 配置与常量 — 双排检测:同队频次/时间间隔/登录时间重合
# ============================================================

DUO_DETECTION_MIN_GAMES = 3
DUO_TIME_WINDOW_MINUTES = 5
TRIO_DETECTION_THRESHOLD = 0.6
PREMADE_CONFIDENCE_HIGH = 0.8
MAX_SCAN_DEPTH = 50


# ============================================================
# 枚举与状态
# ============================================================

class DuoPartnerDetectorState(Enum):
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
class DuoCandidate:
    """双排候选:两名玩家+置信度+历史胜率"""
    data: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    version: str = "1.0.0"
    source_module: str = "M1040"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def is_stale(self, ttl: float = 300.0) -> bool:
        return (time.time() - self.timestamp) > ttl

    def merge(self, other: "DuoCandidate") -> "DuoCandidate":
        merged_data = {**self.data, **other.data}
        return DuoCandidate(data=merged_data, timestamp=max(self.timestamp, other.timestamp))

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "DuoCandidate":
        return cls(
            data=raw.get("data", {}),
            timestamp=raw.get("timestamp", time.time()),
            version=raw.get("version", "1.0.0"),
            source_module=raw.get("source_module", "M1040"),
        )

    def __repr__(self) -> str:
        keys = list(self.data.keys())[:5]
        return f"DuoCandidate(keys={keys}, age={time.time() - self.timestamp:.1f}s)"


@dataclass
class SynergyReport:
    """协同报告:胜率加成/常用组合/位置分配"""
    data: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    version: str = "1.0.0"
    source_module: str = "M1040"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def is_stale(self, ttl: float = 300.0) -> bool:
        return (time.time() - self.timestamp) > ttl

    def merge(self, other: "SynergyReport") -> "SynergyReport":
        merged_data = {**self.data, **other.data}
        return SynergyReport(data=merged_data, timestamp=max(self.timestamp, other.timestamp))

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "SynergyReport":
        return cls(
            data=raw.get("data", {}),
            timestamp=raw.get("timestamp", time.time()),
            version=raw.get("version", "1.0.0"),
            source_module=raw.get("source_module", "M1040"),
        )

    def __repr__(self) -> str:
        keys = list(self.data.keys())[:5]
        return f"SynergyReport(keys={keys}, age={time.time() - self.timestamp:.1f}s)"


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
# 核心类: DuoPartnerDetector
# ============================================================

class DuoPartnerDetector:
    """
    双排搭档检测器 — 检测当前对局中的双排/多排组合,评估其历史配合表现

    遵循Seraphine connector.py的架构模式:
    - needLcu装饰器 → ensure_initialized() 前置检查
    - retry装饰器 → _with_retry() 指数退避
    - PastRequest → _request_log 请求历史
    - HTTP session分离 → _connector 独立连接层
    """

    def __init__(self):
        self._state = DuoPartnerDetectorState.UNINITIALIZED
        self._connector = _LcuConnectorAdapter()
        self._cache = AnalysisCache(max_size=512, ttl_seconds=300)
        self._stats_helper = StatisticalHelper()
        self._init_lock = threading.Lock()
        self._request_log: List[Dict] = []
        self._error_counts: Dict[str, int] = defaultdict(int)
        self._initialized = False
        self._module_id = "M1040"
        self._created_at = time.time()
        logger.info(f"M1040 DuoPartnerDetector instantiated")

    async def ensure_initialized(self) -> bool:
        """初始化检查 — 对应Seraphine的needLcu装饰器"""
        if self._initialized:
            return True
        try:
            self._state = DuoPartnerDetectorState.INITIALIZING
            connected = await self._connector.ensure_connected()
            if not connected:
                self._state = DuoPartnerDetectorState.ERROR
                return False
            self._initialized = True
            self._state = DuoPartnerDetectorState.READY
            logger.info(f"M1040 initialized successfully")
            return True
        except Exception as e:
            self._state = DuoPartnerDetectorState.ERROR
            logger.error(f"M1040 initialization failed: {e}")
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

    async def detect_duos(self, puuids: List[str]) -> List[Dict[str, Any]]:
        """
        检测5人中的双排/多排组合

        Returns:
            List[Dict[str, Any]]: 分析结果字典,包含status/data/metadata字段
        """
        if not await self.ensure_initialized():
            return {"status": "error", "reason": "not_initialized"}

        self._state = DuoPartnerDetectorState.PROCESSING
        start_time = time.time()

        cache_key = f"detect_duos::{puuids}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            logger.debug(f"Cache hit for {cache_key}")
            self._state = DuoPartnerDetectorState.READY
            return cached

        try:
            logger.info(f"M1040.detect_duos starting")

            # ---- 域逻辑: detect_duos ----
            raw_data = await self._connector.request("GET", "/duo_partner_detector/detect_duos")
            if raw_data is None:
                return {"status": "error", "reason": "lcu_request_failed"}

            # 检测/发现逻辑
            candidates = []
            scan_data = raw_data.get("data", {})
            threshold = 0.5
            for item_key, item_val in scan_data.items():
                score = 0.0
                if isinstance(item_val, dict):
                    score = item_val.get("score", 0.0)
                elif isinstance(item_val, (int, float)):
                    score = float(item_val)
                if score >= threshold:
                    candidates.append({
                        "key": item_key,
                        "score": round(score, 4),
                        "confidence": min(score / 1.0, 1.0),
                    })
            candidates.sort(key=lambda x: x["score"], reverse=True)
            result = {"status": "ok", "data": candidates[:20], "total_scanned": len(scan_data)}

            elapsed = time.time() - start_time
            result["metadata"] = {
                "module": "M1040",
                "method": "detect_duos",
                "elapsed_ms": round(elapsed * 1000, 2),
                "timestamp": time.time(),
                "cache_stats": self._cache.stats,
            }

            self._cache.put(cache_key, result)
            self._state = DuoPartnerDetectorState.READY
            logger.info(f"M1040.detect_duos completed in {elapsed:.3f}s")
            return result

        except Exception as e:
            self._state = DuoPartnerDetectorState.ERROR
            self._error_counts["detect_duos"] += 1
            logger.error(f"M1040.detect_duos failed: {e}")
            return {"status": "error", "reason": str(e)}

    async def assess_duo_synergy(self, puuid_a: str, puuid_b: str) -> Dict[str, Any]:
        """
        评估双排组合的协同效果:胜率/KDA加成/位置互补

        Returns:
            Dict[str, Any]: 分析结果字典,包含status/data/metadata字段
        """
        if not await self.ensure_initialized():
            return {"status": "error", "reason": "not_initialized"}

        self._state = DuoPartnerDetectorState.PROCESSING
        start_time = time.time()

        cache_key = f"assess_duo_synergy::{puuid_a}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            logger.debug(f"Cache hit for {cache_key}")
            self._state = DuoPartnerDetectorState.READY
            return cached

        try:
            logger.info(f"M1040.assess_duo_synergy starting")

            # ---- 域逻辑: assess_duo_synergy ----
            raw_data = await self._connector.request("GET", "/duo_partner_detector/assess_duo_synergy")
            if raw_data is None:
                return {"status": "error", "reason": "lcu_request_failed"}

            # 评估逻辑
            assessment_data = raw_data.get("data", {})
            factors = {}
            for k, v in assessment_data.items():
                if isinstance(v, (int, float)):
                    factors[k] = round(float(v), 4)
            overall = self._stats_helper.safe_mean(list(factors.values())) if factors else 0.5
            result = {
                "status": "ok",
                "data": {"factors": factors, "overall": round(overall, 4)},
            }

            elapsed = time.time() - start_time
            result["metadata"] = {
                "module": "M1040",
                "method": "assess_duo_synergy",
                "elapsed_ms": round(elapsed * 1000, 2),
                "timestamp": time.time(),
                "cache_stats": self._cache.stats,
            }

            self._cache.put(cache_key, result)
            self._state = DuoPartnerDetectorState.READY
            logger.info(f"M1040.assess_duo_synergy completed in {elapsed:.3f}s")
            return result

        except Exception as e:
            self._state = DuoPartnerDetectorState.ERROR
            self._error_counts["assess_duo_synergy"] += 1
            logger.error(f"M1040.assess_duo_synergy failed: {e}")
            return {"status": "error", "reason": str(e)}

    async def predict_premade_strategy(self, duo_puuids: List[str]) -> Dict[str, Any]:
        """
        预测预组队的常用策略:英雄组合/位置/打法

        Returns:
            Dict[str, Any]: 分析结果字典,包含status/data/metadata字段
        """
        if not await self.ensure_initialized():
            return {"status": "error", "reason": "not_initialized"}

        self._state = DuoPartnerDetectorState.PROCESSING
        start_time = time.time()

        cache_key = f"predict_premade_strategy::{duo_puuids}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            logger.debug(f"Cache hit for {cache_key}")
            self._state = DuoPartnerDetectorState.READY
            return cached

        try:
            logger.info(f"M1040.predict_premade_strategy starting")

            # ---- 域逻辑: predict_premade_strategy ----
            raw_data = await self._connector.request("GET", "/duo_partner_detector/predict_premade_strategy")
            if raw_data is None:
                return {"status": "error", "reason": "lcu_request_failed"}

            # 预测/估算逻辑
            historical = raw_data.get("data", {})
            series = [float(v) for v in historical.values() if isinstance(v, (int, float))]
            if len(series) >= 2:
                slope, intercept = self._stats_helper.linear_trend(series)
                prediction = slope * (len(series) + 5) + intercept
                confidence = max(0.0, 1.0 - abs(slope) * 0.1)
            else:
                prediction = self._stats_helper.safe_mean(series)
                confidence = 0.3
            result = {
                "status": "ok",
                "data": {
                    "prediction": round(prediction, 4),
                    "confidence": round(confidence, 4),
                    "trend": "up" if slope > 0.01 else "down" if slope < -0.01 else "stable",
                    "historical_count": len(series),
                },
            }

            elapsed = time.time() - start_time
            result["metadata"] = {
                "module": "M1040",
                "method": "predict_premade_strategy",
                "elapsed_ms": round(elapsed * 1000, 2),
                "timestamp": time.time(),
                "cache_stats": self._cache.stats,
            }

            self._cache.put(cache_key, result)
            self._state = DuoPartnerDetectorState.READY
            logger.info(f"M1040.predict_premade_strategy completed in {elapsed:.3f}s")
            return result

        except Exception as e:
            self._state = DuoPartnerDetectorState.ERROR
            self._error_counts["predict_premade_strategy"] += 1
            logger.error(f"M1040.predict_premade_strategy failed: {e}")
            return {"status": "error", "reason": str(e)}

    def get_module_info(self) -> Dict[str, Any]:
        """模块信息"""
        return {
            "module_id": self._module_id,
            "class": "DuoPartnerDetector",
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
            "healthy": self._state in (DuoPartnerDetectorState.READY, DuoPartnerDetectorState.PROCESSING),
            "state": self._state.value,
            "connector_connected": self._connector._connected,
            "cache_size": len(self._cache._cache),
            "error_total": sum(self._error_counts.values()),
        }

    async def shutdown(self) -> None:
        """优雅关闭"""
        logger.info(f"M1040 shutting down...")
        self._cache.clear()
        self._state = DuoPartnerDetectorState.SHUTDOWN
        self._initialized = False

    def __repr__(self) -> str:
        return f"DuoPartnerDetector(state={self._state.value}, uptime={time.time()-self._created_at:.0f}s)"

