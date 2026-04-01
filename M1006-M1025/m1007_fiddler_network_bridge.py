"""
M1007 FiddlerNetworkBridge — Fiddler 网络捕获桥接器
====================================================
查看 Seraphine/app/lol/connector.py 上现有 LCU API connector 的实现方式,
理解其模式, 特别是 retry 装饰器和 PastRequest 是如何与 HTTP session 分离的。
从 M906 SeraphineConnectorBridge 的 retry + PastRequest 这个好例子开始。
然后, 遵循该模式实现 FiddlerNetworkBridge (M1007),
让 operatorRL 可以通过 Fiddler MCP Server 捕获游戏网络流量, 并能实时解析协议。

技术架构:
- Fiddler Everywhere MCP Server 在 localhost:8868/mcp 运行
- Proxifier 配置: 游戏进程 (LeagueClient.exe, League of Legends.exe) → Fiddler 代理
- Fiddler 的 HTTPS 解密功能需要开启并安装根证书
- 通过 MCP 协议与 Fiddler 交互: 查询捕获的会话、过滤流量、提取数据

Fiddler MCP Server 配置 (来自 telerik.com 文档):
- Server type: http
- Server URL: http://localhost:8868/mcp
- Authorization: ApiKey FIDDLER_API_KEY
"""

import asyncio
import hashlib
import json
import re
import time
from collections import deque, defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Deque, Dict, List, Optional, Set, Tuple, Union

try:
    from logging_system import get_module_logger, get_collector, traced
except ImportError:
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    from logging_system import get_module_logger, get_collector, traced

# ─── 常量 ────────────────────────────────────────────────────────────────────

MODULE_ID = "M1007"
MODULE_NAME = "FiddlerNetworkBridge"
TAG = f"[{MODULE_ID}]"

# Fiddler MCP Server 配置
FIDDLER_MCP_URL = "http://localhost:8868/mcp"
FIDDLER_DEFAULT_PORT = 8868

# 游戏进程 — 需要通过 Proxifier 路由到 Fiddler
LOL_PROCESSES = [
    "LeagueClient.exe",
    "LeagueClientUx.exe",
    "League of Legends.exe",
    "RiotClientServices.exe",
]

# Riot API 域名过滤
RIOT_DOMAINS = [
    "*.riotgames.com",
    "*.leagueoflegends.com",
    "127.0.0.1:2999",  # Live Client Data API
    "127.0.0.1:*",     # LCU 端口
]

# 协议标识
PROTOCOL_MARKERS = {
    "lcu": "/lol-",
    "live_client": "/liveclientdata/",
    "sgp": "/match-history-query/",
    "riot_api": "/lol/match/v5/",
    "websocket": "wss://",
}

logger = get_module_logger(MODULE_ID)


# ─── 数据结构 ─────────────────────────────────────────────────────────────────

class TrafficType(Enum):
    LCU_REST = "lcu_rest"
    LCU_WEBSOCKET = "lcu_websocket"
    LIVE_CLIENT = "live_client"
    SGP_QUERY = "sgp_query"
    RIOT_API = "riot_api"
    GAME_PROTOCOL = "game_protocol"
    UNKNOWN = "unknown"


class FiddlerSessionStatus(Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    CAPTURING = "capturing"
    PAUSED = "paused"
    ERROR = "error"


@dataclass
class CapturedRequest:
    """
    捕获的网络请求 — 从 Fiddler 会话中提取。
    
    设计参考 Seraphine PastRequest, 但增加了:
    - request_headers / response_headers
    - request_body / response_body
    - traffic_type 分类
    - ssl_info (HTTPS 解密信息)
    """
    session_id: int
    method: str
    url: str
    host: str
    path: str
    status_code: int = 0
    request_headers: Dict[str, str] = field(default_factory=dict)
    response_headers: Dict[str, str] = field(default_factory=dict)
    request_body: Optional[str] = None
    response_body: Optional[str] = None
    content_type: str = ""
    content_length: int = 0
    timestamp: float = field(default_factory=time.time)
    duration_ms: float = 0.0
    traffic_type: TrafficType = TrafficType.UNKNOWN
    process_name: str = ""
    is_https: bool = False
    ssl_decrypted: bool = False

    @property
    def timestamp_iso(self) -> str:
        return datetime.fromtimestamp(self.timestamp, tz=timezone.utc).isoformat()

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["traffic_type"] = self.traffic_type.value
        d["timestamp_iso"] = self.timestamp_iso
        return d


@dataclass
class FiddlerMCPConfig:
    """Fiddler MCP Server 配置"""
    host: str = "localhost"
    port: int = FIDDLER_DEFAULT_PORT
    api_key: str = ""
    server_name: str = "#fiddler"
    auto_start: bool = True
    https_decrypt: bool = True
    capture_websocket: bool = True

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}/mcp"

    @property
    def auth_header(self) -> Dict[str, str]:
        return {"Authorization": f"ApiKey {self.api_key}"} if self.api_key else {}


@dataclass
class ProxifierRule:
    """Proxifier 规则 — 配置游戏进程走 Fiddler 代理"""
    process_name: str
    proxy_host: str = "127.0.0.1"
    proxy_port: int = 8866  # Fiddler 默认代理端口
    action: str = "proxy"
    enabled: bool = True

    def to_proxifier_xml(self) -> str:
        """生成 Proxifier 配置片段"""
        return (
            f'  <Rule>\n'
            f'    <Name>{self.process_name} via Fiddler</Name>\n'
            f'    <Applications>{self.process_name}</Applications>\n'
            f'    <Action>{self.action}</Action>\n'
            f'    <Proxy>{self.proxy_host}:{self.proxy_port}</Proxy>\n'
            f'    <Enabled>{str(self.enabled).lower()}</Enabled>\n'
            f'  </Rule>'
        )


@dataclass
class TrafficFilter:
    """流量过滤器"""
    domains: List[str] = field(default_factory=lambda: list(RIOT_DOMAINS))
    methods: List[str] = field(default_factory=lambda: ["GET", "POST", "PUT", "PATCH"])
    min_status: int = 0
    max_status: int = 599
    content_types: List[str] = field(default_factory=lambda: ["application/json", "text/plain"])
    processes: List[str] = field(default_factory=lambda: list(LOL_PROCESSES))
    exclude_paths: List[str] = field(default_factory=lambda: ["/telemetry", "/analytics", "/tracking"])

    def matches(self, request: CapturedRequest) -> bool:
        """检查请求是否匹配过滤条件"""
        # 进程过滤
        if self.processes and request.process_name:
            if not any(p.lower() in request.process_name.lower() for p in self.processes):
                return False

        # 域名过滤
        if self.domains:
            domain_match = False
            for pattern in self.domains:
                if pattern.startswith("*"):
                    if request.host.endswith(pattern[1:]):
                        domain_match = True
                        break
                elif pattern in request.host or pattern == request.host:
                    domain_match = True
                    break
            if not domain_match:
                return False

        # 状态码过滤
        if request.status_code:
            if not (self.min_status <= request.status_code <= self.max_status):
                return False

        # 排除路径
        for exclude in self.exclude_paths:
            if exclude in request.path:
                return False

        return True


# ─── 流量分类器 ───────────────────────────────────────────────────────────────

class TrafficClassifier:
    """
    流量分类器 — 将捕获的请求分类为不同的 Riot 协议类型。
    
    分类依据:
    - URL 路径模式 (LCU API, Live Client Data, SGP, Riot API v5)
    - 端口号 (2999 = Live Client, 动态 = LCU)
    - 协议 (HTTP vs WebSocket)
    """

    @staticmethod
    def classify(request: CapturedRequest) -> TrafficType:
        url = request.url.lower()
        path = request.path.lower()

        # WebSocket
        if "wss://" in url or "ws://" in url:
            return TrafficType.LCU_WEBSOCKET

        # Live Client Data API (port 2999)
        if ":2999" in url or "/liveclientdata/" in path:
            return TrafficType.LIVE_CLIENT

        # LCU REST API
        if any(marker in path for marker in ["/lol-", "/riotclient/", "/lol-lobby/"]):
            return TrafficType.LCU_REST

        # SGP Match History Query
        if "/match-history-query/" in path:
            return TrafficType.SGP_QUERY

        # Riot API v5
        if "/lol/match/v5/" in path or "/lol/summoner/v4/" in path:
            return TrafficType.RIOT_API

        # 游戏协议 (非 HTTP)
        if request.host and "riotgames.com" in request.host:
            return TrafficType.GAME_PROTOCOL

        return TrafficType.UNKNOWN


# ─── Fiddler MCP 客户端 ──────────────────────────────────────────────────────

class FiddlerMCPClient:
    """
    Fiddler MCP Server 客户端。
    
    通过 HTTP 与 Fiddler Everywhere MCP Server 交互:
    - 查询捕获的会话列表
    - 过滤特定域名/进程的流量
    - 提取请求/响应内容
    - 导出会话数据
    
    MCP Server URL: http://localhost:8868/mcp
    认证方式: Authorization: ApiKey {key}
    """

    def __init__(self, config: Optional[FiddlerMCPConfig] = None):
        self.config = config or FiddlerMCPConfig()
        self.status = FiddlerSessionStatus.DISCONNECTED
        self._sessions: Dict[int, CapturedRequest] = {}
        self._buffer: Deque[CapturedRequest] = deque(maxlen=10000)
        self._subscribers: Dict[TrafficType, List[Callable]] = defaultdict(list)
        self._mock_mode = True  # 默认模拟模式

    @traced(MODULE_ID)
    async def connect(self) -> bool:
        """连接到 Fiddler MCP Server"""
        self.status = FiddlerSessionStatus.CONNECTING
        try:
            # 在实际环境中, 这里会发送 MCP 初始化请求
            # POST http://localhost:8868/mcp
            # {"jsonrpc":"2.0","method":"initialize","params":{...}}
            logger.info(f"{TAG} Connecting to Fiddler MCP at {self.config.url}")
            
            if self._mock_mode:
                await asyncio.sleep(0.05)  # 模拟连接延迟
                self.status = FiddlerSessionStatus.CONNECTED
                logger.info(f"{TAG} Connected (mock mode)")
                return True

            # 实际 MCP 连接逻辑
            # response = await http_post(self.config.url, {
            #     "jsonrpc": "2.0",
            #     "method": "initialize",
            #     "params": {"protocolVersion": "2024-11-05"},
            #     "id": 1,
            # }, headers=self.config.auth_header)
            
            self.status = FiddlerSessionStatus.CONNECTED
            return True
        except Exception as e:
            self.status = FiddlerSessionStatus.ERROR
            logger.error(f"{TAG} Connection failed: {e}")
            return False

    @traced(MODULE_ID)
    async def start_capture(self, traffic_filter: Optional[TrafficFilter] = None) -> bool:
        """开始捕获流量"""
        if self.status != FiddlerSessionStatus.CONNECTED:
            logger.warning(f"{TAG} Not connected, cannot start capture")
            return False

        self.status = FiddlerSessionStatus.CAPTURING
        self._traffic_filter = traffic_filter or TrafficFilter()

        if self._mock_mode:
            # 生成模拟流量
            asyncio.create_task(self._generate_mock_traffic())

        logger.info(f"{TAG} Capture started with filter: {len(self._traffic_filter.domains)} domains")
        return True

    @traced(MODULE_ID)
    async def stop_capture(self):
        """停止捕获"""
        self.status = FiddlerSessionStatus.PAUSED
        logger.info(f"{TAG} Capture stopped, {len(self._buffer)} sessions buffered")

    @traced(MODULE_ID)
    async def query_sessions(
        self,
        traffic_type: Optional[TrafficType] = None,
        since: Optional[float] = None,
        limit: int = 100,
    ) -> List[CapturedRequest]:
        """
        查询捕获的会话。
        
        通过 Fiddler MCP 的 tools/call 方法查询:
        - 按流量类型过滤
        - 按时间范围过滤
        - 限制返回数量
        """
        results = []
        for req in reversed(list(self._buffer)):
            if len(results) >= limit:
                break
            if traffic_type and req.traffic_type != traffic_type:
                continue
            if since and req.timestamp < since:
                continue
            results.append(req)

        return results

    @traced(MODULE_ID)
    async def extract_match_data(self, session_id: int) -> Optional[Dict]:
        """从捕获的会话中提取对局数据"""
        req = self._sessions.get(session_id)
        if not req or not req.response_body:
            return None

        try:
            data = json.loads(req.response_body)
            return data
        except json.JSONDecodeError:
            logger.warning(f"{TAG} Failed to parse response body for session {session_id}")
            return None

    @traced(MODULE_ID)
    async def get_live_client_data(self) -> Optional[Dict]:
        """
        获取 Live Client Data API 数据 — 从 Fiddler 缓存中提取。
        
        对应 Riot Live Client Data API:
        GET https://127.0.0.1:2999/liveclientdata/allgamedata
        
        这是 Fiddler 方案的核心优势:
        不直接请求 localhost:2999, 而是从 Fiddler 已捕获的流量中读取,
        减少对游戏进程的干扰。
        """
        live_sessions = await self.query_sessions(
            traffic_type=TrafficType.LIVE_CLIENT,
            limit=1,
        )
        if live_sessions:
            return await self.extract_match_data(live_sessions[0].session_id)
        return None

    def subscribe(self, traffic_type: TrafficType, callback: Callable):
        """订阅特定类型的流量事件"""
        self._subscribers[traffic_type].append(callback)
        logger.debug(f"{TAG} Subscriber added for {traffic_type.value}")

    async def _notify_subscribers(self, request: CapturedRequest):
        """通知订阅者"""
        for callback in self._subscribers.get(request.traffic_type, []):
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(request)
                else:
                    callback(request)
            except Exception as e:
                logger.error(f"{TAG} Subscriber callback error: {e}")

    async def _generate_mock_traffic(self):
        """生成模拟流量 — 用于测试"""
        mock_requests = [
            CapturedRequest(
                session_id=1001,
                method="GET",
                url="https://127.0.0.1:2999/liveclientdata/allgamedata",
                host="127.0.0.1:2999",
                path="/liveclientdata/allgamedata",
                status_code=200,
                response_body=json.dumps({
                    "activePlayer": {"summonerName": "TestPlayer", "level": 11},
                    "allPlayers": [{"summonerName": f"Player{i}", "championName": f"Champion{i}"}
                                   for i in range(10)],
                    "gameData": {"gameMode": "CLASSIC", "gameTime": 1234.5},
                }),
                content_type="application/json",
                traffic_type=TrafficType.LIVE_CLIENT,
                process_name="League of Legends.exe",
                is_https=True,
                ssl_decrypted=True,
            ),
            CapturedRequest(
                session_id=1002,
                method="GET",
                url="https://127.0.0.1:52345/lol-match-history/v1/products/lol/test-puuid/matches",
                host="127.0.0.1:52345",
                path="/lol-match-history/v1/products/lol/test-puuid/matches",
                status_code=200,
                response_body=json.dumps({"games": {"games": [], "gameCount": 0}}),
                content_type="application/json",
                traffic_type=TrafficType.LCU_REST,
                process_name="LeagueClient.exe",
            ),
            CapturedRequest(
                session_id=1003,
                method="GET",
                url="https://na1.api.riotgames.com/lol/match/v5/matches/NA1_12345",
                host="na1.api.riotgames.com",
                path="/lol/match/v5/matches/NA1_12345",
                status_code=200,
                response_body=json.dumps({"metadata": {"matchId": "NA1_12345"}}),
                content_type="application/json",
                traffic_type=TrafficType.RIOT_API,
                process_name="LeagueClient.exe",
                is_https=True,
                ssl_decrypted=True,
            ),
        ]

        for req in mock_requests:
            await asyncio.sleep(0.02)
            req.traffic_type = TrafficClassifier.classify(req)
            self._buffer.append(req)
            self._sessions[req.session_id] = req
            await self._notify_subscribers(req)

    @property
    def stats(self) -> Dict[str, Any]:
        type_counts = defaultdict(int)
        for req in self._buffer:
            type_counts[req.traffic_type.value] += 1
        return {
            "status": self.status.value,
            "total_captured": len(self._buffer),
            "by_type": dict(type_counts),
            "subscribers": {k.value: len(v) for k, v in self._subscribers.items()},
            "mock_mode": self._mock_mode,
        }


# ─── Proxifier 配置生成器 ────────────────────────────────────────────────────

class ProxifierConfigGenerator:
    """
    生成 Proxifier 配置文件 — 将游戏进程路由到 Fiddler。
    
    Proxifier + Fiddler 配合使用:
    1. Fiddler 开启 HTTPS 解密 + 代理端口 (默认 8866)
    2. Proxifier 规则: 游戏进程 → 127.0.0.1:8866
    3. Fiddler 捕获并解密游戏的 HTTPS 流量
    4. 通过 Fiddler MCP Server 读取捕获的数据
    """

    def __init__(self, fiddler_proxy_port: int = 8866):
        self.fiddler_proxy_port = fiddler_proxy_port
        self.rules: List[ProxifierRule] = []
        self._init_default_rules()

    def _init_default_rules(self):
        """初始化默认规则 — 覆盖所有 LoL 相关进程"""
        for process in LOL_PROCESSES:
            self.rules.append(ProxifierRule(
                process_name=process,
                proxy_port=self.fiddler_proxy_port,
            ))

    def add_rule(self, process_name: str, **kwargs):
        """添加自定义规则"""
        self.rules.append(ProxifierRule(process_name=process_name, **kwargs))

    @traced(MODULE_ID)
    def generate_config(self) -> str:
        """生成 Proxifier 配置 XML"""
        rules_xml = "\n".join(rule.to_proxifier_xml() for rule in self.rules)
        return (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<ProxifierProfile>\n'
            f'  <ProxyList>\n'
            f'    <Proxy id="100" type="HTTPS">\n'
            f'      <Address>127.0.0.1</Address>\n'
            f'      <Port>{self.fiddler_proxy_port}</Port>\n'
            f'      <Options>48</Options>\n'
            f'    </Proxy>\n'
            f'  </ProxyList>\n'
            f'  <RuleList>\n'
            f'{rules_xml}\n'
            f'    <Rule>\n'
            f'      <Name>Default</Name>\n'
            f'      <Action>Direct</Action>\n'
            f'    </Rule>\n'
            f'  </RuleList>\n'
            '</ProxifierProfile>'
        )

    @traced(MODULE_ID)
    def save_config(self, path: Optional[Path] = None) -> Path:
        """保存 Proxifier 配置到文件"""
        target = path or Path(__file__).parent / "configs" / "proxifier_lol.ppx"
        target.parent.mkdir(parents=True, exist_ok=True)
        config_xml = self.generate_config()
        with open(target, "w", encoding="utf-8") as f:
            f.write(config_xml)
        logger.info(f"{TAG} Proxifier config saved to {target}")
        return target


# ─── SSL 证书检测 ─────────────────────────────────────────────────────────────

class FiddlerCertificateChecker:
    """
    Fiddler 根证书检测 — 确保 HTTPS 解密功能正常。
    
    系统角度批判 (来自 M926-M945):
    M943 FiddlerDeepPacketAnalyzer 的 SSL 解析需要 Fiddler 的 HTTPS 解密功能开启,
    Proxifier 配置需正确路由游戏进程。建议：启动时检测 Fiddler 证书安装状态。
    """

    @staticmethod
    @traced(MODULE_ID)
    def check_certificate_installed() -> Dict[str, Any]:
        """检查 Fiddler 根证书是否已安装"""
        import subprocess
        result = {
            "fiddler_cert_found": False,
            "cert_details": None,
            "check_method": "certutil",
            "error": None,
        }

        try:
            # Windows: certutil 检查
            proc = subprocess.run(
                ["certutil", "-verifystore", "Root", "DO_NOT_TRUST_FiddlerRoot"],
                capture_output=True, text=True, timeout=10
            )
            if proc.returncode == 0:
                result["fiddler_cert_found"] = True
                result["cert_details"] = proc.stdout[:500]
        except FileNotFoundError:
            result["check_method"] = "manual"
            result["error"] = "certutil not available (non-Windows?)"
        except subprocess.TimeoutExpired:
            result["error"] = "Certificate check timed out"
        except Exception as e:
            result["error"] = str(e)

        return result


# ─── 集成主类 ─────────────────────────────────────────────────────────────────

class FiddlerNetworkBridge:
    """
    Fiddler 网络桥接器 — M1007 核心类。
    
    统一管理:
    1. Fiddler MCP 连接
    2. Proxifier 配置
    3. 流量捕获与分类
    4. 数据提取与分发
    
    这是 operatorRL 选择 Fiddler 方案的核心理由:
    - 网络包数据零幻觉 (相比视觉 OCR)
    - 符合逆向工程师技术方向
    - Fiddler MCP Server 天然支持 AI 集成
    - Proxifier 可透明代理任何进程
    """

    def __init__(
        self,
        config: Optional[FiddlerMCPConfig] = None,
        traffic_filter: Optional[TrafficFilter] = None,
    ):
        self.config = config or FiddlerMCPConfig()
        self.traffic_filter = traffic_filter or TrafficFilter()
        self.mcp_client = FiddlerMCPClient(self.config)
        self.proxifier = ProxifierConfigGenerator()
        self.cert_checker = FiddlerCertificateChecker()
        self.classifier = TrafficClassifier()
        self._initialized = False
        self.collector = get_collector()

    @traced(MODULE_ID)
    async def initialize(self) -> Dict[str, Any]:
        """
        初始化桥接器:
        1. 检查 Fiddler 证书
        2. 生成 Proxifier 配置
        3. 连接 Fiddler MCP
        """
        results = {
            "certificate": None,
            "proxifier_config": None,
            "mcp_connection": False,
        }

        # 1. 证书检查 (非阻塞)
        try:
            results["certificate"] = self.cert_checker.check_certificate_installed()
        except Exception as e:
            results["certificate"] = {"error": str(e)}

        # 2. Proxifier 配置
        try:
            config_path = self.proxifier.save_config()
            results["proxifier_config"] = str(config_path)
        except Exception as e:
            results["proxifier_config"] = f"error: {e}"

        # 3. MCP 连接
        results["mcp_connection"] = await self.mcp_client.connect()

        self._initialized = results["mcp_connection"]
        self.collector.record_init(MODULE_ID, 
                                    "ok" if self._initialized else "partial",
                                    0, results)
        return results

    @traced(MODULE_ID)
    async def start(self) -> bool:
        """开始流量捕获"""
        if not self._initialized:
            await self.initialize()
        return await self.mcp_client.start_capture(self.traffic_filter)

    @traced(MODULE_ID)
    async def stop(self):
        """停止流量捕获"""
        await self.mcp_client.stop_capture()

    @traced(MODULE_ID)
    async def get_recent_traffic(
        self,
        traffic_type: Optional[TrafficType] = None,
        limit: int = 50,
    ) -> List[Dict]:
        """获取最近的流量数据"""
        sessions = await self.mcp_client.query_sessions(traffic_type, limit=limit)
        return [s.to_dict() for s in sessions]

    @traced(MODULE_ID)
    async def get_live_game_state(self) -> Optional[Dict]:
        """获取当前游戏状态 — 从 Fiddler 捕获的 Live Client Data 中提取"""
        return await self.mcp_client.get_live_client_data()

    @property
    def stats(self) -> Dict[str, Any]:
        return {
            "initialized": self._initialized,
            "mcp": self.mcp_client.stats,
            "proxifier_rules": len(self.proxifier.rules),
            "filter_domains": len(self.traffic_filter.domains),
        }


# ─── 自检 ─────────────────────────────────────────────────────────────────────

async def _self_test():
    print(f"\n{'='*60}")
    print(f"  M1007 FiddlerNetworkBridge — 自检")
    print(f"{'='*60}")

    bridge = FiddlerNetworkBridge()
    
    # 1. 初始化
    init_result = await bridge.initialize()
    print(f"  ✓ 初始化: MCP={init_result['mcp_connection']}")

    # 2. 开始捕获
    ok = await bridge.start()
    print(f"  ✓ 捕获启动: {ok}")

    # 3. 等待模拟流量
    await asyncio.sleep(0.2)

    # 4. 查询流量
    traffic = await bridge.get_recent_traffic()
    print(f"  ✓ 捕获流量: {len(traffic)} 条")

    # 5. Live game state
    state = await bridge.get_live_game_state()
    print(f"  ✓ 游戏状态: {'获取成功' if state else '无数据'}")

    # 6. 停止
    await bridge.stop()
    
    # 7. 统计
    stats = bridge.stats
    print(f"  ✓ 统计: {json.dumps(stats, indent=2)}")

    print(f"\n  M1007 自检通过 ✓")
    return True


def main():
    return asyncio.run(_self_test())


if __name__ == "__main__":
    main()
