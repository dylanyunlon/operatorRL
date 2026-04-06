"""
canbus/vehicle/data_source_factory.py — 数据源工厂
====================================================

查看 Apollo modules/canbus/vehicle/vehicle_factory.h 上现有车辆工厂模式的
实现方式, 理解其模式, 特别是不同车型适配器是如何通过工厂统一创建的。从
Apollo VehicleFactory::CreateVehicle(vehicle_parameter) 这个好例子开始。
然后遵循该模式实现一个 DataSourceFactory, 让 canbus 可以根据配置创建
LCU/Fiddler/Replay/Mock 不同数据源适配器, 并能在运行时切换数据源。

位置: lolbot-HyperAI/modules/canbus/vehicle/data_source_factory.py
"""

from __future__ import annotations

import abc
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple, Type

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data source result
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PollResult:
    """单次数据拉取结果.

    Apollo 中 vehicle_controller 返回 Chassis protobuf.
    我们返回 PollResult 包含原始 JSON 数据.
    """
    success: bool
    data: Optional[Dict[str, Any]] = None
    latency_ms: float = 0.0
    error: str = ""
    source_type: str = ""
    timestamp: float = field(default_factory=time.time)


# ---------------------------------------------------------------------------
# Abstract data source (= Apollo VehicleController base)
# ---------------------------------------------------------------------------

class DataSource(abc.ABC):
    """数据源抽象基类.

    Apollo VehicleController 的等价物: 每种 "车型" (数据来源)
    实现自己的 Init/Start/Stop/GetChassis.

    所有数据源必须实现:
    - init(): 初始化资源
    - poll(): 拉取一帧数据
    - shutdown(): 清理资源
    - source_type: 标识字符串
    """

    @abc.abstractmethod
    def init(self) -> bool:
        """初始化数据源. Returns True on success."""
        ...

    @abc.abstractmethod
    def poll(self) -> PollResult:
        """拉取一帧数据. 每个 Proc() tick 调用一次."""
        ...

    @abc.abstractmethod
    def shutdown(self) -> None:
        """关闭并清理资源."""
        ...

    @property
    @abc.abstractmethod
    def source_type(self) -> str:
        """数据源类型标识."""
        ...

    @property
    def is_available(self) -> bool:
        """是否可用 (子类可覆盖)."""
        return True

    def stats(self) -> Dict[str, Any]:
        """状态信息 (子类可覆盖)."""
        return {"source_type": self.source_type}


# ---------------------------------------------------------------------------
# LCU data source
# ---------------------------------------------------------------------------

class LCUDataSource(DataSource):
    """LCU Live Client Data API 数据源.

    Apollo 等价: LincolnVehicleController — 特定车型适配.
    """

    def __init__(
        self,
        base_url: str = "https://127.0.0.1:2999",
        timeout_s: float = 2.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout_s = timeout_s
        self._initialized = False
        self._poll_count: int = 0
        self._error_count: int = 0

    @property
    def source_type(self) -> str:
        return "lcu"

    def init(self) -> bool:
        self._initialized = True
        logger.info("LCUDataSource initialized: %s", self._base_url)
        return True

    def poll(self) -> PollResult:
        """GET /liveclientdata/allgamedata."""
        import ssl
        import urllib.error
        import urllib.request

        self._poll_count += 1
        url = f"{self._base_url}/liveclientdata/allgamedata"
        t0 = time.monotonic()

        try:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE

            req = urllib.request.Request(url, method="GET")
            req.add_header("Accept", "application/json")

            with urllib.request.urlopen(
                req, timeout=self._timeout_s, context=ctx
            ) as resp:
                latency = (time.monotonic() - t0) * 1000
                body = resp.read().decode("utf-8")
                data = json.loads(body)
                return PollResult(
                    success=True, data=data,
                    latency_ms=latency, source_type="lcu",
                )

        except Exception as exc:
            latency = (time.monotonic() - t0) * 1000
            self._error_count += 1
            return PollResult(
                success=False, latency_ms=latency,
                error=f"{type(exc).__name__}: {exc}",
                source_type="lcu",
            )

    def shutdown(self) -> None:
        self._initialized = False

    def stats(self) -> Dict[str, Any]:
        return {
            "source_type": "lcu",
            "base_url": self._base_url,
            "poll_count": self._poll_count,
            "error_count": self._error_count,
        }


# ---------------------------------------------------------------------------
# Replay data source
# ---------------------------------------------------------------------------

class ReplayDataSource(DataSource):
    """录制文件回放数据源.

    从 JSONL 录制文件中按时序读取帧, 模拟实时数据流.
    """

    def __init__(
        self,
        filepath: str = "",
        speed: float = 1.0,
        loop: bool = False,
    ) -> None:
        self._filepath = filepath
        self._speed = max(0.1, speed)
        self._loop = loop
        self._frames: List[Dict[str, Any]] = []
        self._frame_index: int = 0
        self._last_frame_time: float = 0.0
        self._initialized = False

    @property
    def source_type(self) -> str:
        return "replay"

    def init(self) -> bool:
        p = Path(self._filepath)
        if not p.exists():
            logger.error("回放文件不存在: %s", p)
            return False

        self._frames = []
        try:
            raw = p.read_text(encoding="utf-8").strip()
            if not raw:
                logger.error("回放文件为空: %s", p)
                return False

            # Claude16: support both single-JSON and JSONL formats.
            # testdata/sample_allgamedata.json is single-JSON (one {} object).
            # logs/recordings/*.jsonl is JSONL (one JSON per line).
            if raw.startswith("{"):
                try:
                    single = json.loads(raw)
                    self._frames.append(single)
                except json.JSONDecodeError:
                    pass  # Not valid single JSON, try JSONL below

            if not self._frames:
                for line in raw.splitlines():
                    line = line.strip()
                    if line:
                        frame = json.loads(line)
                        self._frames.append(frame)

        except (json.JSONDecodeError, IOError) as exc:
            logger.error("回放文件解析失败: %s", exc)
            return False

        if not self._frames:
            logger.error("回放文件为空: %s", p)
            return False

        self._frame_index = 0
        self._initialized = True
        logger.info(
            "ReplayDataSource: %d frames from %s (speed=%.1fx)",
            len(self._frames), p, self._speed,
        )
        return True

    def poll(self) -> PollResult:
        if not self._frames:
            return PollResult(
                success=False, error="No frames loaded",
                source_type="replay",
            )

        if self._frame_index >= len(self._frames):
            if self._loop:
                self._frame_index = 0
            else:
                return PollResult(
                    success=False, error="Replay exhausted",
                    source_type="replay",
                )

        frame = self._frames[self._frame_index]
        self._frame_index += 1

        # 提取 payload (支持两种格式: 直接 dict 或 {payload: dict})
        data = frame.get("payload", frame)

        return PollResult(
            success=True, data=data,
            latency_ms=0.1,  # 回放几乎无延迟
            source_type="replay",
        )

    def shutdown(self) -> None:
        self._frames = []
        self._initialized = False

    @property
    def total_frames(self) -> int:
        return len(self._frames)

    @property
    def current_frame(self) -> int:
        return self._frame_index

    @property
    def progress(self) -> float:
        if not self._frames:
            return 0.0
        return self._frame_index / len(self._frames)

    def stats(self) -> Dict[str, Any]:
        return {
            "source_type": "replay",
            "filepath": self._filepath,
            "total_frames": len(self._frames),
            "current_frame": self._frame_index,
            "progress": round(self.progress, 4),
            "speed": self._speed,
            "loop": self._loop,
        }


# ---------------------------------------------------------------------------
# Mock data source
# ---------------------------------------------------------------------------

class MockDataSource(DataSource):
    """Mock 数据源: 生成假数据用于测试.

    模拟一场 30 分钟的游戏, 游戏时间线性推进.
    """

    def __init__(self) -> None:
        self._tick: int = 0
        self._game_time: float = 0.0
        self._initialized = False

    @property
    def source_type(self) -> str:
        return "mock"

    def init(self) -> bool:
        self._tick = 0
        self._game_time = 0.0
        self._initialized = True
        logger.info("MockDataSource initialized")
        return True

    def poll(self) -> PollResult:
        self._tick += 1
        self._game_time += 0.1  # 每 tick += 0.1s

        data = {
            "gameData": {
                "gameMode": "CLASSIC",
                "gameTime": self._game_time,
                "mapName": "Map11",
                "mapNumber": 11,
                "mapTerrain": "Default",
            },
            "activePlayer": {
                "championName": "MockChampion",
                "level": min(18, 1 + int(self._game_time / 100)),
                "currentGold": 500 + self._tick * 5,
                "summonerName": "TestPlayer",
                "abilities": {},
            },
            "allPlayers": self._generate_mock_players(),
            "events": {"Events": []},
        }

        return PollResult(
            success=True, data=data,
            latency_ms=0.05, source_type="mock",
        )

    def _generate_mock_players(self) -> List[Dict[str, Any]]:
        """生成 10 个假玩家."""
        players = []
        champions = [
            "Ahri", "Jinx", "Thresh", "LeeSin", "Lux",
            "Zed", "Ashe", "Blitzcrank", "Garen", "Darius",
        ]
        for i in range(10):
            team = "ORDER" if i < 5 else "CHAOS"
            players.append({
                "championName": champions[i],
                "isBot": False,
                "isDead": False,
                "level": min(18, 1 + int(self._game_time / 100)),
                "position": "",
                "summonerName": f"Player{i+1}",
                "team": team,
                "scores": {
                    "kills": 0, "deaths": 0, "assists": 0,
                    "creepScore": int(self._game_time / 3),
                    "wardScore": 0.0,
                },
                "items": [],
            })
        return players

    def shutdown(self) -> None:
        self._initialized = False

    def stats(self) -> Dict[str, Any]:
        return {
            "source_type": "mock",
            "tick": self._tick,
            "game_time": round(self._game_time, 1),
        }


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

# 数据源注册表
_REGISTRY: Dict[str, Type[DataSource]] = {}


def register_data_source(name: str, cls: Type[DataSource]) -> None:
    """注册数据源类型.

    Usage::

        register_data_source("lcu", LCUDataSource)
        register_data_source("my_custom", MyCustomSource)
    """
    _REGISTRY[name] = cls
    logger.debug("注册数据源: %s → %s", name, cls.__name__)


# 内置注册
register_data_source("lcu", LCUDataSource)
register_data_source("replay", ReplayDataSource)
register_data_source("mock", MockDataSource)


class DataSourceFactory:
    """数据源工厂.

    Apollo VehicleFactory 的等价物: 根据配置字符串创建对应的数据源.

    Usage::

        factory = DataSourceFactory()
        source = factory.create("lcu", base_url="https://127.0.0.1:2999")
        source.init()
        result = source.poll()
    """

    @staticmethod
    def create(
        source_type: str,
        **kwargs: Any,
    ) -> DataSource:
        """创建数据源实例.

        Args:
            source_type: 注册名 ("lcu", "replay", "mock").
            **kwargs: 传给数据源构造函数的参数.

        Returns:
            DataSource 实例.

        Raises:
            ValueError: 未知的 source_type.
        """
        cls = _REGISTRY.get(source_type)
        if cls is None:
            available = ", ".join(sorted(_REGISTRY.keys()))
            raise ValueError(
                f"未知数据源类型: {source_type!r}. "
                f"可用类型: {available}"
            )
        try:
            return cls(**kwargs)
        except TypeError as exc:
            raise ValueError(
                f"创建 {source_type} 数据源失败: {exc}"
            ) from exc

    @staticmethod
    def available_types() -> List[str]:
        """返回所有已注册的数据源类型."""
        return sorted(_REGISTRY.keys())

    @staticmethod
    def create_from_config(
        data_source: str,
        lcu_base_url: str = "https://127.0.0.1:2999",
        lcu_timeout_s: float = 2.0,
        replay_file: str = "",
        replay_speed: float = 1.0,
        replay_loop: bool = False,
    ) -> DataSource:
        """从配置参数创建数据源 (便捷方法).

        根据 CanbusConf 的字段直接创建.
        """
        if data_source == "lcu":
            return DataSourceFactory.create(
                "lcu", base_url=lcu_base_url, timeout_s=lcu_timeout_s,
            )
        elif data_source == "replay":
            return DataSourceFactory.create(
                "replay", filepath=replay_file,
                speed=replay_speed, loop=replay_loop,
            )
        elif data_source == "mock":
            return DataSourceFactory.create("mock")
        else:
            return DataSourceFactory.create(data_source)

    @staticmethod
    def auto_detect() -> Tuple[str, "DataSource"]:
        """Auto-detect best available data source (Apollo vehicle detection pattern).

        Priority: LCU (live game) → testdata (simulated, time-advancing)
                  → mock (synthetic).

        Claude18: Changed testdata fallback from raw ReplayDataSource to
        SimulatedReplayDataSource. The raw replay loops the same JSON with
        fixed gameTime=1680.5, causing stale-data WARNING floods (~10/sec).
        SimulatedReplayDataSource advances gameTime on each poll(), giving
        the full pipeline realistic progression without stale triggers.
        """
        import ssl, urllib.request, urllib.error
        from pathlib import Path as _P

        # Priority 1: Live LCU game client
        try:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            req = urllib.request.Request(
                "https://127.0.0.1:2999/liveclientdata/gamestats"
            )
            urllib.request.urlopen(req, timeout=1.0, context=ctx)
            return "lcu", DataSourceFactory.create("lcu")
        except Exception:
            pass

        # Priority 2: Testdata with simulated time advancement
        td = _P(__file__).parent.parent / "testdata" / "sample_allgamedata.json"
        if td.exists():
            # Claude18: Use SimulatedReplayDataSource to avoid stale-data spam
            try:
                # Import triggers registration via module-level register_data_source
                import modules.canbus.vehicle.simulated_replay  # noqa: F401
                return "testdata", DataSourceFactory.create(
                    "simulated",
                    filepath=str(td),
                    tick_delta_s=1.0,
                    start_time_s=120.0,
                    inject_events=True,
                    max_game_time_s=2400.0,
                )
            except (ValueError, ImportError):
                # Fallback to raw replay if simulated not available
                return "testdata", DataSourceFactory.create(
                    "replay", filepath=str(td), speed=1.0, loop=True,
                )

        # Priority 3: Mock data
        return "mock", DataSourceFactory.create("mock")

    @staticmethod
    def probe_all() -> Dict[str, bool]:
        """Probe all data source types for availability."""
        results = {}
        for st in DataSourceFactory.available_types():
            try:
                ds = DataSourceFactory.create(st)
                results[st] = hasattr(ds, "probe") and ds.probe() or True
            except Exception:
                results[st] = False
        return results
