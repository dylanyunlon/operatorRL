"""
M1006 HistoricalMatchCrawler — 历史对局爬取器
==============================================
查看 Seraphine/app/lol/connector.py 上现有 LCU API connector 的实现方式,
理解其模式, 特别是 retry 装饰器和 PastRequest 是如何与 HTTP session 分离的。
从 M906 SeraphineConnectorBridge 的 retry + PastRequest 这个好例子开始。
然后, 遵循该模式实现 HistoricalMatchCrawler (M1006),
让 operatorRL 可以批量爬取指定召唤师的历史对局 ID 列表, 并能分页遍历全部对局。
接着 FiddlerNetworkBridge (M1007) 引入 Fiddler MCP 网络捕获桥接,
使 MatchTimelineDeserializer (M1008) 能够反序列化对局时间线事件,
同时 PlayerProfileAggregator (M1009) 优化多区多账号合并。
随后 ChampionMasteryIndexer (M1010) 整合英雄精通度索引,
令 RankTierClassifier (M1011) 支持段位分类与历史段位追踪,
进而 MatchOutcomeCorrelator (M1012) 增强胜负关联分析。
最终 UnifiedHistoricalGateway (M1025) 完善统一网关,
确保全部模块兼容 M906-M925 历史情报层 + M866-M885 实时系统,
全面升级历史数据获取层以达成完整的赛前+赛中AI辅助决策目标。

技术决策: 使用 Fiddler 网络捕获 vs 视觉捕获
---------------------------------------------
选择 Fiddler 原生网络捕获方案:
1. 网络包数据无幻觉 — 直接读取 HTTP/HTTPS 协议内容, 数据100%准确
2. 符合逆向工程技术方向 — Proxifier 配置游戏进程走 Fiddler 代理
3. 延迟更低 — 网络包捕获 < 1ms, 视觉OCR > 100ms
4. Fiddler MCP Server 可直接集成到 operatorRL 的 MCP 网关
5. Riot Games Live Client Data API 在 localhost:2999 开放, 天然适合代理捕获
"""

import asyncio
import hashlib
import json
import logging
import os
import re
import time
from collections import deque, defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from enum import Enum
from pathlib import Path
from typing import (
    Any, Callable, Coroutine, Deque, Dict, List,
    Optional, Set, Tuple, Union
)

# 本地导入
try:
    from logging_system import get_module_logger, get_collector, traced
except ImportError:
    # Fallback for standalone testing
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    from logging_system import get_module_logger, get_collector, traced

# ─── 常量 ────────────────────────────────────────────────────────────────────

MODULE_ID = "M1006"
MODULE_NAME = "HistoricalMatchCrawler"
TAG = f"[{MODULE_ID}]"

# Riot API 端点模板
RIOT_API_BASE = "https://{region}.api.riotgames.com"
MATCH_V5_BASE = "https://{routing}.api.riotgames.com/lol/match/v5"
SUMMONER_V4_BASE = "https://{region}.api.riotgames.com/lol/summoner/v4"

# LCU (League Client Update) 本地端点 — 来自 Seraphine connector 模式
LCU_MATCH_HISTORY = "/lol-match-history/v1/products/lol/{puuid}/matches"
LCU_GAME_DETAIL = "/lol-match-history/v1/games/{gameId}"

# SGP (Service Gateway Proxy) 端点 — Seraphine 的 getSummonerGamesByPuuidViaSGP
SGP_MATCH_QUERY = "/match-history-query/v1/products/lol/player/{puuid}/SUMMARY"

# 速率限制
RATE_LIMIT_PER_SECOND = 20
RATE_LIMIT_PER_2MIN = 100
PAGE_SIZE = 20
MAX_RETRIES = 5
RETRY_BACKOFF_BASE = 1.5

# 区域路由映射 (platform → routing)
REGION_ROUTING = {
    "na1": "americas", "br1": "americas", "la1": "americas", "la2": "americas",
    "oc1": "sea", "ph2": "sea", "sg2": "sea", "th2": "sea", "tw2": "sea", "vn2": "sea",
    "kr": "asia", "jp1": "asia",
    "euw1": "europe", "eun1": "europe", "tr1": "europe", "ru": "europe",
}

logger = get_module_logger(MODULE_ID)


# ─── 数据结构 ─────────────────────────────────────────────────────────────────

class CrawlStatus(Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    RATE_LIMITED = "rate_limited"


@dataclass
class PastRequest:
    """
    请求记录 — 直接借鉴 Seraphine/app/lol/connector.py 的 PastRequest。
    
    记录每次 HTTP 请求的完整上下文:
    - func: 调用的方法名 (非对象引用, 与 Seraphine 一致)
    - params_dict: 参数字典
    - response: 响应内容
    - timestamp: 请求时间戳
    - status_code: HTTP 状态码
    - duration_ms: 耗时
    """
    func: str
    params_dict: Dict[str, Any]
    kwargs: Dict[str, Any] = field(default_factory=dict)
    response: Optional[Any] = None
    timestamp: float = field(default_factory=time.time)
    status_code: int = 0
    duration_ms: float = 0.0
    retry_count: int = 0

    def __str__(self) -> str:
        attrs = [f"{k}={v!r}" for k, v in asdict(self).items() if v is not None]
        return f"PastRequest({', '.join(attrs)})"

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["timestamp_iso"] = datetime.fromtimestamp(
            self.timestamp, tz=timezone.utc
        ).isoformat()
        return d


@dataclass
class MatchReference:
    """对局引用 — 最小对局元数据"""
    match_id: str
    game_id: int
    platform_id: str
    game_creation: int  # epoch ms
    game_duration: int  # seconds
    queue_id: int
    champion_id: int
    role: str = ""
    lane: str = ""
    season: int = 0
    is_fetched_detail: bool = False

    @property
    def game_creation_dt(self) -> datetime:
        return datetime.fromtimestamp(self.game_creation / 1000, tz=timezone.utc)

    @property
    def game_version_major(self) -> str:
        """从 match_id 推断版本 (近似)"""
        return ""

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["game_creation_iso"] = self.game_creation_dt.isoformat()
        return d


@dataclass
class CrawlTask:
    """爬取任务"""
    puuid: str
    region: str
    beg_index: int = 0
    end_index: int = PAGE_SIZE
    status: CrawlStatus = CrawlStatus.PENDING
    created_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None
    match_count: int = 0
    error: Optional[str] = None
    retries: int = 0

    @property
    def task_id(self) -> str:
        return hashlib.md5(
            f"{self.puuid}:{self.beg_index}:{self.end_index}".encode()
        ).hexdigest()[:12]


@dataclass
class CrawlProgress:
    """爬取进度"""
    puuid: str
    total_matches_found: int = 0
    total_matches_fetched: int = 0
    total_details_fetched: int = 0
    pages_completed: int = 0
    pages_total: int = 0
    start_time: float = field(default_factory=time.time)
    last_update: float = field(default_factory=time.time)
    status: CrawlStatus = CrawlStatus.PENDING
    errors: List[str] = field(default_factory=list)

    @property
    def elapsed_seconds(self) -> float:
        return time.time() - self.start_time

    @property
    def progress_pct(self) -> float:
        if self.pages_total == 0:
            return 0.0
        return min(100.0, (self.pages_completed / self.pages_total) * 100)


# ─── 速率限制器 ───────────────────────────────────────────────────────────────

class AdaptiveRateLimiter:
    """
    自适应速率限制器 — 遵守 Riot API 的双层速率限制。
    
    Riot API 限制:
    - 20 requests / 1 second
    - 100 requests / 2 minutes
    
    当触发 429 时自动降速, 恢复后逐步提速。
    """

    def __init__(
        self,
        per_second: int = RATE_LIMIT_PER_SECOND,
        per_2min: int = RATE_LIMIT_PER_2MIN,
    ):
        self._per_second = per_second
        self._per_2min = per_2min
        self._second_window: Deque[float] = deque()
        self._2min_window: Deque[float] = deque()
        self._backoff_until: float = 0.0
        self._consecutive_429: int = 0
        self._total_requests: int = 0
        self._total_throttled: int = 0

    async def acquire(self):
        """获取请求许可 — 如果超限则等待"""
        now = time.monotonic()

        # 检查 429 退避
        if now < self._backoff_until:
            wait = self._backoff_until - now
            logger.debug(f"Rate limited, waiting {wait:.1f}s")
            self._total_throttled += 1
            await asyncio.sleep(wait)
            now = time.monotonic()

        # 清理过期窗口
        while self._second_window and self._second_window[0] < now - 1.0:
            self._second_window.popleft()
        while self._2min_window and self._2min_window[0] < now - 120.0:
            self._2min_window.popleft()

        # 每秒限制
        if len(self._second_window) >= self._per_second:
            wait = self._second_window[0] + 1.0 - now
            if wait > 0:
                await asyncio.sleep(wait)
                now = time.monotonic()

        # 2分钟限制
        if len(self._2min_window) >= self._per_2min:
            wait = self._2min_window[0] + 120.0 - now
            if wait > 0:
                logger.info(f"2-minute rate limit reached, waiting {wait:.1f}s")
                await asyncio.sleep(wait)
                now = time.monotonic()

        self._second_window.append(now)
        self._2min_window.append(now)
        self._total_requests += 1

    def report_429(self, retry_after: float = 1.0):
        """报告 429 响应 — 触发退避"""
        self._consecutive_429 += 1
        backoff = retry_after * (RETRY_BACKOFF_BASE ** self._consecutive_429)
        self._backoff_until = time.monotonic() + backoff
        logger.warning(f"429 received, backing off {backoff:.1f}s (consecutive: {self._consecutive_429})")

    def report_success(self):
        """报告成功响应 — 重置连续 429 计数"""
        self._consecutive_429 = 0

    @property
    def stats(self) -> Dict[str, Any]:
        return {
            "total_requests": self._total_requests,
            "total_throttled": self._total_throttled,
            "consecutive_429": self._consecutive_429,
            "second_window_size": len(self._second_window),
            "2min_window_size": len(self._2min_window),
        }


# ─── HTTP 会话抽象 ────────────────────────────────────────────────────────────

class HttpSessionAbstract:
    """
    HTTP 会话抽象层 — 解耦 HTTP 实现与业务逻辑。
    
    设计参考 Seraphine 的 connector 模式:
    connector.lcuSess (LCU 本地会话) 和 connector.sgpSess (SGP 远程会话)
    是分开管理的, 各自有独立的 base_url / auth / headers。
    
    我们同样分离:
    - RiotApiSession: 访问 Riot 官方 API (需要 API Key)
    - LcuSession: 访问本地 LCU (需要 port + token)
    - FiddlerSession: 通过 Fiddler MCP 读取捕获的流量
    """

    def __init__(self, base_url: str, headers: Optional[Dict] = None):
        self.base_url = base_url.rstrip("/")
        self.headers = headers or {}
        self._closed = False

    async def get(self, path: str, params: Optional[Dict] = None) -> Dict:
        """GET 请求 — 子类实现"""
        raise NotImplementedError

    async def close(self):
        """关闭会话"""
        self._closed = True


class MockSession(HttpSessionAbstract):
    """
    模拟会话 — 用于离线测试和日志系统自检。
    
    返回结构化的模拟数据, 模拟 Riot API 和 LCU 的响应格式。
    """

    def __init__(self, base_url: str = "http://mock"):
        super().__init__(base_url)
        self._call_count = 0

    async def get(self, path: str, params: Optional[Dict] = None) -> Dict:
        self._call_count += 1
        await asyncio.sleep(0.01)  # 模拟网络延迟

        # 模拟 match history 响应
        if "/matches" in path or "/SUMMARY" in path:
            beg = int((params or {}).get("begIndex", 0))
            end = int((params or {}).get("endIndex", PAGE_SIZE))
            return self._mock_match_list(beg, end)
        
        # 模拟 game detail 响应
        if "/games/" in path:
            game_id = path.split("/")[-1]
            return self._mock_game_detail(int(game_id) if game_id.isdigit() else 1)

        # 模拟 summoner 响应
        if "/summoners/" in path or "/by-puuid/" in path:
            return self._mock_summoner()

        return {"status": "ok", "path": path}

    def _mock_match_list(self, beg: int, end: int) -> Dict:
        """模拟 getSummonerGamesByPuuid 的响应格式 (Seraphine 兼容)"""
        games = []
        for i in range(beg, min(end, beg + PAGE_SIZE)):
            games.append({
                "gameId": 7000000000 + i,
                "platformId": "NA1",
                "gameCreation": int((time.time() - i * 3600) * 1000),
                "gameDuration": 1800 + (i % 600),
                "queueId": 420,  # Ranked Solo
                "participants": [
                    {"championId": 1 + (i % 160), "teamId": 100},
                ],
                "participantIdentities": [
                    {"participantId": 1, "player": {
                        "summonerName": f"TestPlayer{i}",
                        "puuid": f"mock-puuid-{i:04d}",
                    }},
                ],
            })
        return {
            "games": {
                "games": games,
                "gameCount": len(games),
                "gameBeginDate": "",
                "gameEndDate": "",
                "gameIndexBegin": beg,
                "gameIndexEnd": end,
            }
        }

    def _mock_game_detail(self, game_id: int) -> Dict:
        """模拟 getGameDetailByGameId 的响应"""
        return {
            "gameId": game_id,
            "platformId": "NA1",
            "gameCreation": int(time.time() * 1000) - 86400000,
            "gameDuration": 1923,
            "queueId": 420,
            "gameMode": "CLASSIC",
            "gameType": "MATCHED_GAME",
            "teams": [
                {"teamId": 100, "win": "Win", "objectives": {
                    "baron": {"kills": 1}, "dragon": {"kills": 3},
                    "tower": {"kills": 8}, "inhibitor": {"kills": 2},
                }},
                {"teamId": 200, "win": "Fail", "objectives": {
                    "baron": {"kills": 0}, "dragon": {"kills": 1},
                    "tower": {"kills": 3}, "inhibitor": {"kills": 0},
                }},
            ],
            "participants": [
                {
                    "participantId": j + 1,
                    "teamId": 100 if j < 5 else 200,
                    "championId": 1 + j,
                    "stats": {
                        "kills": 5 + j, "deaths": 3 + j % 3,
                        "assists": 7 + j, "totalMinionsKilled": 180 + j * 10,
                        "goldEarned": 12000 + j * 500,
                        "visionScore": 20 + j * 3,
                        "win": j < 5,
                    },
                    "timeline": {
                        "role": ["SOLO", "NONE", "SOLO", "DUO_CARRY", "DUO_SUPPORT",
                                 "SOLO", "NONE", "SOLO", "DUO_CARRY", "DUO_SUPPORT"][j],
                        "lane": ["TOP", "JUNGLE", "MID", "BOTTOM", "BOTTOM",
                                 "TOP", "JUNGLE", "MID", "BOTTOM", "BOTTOM"][j],
                    },
                }
                for j in range(10)
            ],
        }

    def _mock_summoner(self) -> Dict:
        return {
            "id": "mock-summoner-id",
            "accountId": "mock-account-id",
            "puuid": "mock-puuid-0001",
            "name": "MockSummoner",
            "summonerLevel": 300,
            "profileIconId": 4567,
        }


# ─── retry 装饰器 ─────────────────────────────────────────────────────────────

def retry(count: int = MAX_RETRIES, retry_sep: float = 0.5):
    """
    重试装饰器 — 直接借鉴 Seraphine/app/lol/connector.py 的 @retry。
    
    与 Seraphine 的差异:
    1. 增加指数退避 (Seraphine 用固定间隔)
    2. 记录 PastRequest 到 request_history deque
    3. 支持 429 状态码的特殊处理
    """
    def decorator(func):
        import functools
        import inspect

        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            # 参数解析 — 与 Seraphine 一致的参数字典构建
            func_params = inspect.signature(func).parameters
            param_names = list(func_params.keys())
            tmp_args = args
            if param_names and param_names[0] == "self":
                param_names = param_names[1:]
                tmp_args = args[1:]
            params_dict = {p: a for p, a in zip(param_names, tmp_args)}

            last_error = None
            for attempt in range(count):
                req = PastRequest(
                    func=func.__name__,
                    params_dict=params_dict,
                    kwargs=kwargs,
                )
                start = time.monotonic()
                try:
                    result = await func(*args, **kwargs)
                    req.duration_ms = (time.monotonic() - start) * 1000
                    req.response = "<success>"
                    req.status_code = 200
                    
                    # 记录到实例的 request_history (如果 self 有)
                    if args and hasattr(args[0], "request_history"):
                        args[0].request_history.append(req)
                    
                    return result
                except RateLimitError as e:
                    req.duration_ms = (time.monotonic() - start) * 1000
                    req.status_code = 429
                    req.retry_count = attempt + 1
                    last_error = e
                    wait = retry_sep * (RETRY_BACKOFF_BASE ** attempt)
                    logger.warning(
                        f"{TAG} {func.__name__} rate limited (attempt {attempt+1}/{count}), "
                        f"waiting {wait:.1f}s"
                    )
                    await asyncio.sleep(wait)
                except Exception as e:
                    req.duration_ms = (time.monotonic() - start) * 1000
                    req.status_code = getattr(e, "status_code", 500)
                    req.retry_count = attempt + 1
                    last_error = e
                    if attempt < count - 1:
                        wait = retry_sep * (RETRY_BACKOFF_BASE ** attempt)
                        logger.warning(
                            f"{TAG} {func.__name__} failed (attempt {attempt+1}/{count}): {e}, "
                            f"retrying in {wait:.1f}s"
                        )
                        await asyncio.sleep(wait)

            raise CrawlerError(
                f"{func.__name__} failed after {count} retries: {last_error}"
            ) from last_error

        return wrapper
    return decorator


# ─── 异常 ─────────────────────────────────────────────────────────────────────

class CrawlerError(Exception):
    """爬取器通用错误"""
    pass

class RateLimitError(CrawlerError):
    """429 速率限制错误"""
    def __init__(self, retry_after: float = 1.0):
        self.retry_after = retry_after
        super().__init__(f"Rate limited, retry after {retry_after}s")

class SessionNotReadyError(CrawlerError):
    """会话未就绪"""
    pass


# ─── 核心爬取器 ───────────────────────────────────────────────────────────────

class HistoricalMatchCrawler:
    """
    历史对局爬取器 — M1006 核心类。
    
    职责:
    1. 通过 LCU / Riot API / SGP 获取召唤师历史对局列表
    2. 分页遍历全部对局
    3. 获取对局详情
    4. 管理速率限制
    5. 记录所有请求到 PastRequest 队列
    
    使用模式 (参考 Seraphine connector):
    ```python
    crawler = HistoricalMatchCrawler(session=MockSession())
    await crawler.initialize()
    matches = await crawler.crawl_summoner_matches("puuid-xxx", region="na1")
    details = await crawler.fetch_match_details(matches[:10])
    ```
    """

    def __init__(
        self,
        session: Optional[HttpSessionAbstract] = None,
        rate_limiter: Optional[AdaptiveRateLimiter] = None,
        cache_dir: Optional[Path] = None,
    ):
        self.session = session or MockSession()
        self.rate_limiter = rate_limiter or AdaptiveRateLimiter()
        self.cache_dir = cache_dir or Path(__file__).parent / "cache" / "matches"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        self.request_history: Deque[PastRequest] = deque(maxlen=1000)
        self._initialized = False
        self._match_cache: Dict[str, MatchReference] = {}
        self._detail_cache: Dict[int, Dict] = {}
        self._crawl_progress: Dict[str, CrawlProgress] = {}
        self._lock = asyncio.Lock()

        self.collector = get_collector()

    @traced(MODULE_ID)
    async def initialize(self) -> bool:
        """初始化爬取器 — 验证会话连通性"""
        start = time.monotonic()
        try:
            # 测试会话
            test_result = await self.session.get("/test")
            self._initialized = True
            duration = (time.monotonic() - start) * 1000
            self.collector.record_init(MODULE_ID, "ok", duration, {
                "session_type": type(self.session).__name__,
                "cache_dir": str(self.cache_dir),
            })
            logger.info(f"{TAG} Initialized with {type(self.session).__name__}")
            return True
        except Exception as e:
            self._initialized = False
            duration = (time.monotonic() - start) * 1000
            self.collector.record_init(MODULE_ID, "error", duration, {
                "error": str(e)
            })
            logger.error(f"{TAG} Initialization failed: {e}")
            return False

    @retry(count=MAX_RETRIES, retry_sep=0.5)
    @traced(MODULE_ID)
    async def fetch_match_list(
        self,
        puuid: str,
        beg_index: int = 0,
        end_index: int = PAGE_SIZE,
        use_sgp: bool = False,
    ) -> List[MatchReference]:
        """
        获取对局列表 — 对应 Seraphine 的 getSummonerGamesByPuuid / getSummonerGamesByPuuidViaSGP。
        
        Args:
            puuid: 召唤师 PUUID
            beg_index: 起始索引
            end_index: 结束索引
            use_sgp: 是否使用 SGP 端点 (更快但数据较少)
        
        Returns:
            MatchReference 列表
        """
        await self.rate_limiter.acquire()

        if use_sgp:
            path = SGP_MATCH_QUERY.format(puuid=puuid)
        else:
            path = LCU_MATCH_HISTORY.format(puuid=puuid)

        params = {"begIndex": beg_index, "endIndex": end_index}
        response = await self.session.get(path, params=params)

        # 解析响应 — 兼容 Seraphine 的嵌套格式
        games_data = response.get("games", response)
        if isinstance(games_data, dict):
            games_data = games_data.get("games", [])

        matches = []
        for game in games_data:
            ref = MatchReference(
                match_id=f"{game.get('platformId', 'NA1')}_{game['gameId']}",
                game_id=game["gameId"],
                platform_id=game.get("platformId", "NA1"),
                game_creation=game.get("gameCreation", 0),
                game_duration=game.get("gameDuration", 0),
                queue_id=game.get("queueId", 0),
                champion_id=self._extract_champion_id(game, puuid),
                role=self._extract_role(game, puuid),
                lane=self._extract_lane(game, puuid),
            )
            matches.append(ref)
            self._match_cache[ref.match_id] = ref

        self.rate_limiter.report_success()
        logger.info(f"{TAG} Fetched {len(matches)} matches for {puuid[:8]}... [{beg_index}:{end_index}]")
        return matches

    @retry(count=MAX_RETRIES, retry_sep=0.5)
    @traced(MODULE_ID)
    async def fetch_match_detail(self, game_id: int) -> Dict:
        """
        获取单场对局详情 — 对应 Seraphine 的 getGameDetailByGameId。
        
        缓存策略: 先查内存 → 再查磁盘 → 最后请求 API
        """
        # 内存缓存
        if game_id in self._detail_cache:
            return self._detail_cache[game_id]

        # 磁盘缓存
        cache_file = self.cache_dir / f"{game_id}.json"
        if cache_file.exists():
            try:
                with open(cache_file, "r") as f:
                    detail = json.load(f)
                self._detail_cache[game_id] = detail
                return detail
            except (json.JSONDecodeError, IOError):
                pass

        # API 请求
        await self.rate_limiter.acquire()
        path = LCU_GAME_DETAIL.format(gameId=game_id)
        detail = await self.session.get(path)
        self.rate_limiter.report_success()

        # 缓存到内存和磁盘
        self._detail_cache[game_id] = detail
        try:
            with open(cache_file, "w") as f:
                json.dump(detail, f, ensure_ascii=False)
        except IOError as e:
            logger.warning(f"{TAG} Failed to cache detail for {game_id}: {e}")

        return detail

    @traced(MODULE_ID)
    async def crawl_summoner_matches(
        self,
        puuid: str,
        region: str = "na1",
        max_matches: int = 200,
        use_sgp: bool = False,
    ) -> List[MatchReference]:
        """
        完整爬取召唤师的历史对局 — 自动分页。
        
        类比 Seraphine/app/lol/tools.py 中的 getRecentlyGamesInfo:
        先获取初始页, 然后循环获取后续页直到无更多数据。
        """
        progress = CrawlProgress(
            puuid=puuid,
            status=CrawlStatus.IN_PROGRESS,
            pages_total=max(1, max_matches // PAGE_SIZE),
        )
        self._crawl_progress[puuid] = progress

        all_matches: List[MatchReference] = []
        beg_index = 0

        while len(all_matches) < max_matches:
            end_index = beg_index + PAGE_SIZE
            try:
                page = await self.fetch_match_list(
                    puuid, beg_index, end_index, use_sgp
                )
                if not page:
                    break

                all_matches.extend(page)
                progress.total_matches_found = len(all_matches)
                progress.pages_completed += 1
                progress.last_update = time.time()

                beg_index = end_index

                if len(page) < PAGE_SIZE:
                    break  # 最后一页

            except CrawlerError as e:
                progress.errors.append(str(e))
                logger.error(f"{TAG} Crawl page failed at [{beg_index}:{end_index}]: {e}")
                break

        progress.status = CrawlStatus.COMPLETED
        progress.total_matches_fetched = len(all_matches)
        logger.info(
            f"{TAG} Crawl completed for {puuid[:8]}...: "
            f"{len(all_matches)} matches in {progress.elapsed_seconds:.1f}s"
        )
        return all_matches

    @traced(MODULE_ID)
    async def fetch_match_details_batch(
        self,
        matches: List[MatchReference],
        concurrency: int = 5,
    ) -> List[Dict]:
        """
        批量获取对局详情 — 带并发控制。
        
        使用 asyncio.Semaphore 限制同时请求数, 避免触发速率限制。
        """
        semaphore = asyncio.Semaphore(concurrency)
        details = []

        async def fetch_one(match_ref: MatchReference) -> Optional[Dict]:
            async with semaphore:
                try:
                    return await self.fetch_match_detail(match_ref.game_id)
                except CrawlerError as e:
                    logger.warning(f"{TAG} Failed to fetch detail for {match_ref.game_id}: {e}")
                    return None

        tasks = [fetch_one(m) for m in matches]
        results = await asyncio.gather(*tasks, return_exceptions=False)

        for result in results:
            if result is not None:
                details.append(result)

        logger.info(f"{TAG} Batch fetched {len(details)}/{len(matches)} match details")
        return details

    def get_progress(self, puuid: str) -> Optional[CrawlProgress]:
        """获取爬取进度"""
        return self._crawl_progress.get(puuid)

    def get_cached_matches(self) -> Dict[str, MatchReference]:
        """获取缓存的对局引用"""
        return dict(self._match_cache)

    def get_request_history(self, last_n: int = 50) -> List[Dict]:
        """获取最近 N 条请求历史"""
        return [req.to_dict() for req in list(self.request_history)[-last_n:]]

    @property
    def stats(self) -> Dict[str, Any]:
        return {
            "initialized": self._initialized,
            "cached_matches": len(self._match_cache),
            "cached_details": len(self._detail_cache),
            "request_count": len(self.request_history),
            "rate_limiter": self.rate_limiter.stats,
            "active_crawls": len(self._crawl_progress),
        }

    # ─── 内部辅助 ──────────────────────────────────────────────────────────

    @staticmethod
    def _extract_champion_id(game: Dict, puuid: str) -> int:
        """从对局数据提取英雄 ID"""
        participants = game.get("participants", [])
        if participants:
            return participants[0].get("championId", 0)
        return 0

    @staticmethod
    def _extract_role(game: Dict, puuid: str) -> str:
        """从对局数据提取角色"""
        participants = game.get("participants", [])
        if participants:
            timeline = participants[0].get("timeline", {})
            return timeline.get("role", "")
        return ""

    @staticmethod
    def _extract_lane(game: Dict, puuid: str) -> str:
        """从对局数据提取位置"""
        participants = game.get("participants", [])
        if participants:
            timeline = participants[0].get("timeline", {})
            return timeline.get("lane", "")
        return ""


# ─── 自检与演示 ───────────────────────────────────────────────────────────────

async def _self_test():
    """M1006 自检 — 使用 MockSession 验证完整流程"""
    print(f"\n{'='*60}")
    print(f"  M1006 HistoricalMatchCrawler — 自检")
    print(f"{'='*60}")

    crawler = HistoricalMatchCrawler(session=MockSession())
    
    # 1. 初始化
    ok = await crawler.initialize()
    assert ok, "Initialization failed"
    print(f"  ✓ 初始化成功")

    # 2. 爬取对局列表
    matches = await crawler.crawl_summoner_matches(
        puuid="test-puuid-001",
        region="na1",
        max_matches=40,
    )
    assert len(matches) > 0, "No matches found"
    print(f"  ✓ 爬取 {len(matches)} 场对局")

    # 3. 获取对局详情
    details = await crawler.fetch_match_details_batch(matches[:5])
    assert len(details) > 0, "No details fetched"
    print(f"  ✓ 获取 {len(details)} 场详情")

    # 4. 统计
    stats = crawler.stats
    print(f"  ✓ 缓存: {stats['cached_matches']} matches, {stats['cached_details']} details")
    print(f"  ✓ 请求历史: {stats['request_count']} 条")

    # 5. 进度
    progress = crawler.get_progress("test-puuid-001")
    if progress:
        print(f"  ✓ 进度: {progress.progress_pct:.0f}% ({progress.elapsed_seconds:.1f}s)")

    print(f"\n  M1006 自检通过 ✓")
    return True


def main():
    """同步入口"""
    return asyncio.run(_self_test())


if __name__ == "__main__":
    main()
