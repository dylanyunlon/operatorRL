#!/usr/bin/env python3
"""
M966: HistoricalPatternRecognizer
=================================

历史模式识别器 — 基于对局时间线的对手行为模式聚类与分类，使用滑动窗口时序分析从Seraphine获取的历史对局中提取可复现的行为序列

Dependencies: M906, M908, M916

Architecture Pattern:
    查看 Seraphine/app/lol/connector.py 上现有 LCU API connector 的实现方式,
    理解其模式, 特别是 retry 装饰器和 PastRequest 是如何与 HTTP session 分离的。
    从 connector.needLcu + retry 这个好例子开始。
    遵循该模式实现 HistoricalPatternRecognizer。

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

logger = logging.getLogger("M966.HistoricalPatternRecognizer")

T = TypeVar("T")


# ============================================================
# 配置与常量
# ============================================================

WINDOW_SIZE_DEFAULT = 5          # 滑动窗口默认大小
MIN_PATTERN_OCCURRENCES = 3      # 最小模式出现次数
SIMILARITY_THRESHOLD = 0.75      # 行为序列相似度阈值
MAX_PATTERNS_CACHED = 500        # 最大缓存模式数
PATTERN_TTL_SECONDS = 3600       # 模式缓存TTL
CLUSTER_EPSILON = 0.3            # DBSCAN聚类epsilon
MIN_CLUSTER_SIZE = 2             # 最小聚类大小
TIMELINE_BUCKET_MINUTES = 2      # 时间线分桶粒度(分钟)
MAX_SEQUENCE_LENGTH = 20         # 最大行为序列长度
DECAY_FACTOR = 0.95              # 时间衰减因子


class EventType(Enum):
    """对局事件类型枚举 — 参考Seraphine getGameDetailByGameId返回的timeline事件"""
    CHAMPION_KILL = auto()
    BUILDING_KILL = auto()
    ELITE_MONSTER_KILL = auto()
    ITEM_PURCHASED = auto()
    ITEM_SOLD = auto()
    ITEM_UNDO = auto()
    SKILL_LEVEL_UP = auto()
    WARD_PLACED = auto()
    WARD_KILL = auto()
    TURRET_PLATE_DESTROYED = auto()
    LEVEL_UP = auto()
    GAME_END = auto()

    @classmethod
    def from_riot_type(cls, riot_type: str) -> Optional["EventType"]:
        mapping = {
            "CHAMPION_KILL": cls.CHAMPION_KILL,
            "BUILDING_KILL": cls.BUILDING_KILL,
            "ELITE_MONSTER_KILL": cls.ELITE_MONSTER_KILL,
            "ITEM_PURCHASED": cls.ITEM_PURCHASED,
            "ITEM_SOLD": cls.ITEM_SOLD,
            "ITEM_UNDO": cls.ITEM_UNDO,
            "SKILL_LEVEL_UP": cls.SKILL_LEVEL_UP,
            "WARD_PLACED": cls.WARD_PLACED,
            "WARD_KILLED": cls.WARD_KILL,
            "TURRET_PLATE_DESTROYED": cls.TURRET_PLATE_DESTROYED,
            "LEVEL_UP": cls.LEVEL_UP,
            "GAME_END": cls.GAME_END,
        }
        return mapping.get(riot_type)


class PatternCategory(Enum):
    """行为模式分类"""
    AGGRESSIVE_EARLY = auto()      # 前期侵略型
    PASSIVE_FARMING = auto()       # 被动刷兵型
    OBJECTIVE_FOCUSED = auto()     # 目标导向型
    ROAMING_HEAVY = auto()         # 频繁游走型
    SPLIT_PUSH = auto()            # 分推型
    TEAM_FIGHT = auto()            # 团战型
    VISION_CONTROL = auto()        # 视野控制型
    COUNTER_JUNGLE = auto()        # 反野型


@dataclass
class TimelineEvent:
    """时间线事件 — 从Seraphine历史对局中提取的单个事件"""
    timestamp_ms: int
    event_type: EventType
    participant_id: int
    position_x: Optional[int] = None
    position_y: Optional[int] = None
    victim_id: Optional[int] = None
    assisting_ids: Optional[List[int]] = None
    item_id: Optional[int] = None
    monster_type: Optional[str] = None
    building_type: Optional[str] = None
    ward_type: Optional[str] = None
    skill_slot: Optional[int] = None
    level: Optional[int] = None

    @property
    def minute(self) -> float:
        return self.timestamp_ms / 60000.0

    @property
    def bucket(self) -> int:
        return int(self.minute / TIMELINE_BUCKET_MINUTES)

    def to_feature_vector(self) -> List[float]:
        """将事件转为特征向量用于模式比较"""
        features = [
            float(self.event_type.value),
            self.minute,
            float(self.position_x or 0) / 15000.0,
            float(self.position_y or 0) / 15000.0,
            float(len(self.assisting_ids)) if self.assisting_ids else 0.0,
            1.0 if self.victim_id else 0.0,
        ]
        return features


@dataclass
class BehaviorSequence:
    """行为序列 — 一段连续的事件组成的行为模式"""
    events: List[TimelineEvent]
    start_minute: float
    end_minute: float
    participant_id: int
    game_id: int

    @property
    def duration_minutes(self) -> float:
        return self.end_minute - self.start_minute

    @property
    def event_density(self) -> float:
        if self.duration_minutes <= 0:
            return 0.0
        return len(self.events) / self.duration_minutes

    def to_feature_matrix(self) -> List[List[float]]:
        return [e.to_feature_vector() for e in self.events]

    def signature_hash(self) -> str:
        """生成序列签名哈希 — 用于快速去重"""
        sig_parts = []
        for e in self.events:
            sig_parts.append(f"{e.event_type.value}:{e.bucket}")
        sig_str = "|".join(sig_parts)
        return hashlib.md5(sig_str.encode()).hexdigest()[:12]


@dataclass
class RecognizedPattern:
    """已识别的行为模式"""
    pattern_id: str
    category: PatternCategory
    representative_sequence: BehaviorSequence
    occurrence_count: int
    confidence: float
    first_seen: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_seen: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    game_ids: Set[int] = field(default_factory=set)
    avg_minute: float = 0.0
    std_minute: float = 0.0
    success_rate: float = 0.0
    tags: List[str] = field(default_factory=list)

    @property
    def is_reliable(self) -> bool:
        return self.occurrence_count >= MIN_PATTERN_OCCURRENCES and self.confidence >= 0.6

    def decay(self, current_time: datetime) -> float:
        """计算时间衰减后的置信度"""
        age_hours = (current_time - self.last_seen).total_seconds() / 3600
        return self.confidence * (DECAY_FACTOR ** age_hours)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "pattern_id": self.pattern_id,
            "category": self.category.name,
            "occurrence_count": self.occurrence_count,
            "confidence": round(self.confidence, 4),
            "avg_minute": round(self.avg_minute, 2),
            "success_rate": round(self.success_rate, 4),
            "game_count": len(self.game_ids),
            "tags": self.tags,
        }


class PatternCache:
    """模式缓存 — LRU + TTL策略, 参考M924 HistoricalDataCache模式"""

    def __init__(self, max_size: int = MAX_PATTERNS_CACHED,
                 ttl_seconds: int = PATTERN_TTL_SECONDS):
        self._max_size = max_size
        self._ttl = ttl_seconds
        self._cache: OrderedDict[str, Tuple[RecognizedPattern, float]] = OrderedDict()
        self._hits = 0
        self._misses = 0
        self._evictions = 0

    def get(self, key: str) -> Optional[RecognizedPattern]:
        if key not in self._cache:
            self._misses += 1
            return None
        pattern, ts = self._cache[key]
        if time.time() - ts > self._ttl:
            del self._cache[key]
            self._misses += 1
            self._evictions += 1
            return None
        self._cache.move_to_end(key)
        self._hits += 1
        return pattern

    def put(self, key: str, pattern: RecognizedPattern) -> None:
        if key in self._cache:
            self._cache.move_to_end(key)
        self._cache[key] = (pattern, time.time())
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

    @property
    def stats(self) -> Dict[str, int]:
        total = self._hits + self._misses
        return {
            "size": len(self._cache),
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": round(self._hits / total, 4) if total > 0 else 0.0,
            "evictions": self._evictions,
        }


class SequenceSimilarity:
    """行为序列相似度计算 — 使用DTW(动态时间规整)变种"""

    @staticmethod
    def euclidean_distance(v1: List[float], v2: List[float]) -> float:
        if len(v1) != len(v2):
            return float("inf")
        return sum((a - b) ** 2 for a, b in zip(v1, v2)) ** 0.5

    @staticmethod
    def dtw_distance(seq1: List[List[float]], seq2: List[List[float]]) -> float:
        """简化DTW距离计算"""
        n, m = len(seq1), len(seq2)
        if n == 0 or m == 0:
            return float("inf")
        # 使用全矩阵DTW (O(nm) 空间, 对短序列可接受)
        dtw = [[float("inf")] * (m + 1) for _ in range(n + 1)]
        dtw[0][0] = 0.0
        for i in range(1, n + 1):
            for j in range(1, m + 1):
                cost = SequenceSimilarity.euclidean_distance(seq1[i-1], seq2[j-1])
                dtw[i][j] = cost + min(dtw[i-1][j], dtw[i][j-1], dtw[i-1][j-1])
        return dtw[n][m]

    @staticmethod
    def normalized_similarity(seq1: BehaviorSequence, seq2: BehaviorSequence) -> float:
        """归一化相似度 [0, 1], 1表示完全相同"""
        mat1 = seq1.to_feature_matrix()
        mat2 = seq2.to_feature_matrix()
        if not mat1 or not mat2:
            return 0.0
        dist = SequenceSimilarity.dtw_distance(mat1, mat2)
        max_len = max(len(mat1), len(mat2))
        norm_dist = dist / (max_len * 6.0)  # 6维特征向量
        return max(0.0, 1.0 - norm_dist)


class PatternClusterer:
    """行为模式聚类器 — 简化DBSCAN实现"""

    def __init__(self, epsilon: float = CLUSTER_EPSILON,
                 min_samples: int = MIN_CLUSTER_SIZE):
        self._epsilon = epsilon
        self._min_samples = min_samples

    def cluster(self, sequences: List[BehaviorSequence]) -> Dict[int, List[int]]:
        """对行为序列进行聚类, 返回 {cluster_id: [sequence_indices]}"""
        n = len(sequences)
        if n == 0:
            return {}
        logger.debug(f"Clustering {n} sequences with eps={self._epsilon}")
        # 计算距离矩阵
        sim_matrix = [[0.0] * n for _ in range(n)]
        for i in range(n):
            for j in range(i + 1, n):
                sim = SequenceSimilarity.normalized_similarity(sequences[i], sequences[j])
                sim_matrix[i][j] = sim
                sim_matrix[j][i] = sim
        # 简化DBSCAN: 找邻居
        labels = [-1] * n
        cluster_id = 0
        visited = set()
        for i in range(n):
            if i in visited:
                continue
            visited.add(i)
            neighbors = [j for j in range(n)
                         if j != i and sim_matrix[i][j] >= (1.0 - self._epsilon)]
            if len(neighbors) < self._min_samples:
                continue
            labels[i] = cluster_id
            seed_set = list(neighbors)
            while seed_set:
                q = seed_set.pop(0)
                if q not in visited:
                    visited.add(q)
                    q_neighbors = [j for j in range(n)
                                   if j != q and sim_matrix[q][j] >= (1.0 - self._epsilon)]
                    if len(q_neighbors) >= self._min_samples:
                        seed_set.extend([x for x in q_neighbors if x not in visited])
                if labels[q] == -1:
                    labels[q] = cluster_id
            cluster_id += 1
        # 组织结果
        clusters: Dict[int, List[int]] = defaultdict(list)
        for idx, label in enumerate(labels):
            if label >= 0:
                clusters[label].append(idx)
        logger.info(f"Found {len(clusters)} clusters from {n} sequences")
        return dict(clusters)


class TimelineParser:
    """时间线解析器 — 从Seraphine getGameDetailByGameId提取事件序列

    参考Seraphine/app/lol/connector.py:
        async def getGameDetailByGameId(self, gameId):
            res = await self.__get(f"/lol-match-history/v1/games/{gameId}")
            return res
    """

    @staticmethod
    def parse_riot_timeline(game_detail: Dict[str, Any],
                            target_puuid: str) -> List[TimelineEvent]:
        """解析Riot API返回的对局详情中的时间线"""
        events = []
        participants = game_detail.get("participants", [])
        # 找到目标玩家的participantId
        target_pid = None
        for p in participants:
            if p.get("puuid") == target_puuid:
                target_pid = p.get("participantId")
                break
        if target_pid is None:
            logger.warning(f"Target puuid {target_puuid[:8]}... not found in game")
            return events
        # 解析timeline frames
        timeline = game_detail.get("timeline", {})
        frames = timeline.get("frames", [])
        for frame in frames:
            frame_events = frame.get("events", [])
            for raw_event in frame_events:
                event_type = EventType.from_riot_type(raw_event.get("type", ""))
                if event_type is None:
                    continue
                # 只保留与目标玩家相关的事件
                pid = raw_event.get("participantId", raw_event.get("creatorId"))
                victim = raw_event.get("victimId")
                assists = raw_event.get("assistingParticipantIds", [])
                is_related = (
                    pid == target_pid
                    or victim == target_pid
                    or target_pid in (assists or [])
                )
                if not is_related:
                    continue
                pos = raw_event.get("position", {})
                te = TimelineEvent(
                    timestamp_ms=raw_event.get("timestamp", 0),
                    event_type=event_type,
                    participant_id=pid or 0,
                    position_x=pos.get("x"),
                    position_y=pos.get("y"),
                    victim_id=victim,
                    assisting_ids=assists,
                    item_id=raw_event.get("itemId"),
                    monster_type=raw_event.get("monsterType"),
                    building_type=raw_event.get("buildingType"),
                    ward_type=raw_event.get("wardType"),
                    skill_slot=raw_event.get("skillSlot"),
                    level=raw_event.get("level"),
                )
                events.append(te)
        events.sort(key=lambda e: e.timestamp_ms)
        logger.debug(f"Parsed {len(events)} events for participant {target_pid}")
        return events


class SlidingWindowExtractor:
    """滑动窗口提取器 — 从时间线事件中提取固定窗口的行为序列"""

    def __init__(self, window_size: int = WINDOW_SIZE_DEFAULT,
                 step_size: int = 1,
                 max_sequence_length: int = MAX_SEQUENCE_LENGTH):
        self._window_size = window_size
        self._step_size = step_size
        self._max_len = max_sequence_length

    def extract(self, events: List[TimelineEvent],
                participant_id: int,
                game_id: int) -> List[BehaviorSequence]:
        """从事件列表中提取行为序列"""
        if len(events) < self._window_size:
            return []
        sequences = []
        for i in range(0, len(events) - self._window_size + 1, self._step_size):
            window = events[i:i + self._window_size]
            if len(window) > self._max_len:
                window = window[:self._max_len]
            seq = BehaviorSequence(
                events=window,
                start_minute=window[0].minute,
                end_minute=window[-1].minute,
                participant_id=participant_id,
                game_id=game_id,
            )
            sequences.append(seq)
        logger.debug(f"Extracted {len(sequences)} sequences from {len(events)} events")
        return sequences


class PatternClassifier:
    """模式分类器 — 将聚类后的行为模式分类到PatternCategory"""

    @staticmethod
    def classify(cluster_sequences: List[BehaviorSequence]) -> PatternCategory:
        """根据聚类内序列的特征分布判断模式类别"""
        if not cluster_sequences:
            return PatternCategory.PASSIVE_FARMING
        # 统计事件类型分布
        event_counts: Dict[EventType, int] = defaultdict(int)
        avg_minute = 0.0
        total_events = 0
        for seq in cluster_sequences:
            for e in seq.events:
                event_counts[e.event_type] += 1
                total_events += 1
            avg_minute += seq.start_minute
        avg_minute /= len(cluster_sequences)
        if total_events == 0:
            return PatternCategory.PASSIVE_FARMING
        # 计算各类型比例
        kill_ratio = event_counts[EventType.CHAMPION_KILL] / total_events
        ward_ratio = (event_counts[EventType.WARD_PLACED] +
                      event_counts[EventType.WARD_KILL]) / total_events
        obj_ratio = (event_counts[EventType.ELITE_MONSTER_KILL] +
                     event_counts[EventType.BUILDING_KILL]) / total_events
        item_ratio = event_counts[EventType.ITEM_PURCHASED] / total_events
        # 分类规则
        if avg_minute < 10 and kill_ratio > 0.3:
            return PatternCategory.AGGRESSIVE_EARLY
        if ward_ratio > 0.25:
            return PatternCategory.VISION_CONTROL
        if obj_ratio > 0.2:
            return PatternCategory.OBJECTIVE_FOCUSED
        if kill_ratio > 0.2 and avg_minute > 15:
            return PatternCategory.TEAM_FIGHT
        if item_ratio > 0.4:
            return PatternCategory.PASSIVE_FARMING
        return PatternCategory.PASSIVE_FARMING


class HistoricalPatternRecognizer:
    """
    历史模式识别器 — 核心类

    查看 Seraphine/app/lol/connector.py 上现有 LCU API connector 的实现方式,
    理解其 retry + PastRequest 模式, 特别是代码和接口如何分离。
    从 connector.needLcu + retry 这个好例子开始。
    然后, 遵循该模式实现 HistoricalPatternRecognizer,
    让 operatorRL 可以从历史对局中识别对手行为模式,
    并能将模式转化为可操作的情报。
    接着 PatternClusterer 引入 DBSCAN 聚类,
    使 PatternClassifier 能够对模式分类,
    同时 PatternCache 优化缓存策略。
    随后 SlidingWindowExtractor 整合时间线数据,
    令 TimelineParser 支持 Seraphine API 格式解析,
    进而 SequenceSimilarity 增强 DTW 相似度计算。
    最终 HistoricalPatternRecognizer 完善编排逻辑,
    确保输出兼容 M906-M925 历史数据模块,
    全面升级模式识别层以达成行为预测目标。
    """

    def __init__(self, cache_max_size: int = MAX_PATTERNS_CACHED,
                 cache_ttl: int = PATTERN_TTL_SECONDS,
                 window_size: int = WINDOW_SIZE_DEFAULT,
                 cluster_epsilon: float = CLUSTER_EPSILON):
        self._cache = PatternCache(cache_max_size, cache_ttl)
        self._extractor = SlidingWindowExtractor(window_size)
        self._clusterer = PatternClusterer(cluster_epsilon)
        self._classifier = PatternClassifier()
        self._pattern_store: Dict[str, RecognizedPattern] = {}
        self._analysis_count = 0
        self._total_events_processed = 0
        self._lock = asyncio.Lock()
        logger.info(f"HistoricalPatternRecognizer initialized: "
                    f"window={window_size}, eps={cluster_epsilon}")

    async def analyze_opponent_history(
        self,
        puuid: str,
        game_details: List[Dict[str, Any]],
        game_outcomes: Optional[Dict[int, bool]] = None,
    ) -> List[RecognizedPattern]:
        """
        分析对手历史对局, 识别行为模式

        Args:
            puuid: 对手的puuid (来自Seraphine getSummonerByPuuid)
            game_details: 对局详情列表 (来自Seraphine getGameDetailByGameId)
            game_outcomes: {game_id: won} 对局胜负映射

        Returns:
            识别到的行为模式列表, 按置信度降序
        """
        async with self._lock:
            self._analysis_count += 1
            analysis_id = self._analysis_count
        logger.info(f"[Analysis #{analysis_id}] Starting for puuid={puuid[:8]}... "
                    f"with {len(game_details)} games")
        start_time = time.monotonic()
        # Phase 1: 解析所有对局的时间线事件
        all_sequences: List[BehaviorSequence] = []
        for game in game_details:
            game_id = game.get("gameId", 0)
            events = TimelineParser.parse_riot_timeline(game, puuid)
            self._total_events_processed += len(events)
            # 找到对手的participantId
            pid = None
            for p in game.get("participants", []):
                if p.get("puuid") == puuid:
                    pid = p.get("participantId", 0)
                    break
            if pid is None:
                continue
            sequences = self._extractor.extract(events, pid, game_id)
            all_sequences.extend(sequences)
        logger.info(f"[Analysis #{analysis_id}] Extracted {len(all_sequences)} sequences")
        if not all_sequences:
            return []
        # Phase 2: 去重
        seen_hashes: Set[str] = set()
        unique_sequences: List[BehaviorSequence] = []
        for seq in all_sequences:
            h = seq.signature_hash()
            if h not in seen_hashes:
                seen_hashes.add(h)
                unique_sequences.append(seq)
        logger.debug(f"Deduplication: {len(all_sequences)} -> {len(unique_sequences)}")
        # Phase 3: 聚类
        clusters = self._clusterer.cluster(unique_sequences)
        # Phase 4: 每个聚类生成一个模式
        patterns: List[RecognizedPattern] = []
        for cluster_id, indices in clusters.items():
            cluster_seqs = [unique_sequences[i] for i in indices]
            category = self._classifier.classify(cluster_seqs)
            # 选择代表性序列(事件最多的)
            representative = max(cluster_seqs, key=lambda s: len(s.events))
            # 计算聚类统计
            minutes = [s.start_minute for s in cluster_seqs]
            avg_min = statistics.mean(minutes) if minutes else 0.0
            std_min = statistics.stdev(minutes) if len(minutes) > 1 else 0.0
            # 计算成功率 (如果有胜负信息)
            success_rate = 0.5
            if game_outcomes:
                wins = sum(1 for s in cluster_seqs
                           if game_outcomes.get(s.game_id, False))
                success_rate = wins / len(cluster_seqs) if cluster_seqs else 0.5
            # 计算置信度
            confidence = min(1.0, len(cluster_seqs) / 10.0) * 0.7
            confidence += 0.3 * (1.0 - std_min / max(avg_min, 1.0))
            confidence = max(0.0, min(1.0, confidence))
            pattern_id = f"PAT-{puuid[:6]}-{cluster_id:03d}-{category.name[:4]}"
            pattern = RecognizedPattern(
                pattern_id=pattern_id,
                category=category,
                representative_sequence=representative,
                occurrence_count=len(cluster_seqs),
                confidence=confidence,
                game_ids={s.game_id for s in cluster_seqs},
                avg_minute=avg_min,
                std_minute=std_min,
                success_rate=success_rate,
                tags=[category.name.lower(), f"min_{int(avg_min)}"],
            )
            patterns.append(pattern)
            # 缓存
            self._cache.put(pattern_id, pattern)
            self._pattern_store[pattern_id] = pattern
        # 按置信度排序
        patterns.sort(key=lambda p: p.confidence, reverse=True)
        elapsed = time.monotonic() - start_time
        logger.info(f"[Analysis #{analysis_id}] Completed: {len(patterns)} patterns "
                    f"in {elapsed:.3f}s, cache={self._cache.stats}")
        return patterns

    async def get_pattern_by_id(self, pattern_id: str) -> Optional[RecognizedPattern]:
        """按ID获取已缓存的模式"""
        cached = self._cache.get(pattern_id)
        if cached:
            return cached
        return self._pattern_store.get(pattern_id)

    async def get_patterns_for_puuid(self, puuid: str) -> List[RecognizedPattern]:
        """获取指定puuid的所有已知模式"""
        prefix = f"PAT-{puuid[:6]}-"
        return [p for pid, p in self._pattern_store.items()
                if pid.startswith(prefix)]

    async def compare_patterns(
        self, puuid1: str, puuid2: str
    ) -> Dict[str, Any]:
        """比较两个玩家的行为模式相似度"""
        p1 = await self.get_patterns_for_puuid(puuid1)
        p2 = await self.get_patterns_for_puuid(puuid2)
        if not p1 or not p2:
            return {"similarity": 0.0, "common_categories": []}
        cats1 = {p.category for p in p1}
        cats2 = {p.category for p in p2}
        common = cats1 & cats2
        jaccard = len(common) / len(cats1 | cats2) if (cats1 | cats2) else 0.0
        return {
            "similarity": round(jaccard, 4),
            "common_categories": [c.name for c in common],
            "unique_to_1": [c.name for c in cats1 - cats2],
            "unique_to_2": [c.name for c in cats2 - cats1],
        }

    def get_diagnostics(self) -> Dict[str, Any]:
        """获取诊断信息"""
        return {
            "analysis_count": self._analysis_count,
            "total_events_processed": self._total_events_processed,
            "pattern_store_size": len(self._pattern_store),
            "cache_stats": self._cache.stats,
        }

    async def reset(self) -> None:
        """重置所有状态"""
        async with self._lock:
            self._cache.clear()
            self._pattern_store.clear()
            self._analysis_count = 0
            self._total_events_processed = 0
        logger.info("HistoricalPatternRecognizer reset complete")


# ============================================================
# 模块自测入口
# ============================================================

async def _self_test():
    """模块自测 — 使用模拟数据验证核心逻辑"""
    logger.info("Starting M966 HistoricalPatternRecognizer self-test")
    recognizer = HistoricalPatternRecognizer(window_size=3, cluster_epsilon=0.4)
    # 模拟对局数据 (简化的Seraphine getGameDetailByGameId返回格式)
    mock_puuid = "test-puuid-12345678"
    mock_games = []
    for gid in range(5):
        events = []
        for t in range(0, 30, 2):
            events.append({
                "type": "CHAMPION_KILL" if t % 6 == 0 else "ITEM_PURCHASED",
                "timestamp": t * 60000,
                "participantId": 1,
                "position": {"x": 7000 + t * 100, "y": 8000 + t * 50},
            })
        mock_games.append({
            "gameId": 1000 + gid,
            "participants": [{"puuid": mock_puuid, "participantId": 1}],
            "timeline": {"frames": [{"events": events}]},
        })
    patterns = await recognizer.analyze_opponent_history(
        mock_puuid, mock_games, {1000 + i: i % 2 == 0 for i in range(5)}
    )
    logger.info(f"Self-test found {len(patterns)} patterns")
    for p in patterns:
        logger.info(f"  {p.pattern_id}: {p.category.name} "
                    f"(conf={p.confidence:.3f}, n={p.occurrence_count})")
    diag = recognizer.get_diagnostics()
    logger.info(f"Diagnostics: {json.dumps(diag, indent=2)}")
    assert recognizer._analysis_count == 1
    logger.info("M966 self-test PASSED")


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    asyncio.run(_self_test())
