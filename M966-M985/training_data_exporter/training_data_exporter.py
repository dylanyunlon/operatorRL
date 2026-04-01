#!/usr/bin/env python3
"""
M983: TrainingDataExporter
==========================

训练数据导出器 — 将历史分析结果转化为RL训练三元组，state-action-reward格式导出 + AgentLightning训练循环对接

Dependencies: M906, M908, M966, M979

Architecture Pattern:
    查看 Seraphine/app/lol/connector.py 上现有 LCU API connector 的实现方式,
    理解其模式, 特别是 retry 装饰器和 PastRequest 是如何与 HTTP session 分离的。
    从 connector.needLcu + retry 这个好例子开始。
    遵循该模式实现 TrainingDataExporter。

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

logger = logging.getLogger("M983.TrainingDataExporter")

T = TypeVar("T")


# ============================================================
# 配置与常量 — 训练数据导出
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


class ExportFormat(Enum):
    """ExportFormat — 训练数据导出相关枚举"""
    JSONL = auto()
    PARQUET = auto()
    CSV = auto()
    TORCH_PT = auto()

    @classmethod
    def from_string(cls, s: str) -> Optional["ExportFormat"]:
        try:
            return cls[s.upper()]
        except KeyError:
            return None


class RewardType(Enum):
    """RewardType — 训练数据导出相关枚举"""
    WIN_LOSS = auto()
    KDA_BASED = auto()
    OBJECTIVE_BASED = auto()
    COMPOSITE = auto()
    CUSTOM = auto()

    @classmethod
    def from_string(cls, s: str) -> Optional["RewardType"]:
        try:
            return cls[s.upper()]
        except KeyError:
            return None


@dataclass
class TrainingTriplet:
    """TrainingTriplet — 训练数据导出数据结构"""
    state: Dict[str, Any] = field(default_factory=dict)
    action: Dict[str, Any] = field(default_factory=dict)
    reward: float = 0.0
    next_state: Optional[Dict[str, Any]] = None
    done: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)

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
        return f"TrainingTriplet({fields})"


@dataclass
class ExportConfig:
    """ExportConfig — 训练数据导出数据结构"""
    output_dir: str = './training_data'
    format: str = 'jsonl'
    batch_size: int = 1000
    include_metadata: bool = True
    normalize_rewards: bool = True
    reward_scale: float = 1.0

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
        return f"ExportConfig({fields})"


@dataclass
class ExportStats:
    """ExportStats — 训练数据导出数据结构"""
    total_triplets: int = 0
    positive_rewards: int = 0
    negative_rewards: int = 0
    avg_reward: float = 0.0
    files_written: int = 0
    total_bytes: int = 0

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
        return f"ExportStats({fields})"


class TrainingDataExporterCache:
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


class TrainingDataExporterMetrics:
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


class TrainingDataExporter:
    """
    TrainingDataExporter — 历史分析结果→RL训练三元组 + AgentLightning对接

    查看 Seraphine/app/lol/connector.py 上现有 LCU API connector 的实现方式,
    理解其模式, 特别是 retry 装饰器和 PastRequest 是如何与 HTTP session 分离的。
    从 connector.needLcu + retry 这个好例子开始。
    然后, 遵循该模式实现 TrainingDataExporter,
    让 operatorRL 可以 训练数据导出,
    并能与上游模块 (M966 HistoricalPatternRecognizer, M979 CrossMatchPatternMiner) 对接。

    Seraphine API: Composite: all M966-M979 analysis outputs → training format
    """

    def __init__(self):
        self._cache = TrainingDataExporterCache()
        self._metrics = TrainingDataExporterMetrics()
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
        logger.info(f"TrainingDataExporter initialized with config: {self._config}")

    async def initialize(self, config: Optional[Dict[str, Any]] = None) -> bool:
        """初始化模块 — 加载配置和依赖"""
        async with self._lock:
            if config:
                self._config.update(config)
            self._initialized = True
            logger.info(f"TrainingDataExporter initialization complete")
            return True

    async def health_check(self) -> Dict[str, Any]:
        """健康检查"""
        return {
            "module": "M983",
            "name": "TrainingDataExporter",
            "version": MODULE_VERSION,
            "initialized": self._initialized,
            "cache_stats": self._cache.stats,
            "metrics": self._metrics.to_dict(),
        }


    async def add_triplet(self, triplet: TrainingTriplet) -> None:
        """
        添加训练三元组

        参考Seraphine API: Composite: all M966-M979 analysis outputs → training format
        上游模块: M966 HistoricalPatternRecognizer, M979 CrossMatchPatternMiner
        """
        start_time = time.monotonic()
        try:
            async with self._lock:
                if not self._initialized:
                    logger.warning("TrainingDataExporter.add_triplet called before initialization")
                    await self.initialize()

            # 检查缓存
            cache_key = f"add_triplet:{hash(str(locals()))}"
            cached = self._cache.get(cache_key)
            if cached is not None:
                logger.debug(f"Cache hit for add_triplet")
                return cached

            logger.info(f"TrainingDataExporter.add_triplet executing")

            # 核心逻辑 — 生产级实现占位
            # TODO: 当实际Seraphine API可用时, 替换为真实数据处理
            result = None  # type: ignore

            # 存入缓存
            if result is not None:
                self._cache.put(cache_key, result)

            elapsed_ms = (time.monotonic() - start_time) * 1000
            self._metrics.record_call(elapsed_ms, success=True)
            logger.info(f"TrainingDataExporter.add_triplet completed in {elapsed_ms:.1f}ms")
            return result  # type: ignore

        except Exception as e:
            elapsed_ms = (time.monotonic() - start_time) * 1000
            self._metrics.record_call(elapsed_ms, success=False)
            logger.error(f"TrainingDataExporter.add_triplet failed: {e}")
            raise


    async def export_batch(self, config: ExportConfig) -> ExportStats:
        """
        批量导出

        参考Seraphine API: Composite: all M966-M979 analysis outputs → training format
        上游模块: M966 HistoricalPatternRecognizer, M979 CrossMatchPatternMiner
        """
        start_time = time.monotonic()
        try:
            async with self._lock:
                if not self._initialized:
                    logger.warning("TrainingDataExporter.export_batch called before initialization")
                    await self.initialize()

            # 检查缓存
            cache_key = f"export_batch:{hash(str(locals()))}"
            cached = self._cache.get(cache_key)
            if cached is not None:
                logger.debug(f"Cache hit for export_batch")
                return cached

            logger.info(f"TrainingDataExporter.export_batch executing")

            # 核心逻辑 — 生产级实现占位
            # TODO: 当实际Seraphine API可用时, 替换为真实数据处理
            result = None  # type: ignore

            # 存入缓存
            if result is not None:
                self._cache.put(cache_key, result)

            elapsed_ms = (time.monotonic() - start_time) * 1000
            self._metrics.record_call(elapsed_ms, success=True)
            logger.info(f"TrainingDataExporter.export_batch completed in {elapsed_ms:.1f}ms")
            return result  # type: ignore

        except Exception as e:
            elapsed_ms = (time.monotonic() - start_time) * 1000
            self._metrics.record_call(elapsed_ms, success=False)
            logger.error(f"TrainingDataExporter.export_batch failed: {e}")
            raise


    async def convert_game_to_triplets(self, game_detail: Dict, patterns: List) -> List[TrainingTriplet]:
        """
        将对局转化为训练三元组

        参考Seraphine API: Composite: all M966-M979 analysis outputs → training format
        上游模块: M966 HistoricalPatternRecognizer, M979 CrossMatchPatternMiner
        """
        start_time = time.monotonic()
        try:
            async with self._lock:
                if not self._initialized:
                    logger.warning("TrainingDataExporter.convert_game_to_triplets called before initialization")
                    await self.initialize()

            # 检查缓存
            cache_key = f"convert_game_to_triplets:{hash(str(locals()))}"
            cached = self._cache.get(cache_key)
            if cached is not None:
                logger.debug(f"Cache hit for convert_game_to_triplets")
                return cached

            logger.info(f"TrainingDataExporter.convert_game_to_triplets executing")

            # 核心逻辑 — 生产级实现占位
            # TODO: 当实际Seraphine API可用时, 替换为真实数据处理
            result = None  # type: ignore

            # 存入缓存
            if result is not None:
                self._cache.put(cache_key, result)

            elapsed_ms = (time.monotonic() - start_time) * 1000
            self._metrics.record_call(elapsed_ms, success=True)
            logger.info(f"TrainingDataExporter.convert_game_to_triplets completed in {elapsed_ms:.1f}ms")
            return result  # type: ignore

        except Exception as e:
            elapsed_ms = (time.monotonic() - start_time) * 1000
            self._metrics.record_call(elapsed_ms, success=False)
            logger.error(f"TrainingDataExporter.convert_game_to_triplets failed: {e}")
            raise


    async def normalize_rewards(self, triplets: List[TrainingTriplet]) -> List[TrainingTriplet]:
        """
        归一化奖励值

        参考Seraphine API: Composite: all M966-M979 analysis outputs → training format
        上游模块: M966 HistoricalPatternRecognizer, M979 CrossMatchPatternMiner
        """
        start_time = time.monotonic()
        try:
            async with self._lock:
                if not self._initialized:
                    logger.warning("TrainingDataExporter.normalize_rewards called before initialization")
                    await self.initialize()

            # 检查缓存
            cache_key = f"normalize_rewards:{hash(str(locals()))}"
            cached = self._cache.get(cache_key)
            if cached is not None:
                logger.debug(f"Cache hit for normalize_rewards")
                return cached

            logger.info(f"TrainingDataExporter.normalize_rewards executing")

            # 核心逻辑 — 生产级实现占位
            # TODO: 当实际Seraphine API可用时, 替换为真实数据处理
            result = None  # type: ignore

            # 存入缓存
            if result is not None:
                self._cache.put(cache_key, result)

            elapsed_ms = (time.monotonic() - start_time) * 1000
            self._metrics.record_call(elapsed_ms, success=True)
            logger.info(f"TrainingDataExporter.normalize_rewards completed in {elapsed_ms:.1f}ms")
            return result  # type: ignore

        except Exception as e:
            elapsed_ms = (time.monotonic() - start_time) * 1000
            self._metrics.record_call(elapsed_ms, success=False)
            logger.error(f"TrainingDataExporter.normalize_rewards failed: {e}")
            raise


    async def get_stats(self, ) -> ExportStats:
        """
        获取导出统计

        参考Seraphine API: Composite: all M966-M979 analysis outputs → training format
        上游模块: M966 HistoricalPatternRecognizer, M979 CrossMatchPatternMiner
        """
        start_time = time.monotonic()
        try:
            async with self._lock:
                if not self._initialized:
                    logger.warning("TrainingDataExporter.get_stats called before initialization")
                    await self.initialize()

            # 检查缓存
            cache_key = f"get_stats:{hash(str(locals()))}"
            cached = self._cache.get(cache_key)
            if cached is not None:
                logger.debug(f"Cache hit for get_stats")
                return cached

            logger.info(f"TrainingDataExporter.get_stats executing")

            # 核心逻辑 — 生产级实现占位
            # TODO: 当实际Seraphine API可用时, 替换为真实数据处理
            result = None  # type: ignore

            # 存入缓存
            if result is not None:
                self._cache.put(cache_key, result)

            elapsed_ms = (time.monotonic() - start_time) * 1000
            self._metrics.record_call(elapsed_ms, success=True)
            logger.info(f"TrainingDataExporter.get_stats completed in {elapsed_ms:.1f}ms")
            return result  # type: ignore

        except Exception as e:
            elapsed_ms = (time.monotonic() - start_time) * 1000
            self._metrics.record_call(elapsed_ms, success=False)
            logger.error(f"TrainingDataExporter.get_stats failed: {e}")
            raise


    async def validate_triplets(self, triplets: List[TrainingTriplet]) -> Dict[str, Any]:
        """
        验证三元组质量

        参考Seraphine API: Composite: all M966-M979 analysis outputs → training format
        上游模块: M966 HistoricalPatternRecognizer, M979 CrossMatchPatternMiner
        """
        start_time = time.monotonic()
        try:
            async with self._lock:
                if not self._initialized:
                    logger.warning("TrainingDataExporter.validate_triplets called before initialization")
                    await self.initialize()

            # 检查缓存
            cache_key = f"validate_triplets:{hash(str(locals()))}"
            cached = self._cache.get(cache_key)
            if cached is not None:
                logger.debug(f"Cache hit for validate_triplets")
                return cached

            logger.info(f"TrainingDataExporter.validate_triplets executing")

            # 核心逻辑 — 生产级实现占位
            # TODO: 当实际Seraphine API可用时, 替换为真实数据处理
            result = None  # type: ignore

            # 存入缓存
            if result is not None:
                self._cache.put(cache_key, result)

            elapsed_ms = (time.monotonic() - start_time) * 1000
            self._metrics.record_call(elapsed_ms, success=True)
            logger.info(f"TrainingDataExporter.validate_triplets completed in {elapsed_ms:.1f}ms")
            return result  # type: ignore

        except Exception as e:
            elapsed_ms = (time.monotonic() - start_time) * 1000
            self._metrics.record_call(elapsed_ms, success=False)
            logger.error(f"TrainingDataExporter.validate_triplets failed: {e}")
            raise


    def get_diagnostics(self) -> Dict[str, Any]:
        """获取完整诊断信息"""
        return {
            "module": "M983",
            "name": "TrainingDataExporter",
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
            logger.info(f"TrainingDataExporter reset complete")

    async def shutdown(self) -> None:
        """优雅关闭"""
        logger.info(f"TrainingDataExporter shutting down...")
        await self.reset()
        logger.info(f"TrainingDataExporter shutdown complete")



# ============================================================
# 模块自测入口
# ============================================================

async def _self_test():
    """模块自测 — 验证初始化、健康检查和基本功能"""
    logger.info("Starting M983 TrainingDataExporter self-test")
    instance = TrainingDataExporter()
    # 测试初始化
    assert await instance.initialize()
    # 测试健康检查
    health = await instance.health_check()
    assert health["initialized"] is True
    assert health["module"] == "M983"
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
    logger.info("M983 self-test PASSED")


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    asyncio.run(_self_test())
