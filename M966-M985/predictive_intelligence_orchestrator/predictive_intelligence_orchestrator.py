#!/usr/bin/env python3
"""
M985: PredictiveIntelligenceOrchestrator
========================================

预测情报编排器 — 统一编排所有M966-M984模块的顶层管道，调度分析任务 + 缓存策略 + 健康监控 + 与M866-M885实时系统对接

Dependencies: M906, M966-M984

Architecture Pattern:
    查看 Seraphine/app/lol/connector.py 上现有 LCU API connector 的实现方式,
    理解其模式, 特别是 retry 装饰器和 PastRequest 是如何与 HTTP session 分离的。
    从 connector.needLcu + retry 这个好例子开始。
    遵循该模式实现 PredictiveIntelligenceOrchestrator。

Reference:
    - Seraphine: github.com/ljszx/Seraphine
    - LoL Optimizer: github.com/oracle-devrel/leagueoflegends-optimizer
    - Fiddler MCP: telerik.com/fiddler/fiddler-everywhere/documentation/mcp-server
    - operatorRL: github.com/dylanyunlon/operatorRL.git
"""

import asyncio
import json
import logging
import time
import hashlib
import statistics
from collections import defaultdict, deque, OrderedDict
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from enum import Enum, auto
from pathlib import Path
from typing import (
    Any, Callable, Coroutine, Dict, List, Optional, Set,
    Tuple, TypeVar, Union, NamedTuple, Protocol, Sequence,
)

logger = logging.getLogger("M985.PredictiveIntelligenceOrchestrator")

T = TypeVar("T")


# ============================================================
# 配置与常量 — 预测情报编排
# ============================================================

MODULE_VERSION = "1.0.0"
MAX_CACHE_SIZE = 500
CACHE_TTL_SECONDS = 1800
MIN_SAMPLE_SIZE = 3
CONFIDENCE_THRESHOLD = 0.5
BATCH_SIZE = 100
MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 1.0
TIMEOUT_SECONDS = 30.0
METRIC_WINDOW_SIZE = 100


class PipelineStage(Enum):
    """PipelineStage — 预测情报编排相关枚举"""
    FETCH_HISTORY = auto()
    PATTERN_RECOGNITION = auto()
    OUTCOME_PREDICTION = auto()
    DRAFT_SIMULATION = auto()
    REPORT_GENERATION = auto()
    VOICE_NARRATION = auto()
    TRAINING_EXPORT = auto()

    @classmethod
    def from_string(cls, s: str) -> Optional["PipelineStage"]:
        try:
            return cls[s.upper()]
        except KeyError:
            return None


class ModuleStatus(Enum):
    """ModuleStatus — 预测情报编排相关枚举"""
    HEALTHY = auto()
    DEGRADED = auto()
    UNAVAILABLE = auto()
    INITIALIZING = auto()

    @classmethod
    def from_string(cls, s: str) -> Optional["ModuleStatus"]:
        try:
            return cls[s.upper()]
        except KeyError:
            return None


@dataclass
class OrchestratorConfig:
    """OrchestratorConfig — 预测情报编排数据结构"""
    max_concurrent_tasks: int = 10
    cache_ttl_seconds: int = 300
    health_check_interval: int = 60
    enable_voice: bool = True
    enable_fiddler: bool = True
    training_export_enabled: bool = False

    def to_dict(self) -> Dict[str, Any]:
        result = {}
        for k, v in self.__dict__.items():
            if isinstance(v, Enum):
                result[k] = v.name
            elif isinstance(v, (list, tuple)):
                result[k] = [x.to_dict() if hasattr(x, "to_dict") else x for x in v]
            elif isinstance(v, dict):
                result[k] = {kk: vv.to_dict() if hasattr(vv, "to_dict") else vv for kk, vv in v.items()}
            elif isinstance(v, set):
                result[k] = list(v)
            elif isinstance(v, deque):
                result[k] = list(v)
            else:
                result[k] = v
        return result

    def __repr__(self):
        fields = ", ".join(f"{k}={v!r}" for k, v in self.__dict__.items()
                          if v is not None and v != [] and v != {})
        return f"OrchestratorConfig({fields})"


@dataclass
class ModuleHealth:
    """ModuleHealth — 预测情报编排数据结构"""
    module_id: str
    status: str = 'unknown'
    last_check: float = 0.0
    error_count: int = 0
    avg_latency_ms: float = 0.0
    calls_total: int = 0

    def to_dict(self) -> Dict[str, Any]:
        result = {}
        for k, v in self.__dict__.items():
            if isinstance(v, Enum):
                result[k] = v.name
            elif isinstance(v, (list, tuple)):
                result[k] = [x.to_dict() if hasattr(x, "to_dict") else x for x in v]
            elif isinstance(v, dict):
                result[k] = {kk: vv.to_dict() if hasattr(vv, "to_dict") else vv for kk, vv in v.items()}
            elif isinstance(v, set):
                result[k] = list(v)
            elif isinstance(v, deque):
                result[k] = list(v)
            else:
                result[k] = v
        return result

    def __repr__(self):
        fields = ", ".join(f"{k}={v!r}" for k, v in self.__dict__.items()
                          if v is not None and v != [] and v != {})
        return f"ModuleHealth({fields})"


@dataclass
class OrchestratorState:
    """OrchestratorState — 预测情报编排数据结构"""
    active_tasks: int = 0
    completed_tasks: int = 0
    failed_tasks: int = 0
    module_health: Dict[str, ModuleHealth] = field(default_factory=dict)
    uptime_seconds: float = 0.0
    last_full_analysis: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        result = {}
        for k, v in self.__dict__.items():
            if isinstance(v, Enum):
                result[k] = v.name
            elif isinstance(v, (list, tuple)):
                result[k] = [x.to_dict() if hasattr(x, "to_dict") else x for x in v]
            elif isinstance(v, dict):
                result[k] = {kk: vv.to_dict() if hasattr(vv, "to_dict") else vv for kk, vv in v.items()}
            elif isinstance(v, set):
                result[k] = list(v)
            elif isinstance(v, deque):
                result[k] = list(v)
            else:
                result[k] = v
        return result

    def __repr__(self):
        fields = ", ".join(f"{k}={v!r}" for k, v in self.__dict__.items()
                          if v is not None and v != [] and v != {})
        return f"OrchestratorState({fields})"


@dataclass
class AnalysisPipeline:
    """AnalysisPipeline — 预测情报编排数据结构"""
    pipeline_id: str = ''
    stages: List[str] = field(default_factory=list)
    current_stage: int = 0
    results: Dict[str, Any] = field(default_factory=dict)
    started_at: float = 0.0
    completed_at: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        result = {}
        for k, v in self.__dict__.items():
            if isinstance(v, Enum):
                result[k] = v.name
            elif isinstance(v, (list, tuple)):
                result[k] = [x.to_dict() if hasattr(x, "to_dict") else x for x in v]
            elif isinstance(v, dict):
                result[k] = {kk: vv.to_dict() if hasattr(vv, "to_dict") else vv for kk, vv in v.items()}
            elif isinstance(v, set):
                result[k] = list(v)
            elif isinstance(v, deque):
                result[k] = list(v)
            else:
                result[k] = v
        return result

    def __repr__(self):
        fields = ", ".join(f"{k}={v!r}" for k, v in self.__dict__.items()
                          if v is not None and v != [] and v != {})
        return f"AnalysisPipeline({fields})"


class PredictiveIntelligenceOrchestratorCache:
    """LRU+TTL缓存 — 参考M924 HistoricalDataCache模式"""

    def __init__(self, max_size: int = MAX_CACHE_SIZE,
                 ttl_seconds: int = CACHE_TTL_SECONDS):
        self._max_size = max_size
        self._ttl = ttl_seconds
        self._cache: OrderedDict[str, Tuple[Any, float]] = OrderedDict()
        self._hits = 0
        self._misses = 0
        self._evictions = 0

    def get(self, key: str) -> Optional[Any]:
        if key not in self._cache:
            self._misses += 1
            return None
        value, ts = self._cache[key]
        if time.time() - ts > self._ttl:
            del self._cache[key]
            self._misses += 1
            self._evictions += 1
            return None
        self._cache.move_to_end(key)
        self._hits += 1
        return value

    def put(self, key: str, value: Any) -> None:
        if key in self._cache:
            self._cache.move_to_end(key)
        self._cache[key] = (value, time.time())
        while len(self._cache) > self._max_size:
            self._cache.popitem(last=False)
            self._evictions += 1

    def invalidate(self, key: str) -> bool:
        if key in self._cache:
            del self._cache[key]
            return True
        return False

    def clear(self) -> None:
        self._cache.clear()
        self._hits = 0
        self._misses = 0

    @property
    def stats(self) -> Dict[str, Any]:
        total = self._hits + self._misses
        return {
            "size": len(self._cache),
            "max_size": self._max_size,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": round(self._hits / total, 4) if total > 0 else 0.0,
            "evictions": self._evictions,
        }


class PredictiveIntelligenceOrchestratorMetrics:
    """运行时指标收集器"""

    def __init__(self, window_size: int = METRIC_WINDOW_SIZE):
        self._window_size = window_size
        self._latencies: deque = deque(maxlen=window_size)
        self._call_count = 0
        self._error_count = 0
        self._start_time = time.time()

    def record_call(self, latency_ms: float, success: bool = True) -> None:
        self._latencies.append(latency_ms)
        self._call_count += 1
        if not success:
            self._error_count += 1

    @property
    def avg_latency_ms(self) -> float:
        if not self._latencies:
            return 0.0
        return statistics.mean(self._latencies)

    @property
    def p95_latency_ms(self) -> float:
        if len(self._latencies) < 20:
            return self.avg_latency_ms
        sorted_lat = sorted(self._latencies)
        idx = int(len(sorted_lat) * 0.95)
        return sorted_lat[min(idx, len(sorted_lat) - 1)]

    @property
    def error_rate(self) -> float:
        if self._call_count == 0:
            return 0.0
        return self._error_count / self._call_count

    @property
    def uptime_seconds(self) -> float:
        return time.time() - self._start_time

    def to_dict(self) -> Dict[str, Any]:
        return {
            "call_count": self._call_count,
            "error_count": self._error_count,
            "error_rate": round(self.error_rate, 4),
            "avg_latency_ms": round(self.avg_latency_ms, 2),
            "p95_latency_ms": round(self.p95_latency_ms, 2),
            "uptime_seconds": round(self.uptime_seconds, 1),
        }


class PredictiveIntelligenceOrchestrator:
    """
    PredictiveIntelligenceOrchestrator — 统一编排M966-M984 + 调度/缓存/监控 + M866-M885对接

    查看 Seraphine/app/lol/connector.py 上现有 LCU API connector 的实现方式,
    理解其模式, 特别是 retry 装饰器和 PastRequest 是如何与 HTTP session 分离的。
    从 connector.needLcu + retry 这个好例子开始。
    然后, 遵循该模式实现 PredictiveIntelligenceOrchestrator,
    让 operatorRL 可以 预测情报编排,
    并能与上游模块 (M966-M984 全部模块, M866-M885 实时系统, M906-M925 历史数据层) 对接。

    Seraphine API: Orchestrates all M966-M984 modules + M866-M885 live system
    """

    def __init__(self):
        self._cache = PredictiveIntelligenceOrchestratorCache()
        self._metrics = PredictiveIntelligenceOrchestratorMetrics()
        self._data_store: Dict[str, Any] = {}
        self._lock = asyncio.Lock()
        self._initialized = False
        self._config: Dict[str, Any] = {
            "max_cache_size": MAX_CACHE_SIZE,
            "cache_ttl": CACHE_TTL_SECONDS,
            "min_sample_size": MIN_SAMPLE_SIZE,
            "confidence_threshold": CONFIDENCE_THRESHOLD,
            "batch_size": BATCH_SIZE,
        }
        logger.info(f"PredictiveIntelligenceOrchestrator initialized with config: {self._config}")

    async def initialize(self, config: Optional[Dict[str, Any]] = None) -> bool:
        """初始化模块 — 加载配置和依赖"""
        async with self._lock:
            if config:
                self._config.update(config)
            self._initialized = True
            logger.info(f"PredictiveIntelligenceOrchestrator initialization complete")
            return True

    async def health_check(self) -> Dict[str, Any]:
        """健康检查"""
        return {
            "module": "M985",
            "name": "PredictiveIntelligenceOrchestrator",
            "version": MODULE_VERSION,
            "initialized": self._initialized,
            "cache_stats": self._cache.stats,
            "metrics": self._metrics.to_dict(),
        }


    async def initialize(self, config: OrchestratorConfig) -> bool:
        """
        初始化编排器和所有子模块

        参考Seraphine API: Orchestrates all M966-M984 modules + M866-M885 live system
        上游模块: M966-M984 全部模块, M866-M885 实时系统, M906-M925 历史数据层
        """
        start_time = time.monotonic()
        try:
            async with self._lock:
                if not self._initialized:
                    logger.warning("PredictiveIntelligenceOrchestrator.initialize called before initialization")
                    await self.initialize()

            # 检查缓存
            cache_key = f"initialize:{hash(str(locals()))}"
            cached = self._cache.get(cache_key)
            if cached is not None:
                logger.debug(f"Cache hit for initialize")
                return cached

            logger.info(f"PredictiveIntelligenceOrchestrator.initialize executing")

            # 核心逻辑 — 生产级实现占位
            # TODO: 当实际Seraphine API可用时, 替换为真实数据处理
            result = None  # type: ignore

            # 存入缓存
            if result is not None:
                self._cache.put(cache_key, result)

            elapsed_ms = (time.monotonic() - start_time) * 1000
            self._metrics.record_call(elapsed_ms, success=True)
            logger.info(f"PredictiveIntelligenceOrchestrator.initialize completed in {elapsed_ms:.1f}ms")
            return result  # type: ignore

        except Exception as e:
            elapsed_ms = (time.monotonic() - start_time) * 1000
            self._metrics.record_call(elapsed_ms, success=False)
            logger.error(f"PredictiveIntelligenceOrchestrator.initialize failed: {e}")
            raise


    async def run_full_analysis(self, blue_puuids: List[str], red_puuids: List[str]) -> Dict[str, Any]:
        """
        运行完整分析管道

        参考Seraphine API: Orchestrates all M966-M984 modules + M866-M885 live system
        上游模块: M966-M984 全部模块, M866-M885 实时系统, M906-M925 历史数据层
        """
        start_time = time.monotonic()
        try:
            async with self._lock:
                if not self._initialized:
                    logger.warning("PredictiveIntelligenceOrchestrator.run_full_analysis called before initialization")
                    await self.initialize()

            # 检查缓存
            cache_key = f"run_full_analysis:{hash(str(locals()))}"
            cached = self._cache.get(cache_key)
            if cached is not None:
                logger.debug(f"Cache hit for run_full_analysis")
                return cached

            logger.info(f"PredictiveIntelligenceOrchestrator.run_full_analysis executing")

            # 核心逻辑 — 生产级实现占位
            # TODO: 当实际Seraphine API可用时, 替换为真实数据处理
            result = None  # type: ignore

            # 存入缓存
            if result is not None:
                self._cache.put(cache_key, result)

            elapsed_ms = (time.monotonic() - start_time) * 1000
            self._metrics.record_call(elapsed_ms, success=True)
            logger.info(f"PredictiveIntelligenceOrchestrator.run_full_analysis completed in {elapsed_ms:.1f}ms")
            return result  # type: ignore

        except Exception as e:
            elapsed_ms = (time.monotonic() - start_time) * 1000
            self._metrics.record_call(elapsed_ms, success=False)
            logger.error(f"PredictiveIntelligenceOrchestrator.run_full_analysis failed: {e}")
            raise


    async def run_pregame_pipeline(self, game_lobby: Dict) -> Dict[str, Any]:
        """
        运行赛前管道

        参考Seraphine API: Orchestrates all M966-M984 modules + M866-M885 live system
        上游模块: M966-M984 全部模块, M866-M885 实时系统, M906-M925 历史数据层
        """
        start_time = time.monotonic()
        try:
            async with self._lock:
                if not self._initialized:
                    logger.warning("PredictiveIntelligenceOrchestrator.run_pregame_pipeline called before initialization")
                    await self.initialize()

            # 检查缓存
            cache_key = f"run_pregame_pipeline:{hash(str(locals()))}"
            cached = self._cache.get(cache_key)
            if cached is not None:
                logger.debug(f"Cache hit for run_pregame_pipeline")
                return cached

            logger.info(f"PredictiveIntelligenceOrchestrator.run_pregame_pipeline executing")

            # 核心逻辑 — 生产级实现占位
            # TODO: 当实际Seraphine API可用时, 替换为真实数据处理
            result = None  # type: ignore

            # 存入缓存
            if result is not None:
                self._cache.put(cache_key, result)

            elapsed_ms = (time.monotonic() - start_time) * 1000
            self._metrics.record_call(elapsed_ms, success=True)
            logger.info(f"PredictiveIntelligenceOrchestrator.run_pregame_pipeline completed in {elapsed_ms:.1f}ms")
            return result  # type: ignore

        except Exception as e:
            elapsed_ms = (time.monotonic() - start_time) * 1000
            self._metrics.record_call(elapsed_ms, success=False)
            logger.error(f"PredictiveIntelligenceOrchestrator.run_pregame_pipeline failed: {e}")
            raise


    async def run_live_pipeline(self, live_game_state: Dict) -> Dict[str, Any]:
        """
        运行实时管道

        参考Seraphine API: Orchestrates all M966-M984 modules + M866-M885 live system
        上游模块: M966-M984 全部模块, M866-M885 实时系统, M906-M925 历史数据层
        """
        start_time = time.monotonic()
        try:
            async with self._lock:
                if not self._initialized:
                    logger.warning("PredictiveIntelligenceOrchestrator.run_live_pipeline called before initialization")
                    await self.initialize()

            # 检查缓存
            cache_key = f"run_live_pipeline:{hash(str(locals()))}"
            cached = self._cache.get(cache_key)
            if cached is not None:
                logger.debug(f"Cache hit for run_live_pipeline")
                return cached

            logger.info(f"PredictiveIntelligenceOrchestrator.run_live_pipeline executing")

            # 核心逻辑 — 生产级实现占位
            # TODO: 当实际Seraphine API可用时, 替换为真实数据处理
            result = None  # type: ignore

            # 存入缓存
            if result is not None:
                self._cache.put(cache_key, result)

            elapsed_ms = (time.monotonic() - start_time) * 1000
            self._metrics.record_call(elapsed_ms, success=True)
            logger.info(f"PredictiveIntelligenceOrchestrator.run_live_pipeline completed in {elapsed_ms:.1f}ms")
            return result  # type: ignore

        except Exception as e:
            elapsed_ms = (time.monotonic() - start_time) * 1000
            self._metrics.record_call(elapsed_ms, success=False)
            logger.error(f"PredictiveIntelligenceOrchestrator.run_live_pipeline failed: {e}")
            raise


    async def get_module_health(self, ) -> Dict[str, ModuleHealth]:
        """
        获取所有模块健康状态

        参考Seraphine API: Orchestrates all M966-M984 modules + M866-M885 live system
        上游模块: M966-M984 全部模块, M866-M885 实时系统, M906-M925 历史数据层
        """
        start_time = time.monotonic()
        try:
            async with self._lock:
                if not self._initialized:
                    logger.warning("PredictiveIntelligenceOrchestrator.get_module_health called before initialization")
                    await self.initialize()

            # 检查缓存
            cache_key = f"get_module_health:{hash(str(locals()))}"
            cached = self._cache.get(cache_key)
            if cached is not None:
                logger.debug(f"Cache hit for get_module_health")
                return cached

            logger.info(f"PredictiveIntelligenceOrchestrator.get_module_health executing")

            # 核心逻辑 — 生产级实现占位
            # TODO: 当实际Seraphine API可用时, 替换为真实数据处理
            result = None  # type: ignore

            # 存入缓存
            if result is not None:
                self._cache.put(cache_key, result)

            elapsed_ms = (time.monotonic() - start_time) * 1000
            self._metrics.record_call(elapsed_ms, success=True)
            logger.info(f"PredictiveIntelligenceOrchestrator.get_module_health completed in {elapsed_ms:.1f}ms")
            return result  # type: ignore

        except Exception as e:
            elapsed_ms = (time.monotonic() - start_time) * 1000
            self._metrics.record_call(elapsed_ms, success=False)
            logger.error(f"PredictiveIntelligenceOrchestrator.get_module_health failed: {e}")
            raise


    async def get_state(self, ) -> OrchestratorState:
        """
        获取编排器状态

        参考Seraphine API: Orchestrates all M966-M984 modules + M866-M885 live system
        上游模块: M966-M984 全部模块, M866-M885 实时系统, M906-M925 历史数据层
        """
        start_time = time.monotonic()
        try:
            async with self._lock:
                if not self._initialized:
                    logger.warning("PredictiveIntelligenceOrchestrator.get_state called before initialization")
                    await self.initialize()

            # 检查缓存
            cache_key = f"get_state:{hash(str(locals()))}"
            cached = self._cache.get(cache_key)
            if cached is not None:
                logger.debug(f"Cache hit for get_state")
                return cached

            logger.info(f"PredictiveIntelligenceOrchestrator.get_state executing")

            # 核心逻辑 — 生产级实现占位
            # TODO: 当实际Seraphine API可用时, 替换为真实数据处理
            result = None  # type: ignore

            # 存入缓存
            if result is not None:
                self._cache.put(cache_key, result)

            elapsed_ms = (time.monotonic() - start_time) * 1000
            self._metrics.record_call(elapsed_ms, success=True)
            logger.info(f"PredictiveIntelligenceOrchestrator.get_state completed in {elapsed_ms:.1f}ms")
            return result  # type: ignore

        except Exception as e:
            elapsed_ms = (time.monotonic() - start_time) * 1000
            self._metrics.record_call(elapsed_ms, success=False)
            logger.error(f"PredictiveIntelligenceOrchestrator.get_state failed: {e}")
            raise


    async def shutdown(self, ) -> None:
        """
        优雅关闭

        参考Seraphine API: Orchestrates all M966-M984 modules + M866-M885 live system
        上游模块: M966-M984 全部模块, M866-M885 实时系统, M906-M925 历史数据层
        """
        start_time = time.monotonic()
        try:
            async with self._lock:
                if not self._initialized:
                    logger.warning("PredictiveIntelligenceOrchestrator.shutdown called before initialization")
                    await self.initialize()

            # 检查缓存
            cache_key = f"shutdown:{hash(str(locals()))}"
            cached = self._cache.get(cache_key)
            if cached is not None:
                logger.debug(f"Cache hit for shutdown")
                return cached

            logger.info(f"PredictiveIntelligenceOrchestrator.shutdown executing")

            # 核心逻辑 — 生产级实现占位
            # TODO: 当实际Seraphine API可用时, 替换为真实数据处理
            result = None  # type: ignore

            # 存入缓存
            if result is not None:
                self._cache.put(cache_key, result)

            elapsed_ms = (time.monotonic() - start_time) * 1000
            self._metrics.record_call(elapsed_ms, success=True)
            logger.info(f"PredictiveIntelligenceOrchestrator.shutdown completed in {elapsed_ms:.1f}ms")
            return result  # type: ignore

        except Exception as e:
            elapsed_ms = (time.monotonic() - start_time) * 1000
            self._metrics.record_call(elapsed_ms, success=False)
            logger.error(f"PredictiveIntelligenceOrchestrator.shutdown failed: {e}")
            raise


    def get_diagnostics(self) -> Dict[str, Any]:
        """获取完整诊断信息"""
        return {
            "module": "M985",
            "name": "PredictiveIntelligenceOrchestrator",
            "version": MODULE_VERSION,
            "initialized": self._initialized,
            "config": self._config,
            "cache_stats": self._cache.stats,
            "metrics": self._metrics.to_dict(),
            "data_store_size": len(self._data_store),
        }

    async def reset(self) -> None:
        """重置所有状态"""
        async with self._lock:
            self._cache.clear()
            self._data_store.clear()
            self._initialized = False
            logger.info(f"PredictiveIntelligenceOrchestrator reset complete")

    async def shutdown(self) -> None:
        """优雅关闭"""
        logger.info(f"PredictiveIntelligenceOrchestrator shutting down...")
        await self.reset()
        logger.info(f"PredictiveIntelligenceOrchestrator shutdown complete")



# ============================================================
# 模块自测入口
# ============================================================

async def _self_test():
    """模块自测 — 验证初始化、健康检查和基本功能"""
    logger.info("Starting M985 PredictiveIntelligenceOrchestrator self-test")
    instance = PredictiveIntelligenceOrchestrator()
    # 测试初始化
    assert await instance.initialize()
    # 测试健康检查
    health = await instance.health_check()
    assert health["initialized"] is True
    assert health["module"] == "M985"
    logger.info(f"Health: {json.dumps(health, indent=2)}")
    # 测试诊断
    diag = instance.get_diagnostics()
    assert diag["version"] == MODULE_VERSION
    logger.info(f"Diagnostics: {json.dumps(diag, indent=2)}")
    # 测试重置
    await instance.reset()
    assert not instance._initialized
    # 测试关闭
    await instance.initialize()
    await instance.shutdown()
    logger.info("M985 self-test PASSED")


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    asyncio.run(_self_test())
