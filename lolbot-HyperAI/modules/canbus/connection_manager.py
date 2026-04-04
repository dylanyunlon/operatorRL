"""
canbus/connection_manager.py — LCU/Fiddler 连接状态机
======================================================

查看 Seraphine connector.py 上现有 ConnectionState + 自动重连的实现方式,
理解其模式, 特别是状态转换和 backoff 是如何与业务逻辑分离的。从
Seraphine 的 ConnectorState 状态机这个好例子开始。然后遵循该模式实现
一个新的 ConnectionManager, 让 canbus_component.Proc() 可以直接调用
manager.ensure_connected() 而不关心重连细节, 并能通过事件回调通知上层
状态变化。

位置: lolbot-HyperAI/modules/canbus/connection_manager.py
"""

from __future__ import annotations

import json
import logging
import ssl
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Connection states
# ---------------------------------------------------------------------------

class ConnectionState(Enum):
    """LCU 连接状态机.

    状态转换:
        DISCONNECTED → PROBING → CONNECTED → GAME_ACTIVE
                                     ↓              ↓
                                  BACKOFF ←────── STALE
                                     ↓
                                DISCONNECTED
    """
    DISCONNECTED = auto()
    PROBING = auto()
    CONNECTED = auto()
    GAME_ACTIVE = auto()
    STALE = auto()
    BACKOFF = auto()
    ERROR = auto()


# ---------------------------------------------------------------------------
# SSL context singleton
# ---------------------------------------------------------------------------

_SSL_CTX: Optional[ssl.SSLContext] = None


def _get_ssl_context() -> ssl.SSLContext:
    global _SSL_CTX
    if _SSL_CTX is None:
        _SSL_CTX = ssl.create_default_context()
        _SSL_CTX.check_hostname = False
        _SSL_CTX.verify_mode = ssl.CERT_NONE
    return _SSL_CTX


# ---------------------------------------------------------------------------
# Probe result
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ProbeResult:
    """单次 HTTP 探测结果."""
    success: bool
    status_code: int = 0
    latency_ms: float = 0.0
    data: Optional[Dict[str, Any]] = None
    error: str = ""


# ---------------------------------------------------------------------------
# Backoff calculator
# ---------------------------------------------------------------------------

class ExponentialBackoff:
    """指数退避计算器, 带 jitter.

    Apollo canbus 在 CAN 卡断开时使用固定重试间隔, 但网络环境下
    指数退避更合理 (参考 Seraphine retry 装饰器)。
    """

    def __init__(
        self,
        initial_s: float = 1.0,
        maximum_s: float = 30.0,
        multiplier: float = 2.0,
    ) -> None:
        self._initial = initial_s
        self._maximum = maximum_s
        self._multiplier = multiplier
        self._current = initial_s
        self._attempt: int = 0

    def next_delay(self) -> float:
        """返回下一次等待时长 (秒)."""
        delay = self._current
        self._current = min(self._current * self._multiplier, self._maximum)
        self._attempt += 1
        return delay

    def reset(self) -> None:
        """重连成功后重置."""
        self._current = self._initial
        self._attempt = 0

    @property
    def attempt(self) -> int:
        return self._attempt

    @property
    def current_delay(self) -> float:
        return self._current


# ---------------------------------------------------------------------------
# Game time tracker — stale data detection
# ---------------------------------------------------------------------------

class GameTimeTracker:
    """追踪游戏时间是否前进, 检测 stale 数据.

    连续 N 个 tick 游戏时间不变 → stale_warning.
    连续 M 个 tick 不变 → game_ended.
    """

    def __init__(
        self,
        stale_threshold: int = 50,
        game_end_threshold: int = 100,
    ) -> None:
        self._stale_threshold = stale_threshold
        self._game_end_threshold = game_end_threshold
        self._last_game_time: float = 0.0
        self._stale_count: int = 0

    def update(self, game_time: float) -> str:
        """更新游戏时间, 返回状态.

        Returns:
            "advancing" | "stale" | "game_ended"
        """
        if game_time > self._last_game_time:
            self._stale_count = 0
            self._last_game_time = game_time
            return "advancing"

        if self._last_game_time > 0:
            self._stale_count += 1

        if self._stale_count >= self._game_end_threshold:
            return "game_ended"
        if self._stale_count >= self._stale_threshold:
            return "stale"

        self._last_game_time = game_time
        return "advancing"

    def reset(self) -> None:
        self._last_game_time = 0.0
        self._stale_count = 0

    @property
    def last_game_time(self) -> float:
        return self._last_game_time

    @property
    def stale_count(self) -> int:
        return self._stale_count


# ---------------------------------------------------------------------------
# HTTP probe functions
# ---------------------------------------------------------------------------

def _http_get_json(
    url: str,
    timeout_s: float = 2.0,
    headers: Optional[Dict[str, str]] = None,
) -> ProbeResult:
    """执行 HTTP GET 并解析 JSON.

    不抛出异常, 始终返回 ProbeResult.
    """
    t0 = time.monotonic()
    try:
        req = urllib.request.Request(url, method="GET")
        req.add_header("Accept", "application/json")
        if headers:
            for k, v in headers.items():
                req.add_header(k, v)

        with urllib.request.urlopen(
            req, timeout=timeout_s, context=_get_ssl_context()
        ) as resp:
            latency = (time.monotonic() - t0) * 1000
            body = resp.read().decode("utf-8")
            data = json.loads(body)
            return ProbeResult(
                success=True,
                status_code=resp.status,
                latency_ms=latency,
                data=data,
            )

    except urllib.error.URLError as exc:
        latency = (time.monotonic() - t0) * 1000
        reason = str(getattr(exc, "reason", exc))
        return ProbeResult(
            success=False, latency_ms=latency,
            error=f"URLError: {reason}",
        )

    except TimeoutError:
        latency = (time.monotonic() - t0) * 1000
        return ProbeResult(
            success=False, latency_ms=latency,
            error=f"Timeout after {timeout_s}s",
        )

    except json.JSONDecodeError as exc:
        latency = (time.monotonic() - t0) * 1000
        return ProbeResult(
            success=False, latency_ms=latency,
            error=f"JSON decode: {exc}",
        )

    except Exception as exc:
        latency = (time.monotonic() - t0) * 1000
        return ProbeResult(
            success=False, latency_ms=latency,
            error=f"{type(exc).__name__}: {exc}",
        )


# ---------------------------------------------------------------------------
# ConnectionManager
# ---------------------------------------------------------------------------

# Callback type aliases
StateChangeCallback = Callable[[ConnectionState, ConnectionState], None]
GameDetectedCallback = Callable[[Dict[str, Any]], None]


class ConnectionManager:
    """LCU 连接生命周期管理器.

    职责:
        1. 探测 LCU Live Client API 是否可达
        2. 检测游戏是否在进行中
        3. 管理 backoff 和自动重连
        4. 追踪 stale 数据
        5. 通过回调通知状态变化

    与 canbus_component.Proc() 的协作:
        每个 tick 调用 ensure_connected(), 返回 (state, game_data).
        Proc() 只需根据返回值决定是否发布数据.

    Usage::

        manager = ConnectionManager(base_url="https://127.0.0.1:2999")
        # 在 canbus Init() 中:
        manager.start()
        # 在 canbus Proc() 中:
        state, data = manager.poll()
        if data is not None:
            transport.publish(raw_lcu_msg(data))
    """

    def __init__(
        self,
        base_url: str = "https://127.0.0.1:2999",
        timeout_s: float = 2.0,
        backoff_initial: float = 1.0,
        backoff_max: float = 30.0,
        backoff_multiplier: float = 2.0,
        stale_threshold: int = 50,
        game_end_threshold: int = 100,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout_s = timeout_s
        self._state = ConnectionState.DISCONNECTED
        self._backoff = ExponentialBackoff(
            backoff_initial, backoff_max, backoff_multiplier
        )
        self._game_tracker = GameTimeTracker(stale_threshold, game_end_threshold)

        # Timing
        self._last_probe_time: float = 0.0
        self._next_probe_time: float = 0.0

        # Stats
        self._total_probes: int = 0
        self._successful_probes: int = 0
        self._failed_probes: int = 0
        self._total_latency_ms: float = 0.0
        self._last_latency_ms: float = 0.0
        self._game_detected_count: int = 0

        # Callbacks
        self._state_callbacks: List[StateChangeCallback] = []
        self._game_callbacks: List[GameDetectedCallback] = []

    # ─── Public API ──────────────────────────────────────────────────

    def on_state_change(self, cb: StateChangeCallback) -> None:
        """注册状态变化回调."""
        self._state_callbacks.append(cb)

    def on_game_detected(self, cb: GameDetectedCallback) -> None:
        """注册游戏检测回调 (首次检测到游戏时触发)."""
        self._game_callbacks.append(cb)

    def start(self) -> None:
        """初始化, 开始探测周期."""
        self._transition(ConnectionState.PROBING)
        self._next_probe_time = 0.0  # 立即探测

    def poll(self) -> Tuple[ConnectionState, Optional[Dict[str, Any]]]:
        """每个 Proc() tick 调用一次.

        根据当前状态决定行为:
        - DISCONNECTED/PROBING: 探测 gamestats
        - CONNECTED: 探测 gamestats (等待游戏开始)
        - GAME_ACTIVE: 获取 allgamedata
        - BACKOFF: 等待退避时间结束
        - STALE: 继续尝试, 但标记 stale

        Returns:
            (当前状态, allgamedata 或 None)
        """
        now = time.monotonic()

        # BACKOFF 状态: 等待
        if self._state == ConnectionState.BACKOFF:
            if now < self._next_probe_time:
                return self._state, None
            self._transition(ConnectionState.PROBING)

        # DISCONNECTED → 开始探测
        if self._state == ConnectionState.DISCONNECTED:
            self._transition(ConnectionState.PROBING)

        # PROBING / CONNECTED: 检查游戏是否活跃
        if self._state in (ConnectionState.PROBING, ConnectionState.CONNECTED):
            probe = self._probe_gamestats()
            if not probe.success:
                self._on_probe_failed(probe)
                return self._state, None

            self._backoff.reset()
            game_time = 0.0
            if probe.data:
                game_time = probe.data.get("gameTime", 0.0)

            if game_time > 0:
                if self._state != ConnectionState.GAME_ACTIVE:
                    self._transition(ConnectionState.GAME_ACTIVE)
                    self._game_detected_count += 1
                    for cb in self._game_callbacks:
                        try:
                            cb(probe.data or {})
                        except Exception:
                            logger.exception("game_detected callback error")
                # 游戏活跃 → 获取完整数据
                return self._fetch_allgamedata()
            else:
                if self._state != ConnectionState.CONNECTED:
                    self._transition(ConnectionState.CONNECTED)
                return self._state, None

        # GAME_ACTIVE: 获取完整数据
        if self._state in (ConnectionState.GAME_ACTIVE, ConnectionState.STALE):
            return self._fetch_allgamedata()

        return self._state, None

    def reset(self) -> None:
        """重置所有状态 (游戏结束后调用)."""
        self._game_tracker.reset()
        self._backoff.reset()
        self._transition(ConnectionState.PROBING)

    # ─── Internal ────────────────────────────────────────────────────

    def _probe_gamestats(self) -> ProbeResult:
        """轻量探测: GET /liveclientdata/gamestats."""
        url = f"{self._base_url}/liveclientdata/gamestats"
        self._total_probes += 1
        result = _http_get_json(url, self._timeout_s)
        self._last_latency_ms = result.latency_ms
        self._total_latency_ms += result.latency_ms
        if result.success:
            self._successful_probes += 1
        else:
            self._failed_probes += 1
        return result

    def _fetch_allgamedata(
        self,
    ) -> Tuple[ConnectionState, Optional[Dict[str, Any]]]:
        """获取完整游戏数据: GET /liveclientdata/allgamedata."""
        url = f"{self._base_url}/liveclientdata/allgamedata"
        self._total_probes += 1
        result = _http_get_json(url, self._timeout_s)
        self._last_latency_ms = result.latency_ms
        self._total_latency_ms += result.latency_ms

        if not result.success:
            self._failed_probes += 1
            self._on_probe_failed(result)
            return self._state, None

        self._successful_probes += 1
        self._backoff.reset()

        # Stale detection
        if result.data:
            game_data = result.data.get("gameData", {})
            gt = game_data.get("gameTime", 0.0)
            track_status = self._game_tracker.update(gt)

            if track_status == "game_ended":
                logger.info("游戏结束 (数据停止更新 %d ticks)",
                            self._game_tracker.stale_count)
                self._transition(ConnectionState.CONNECTED)
                self._game_tracker.reset()
                return self._state, None

            if track_status == "stale":
                if self._state != ConnectionState.STALE:
                    self._transition(ConnectionState.STALE)

        return self._state, result.data

    def _on_probe_failed(self, probe: ProbeResult) -> None:
        """探测失败处理: 进入 backoff."""
        delay = self._backoff.next_delay()
        self._next_probe_time = time.monotonic() + delay
        if self._state != ConnectionState.BACKOFF:
            self._transition(ConnectionState.BACKOFF)
        if self._backoff.attempt <= 3 or self._backoff.attempt % 10 == 0:
            logger.warning(
                "LCU 探测失败 (attempt=%d, next=%.1fs): %s",
                self._backoff.attempt, delay, probe.error,
            )

    def _transition(self, new_state: ConnectionState) -> None:
        """状态转换, 触发回调."""
        old = self._state
        if old == new_state:
            return
        self._state = new_state
        logger.info("Connection: %s → %s", old.name, new_state.name)
        for cb in self._state_callbacks:
            try:
                cb(old, new_state)
            except Exception:
                logger.exception("state_change callback error")

    # ─── Introspection ───────────────────────────────────────────────

    @property
    def state(self) -> ConnectionState:
        return self._state

    @property
    def is_game_active(self) -> bool:
        return self._state in (
            ConnectionState.GAME_ACTIVE,
            ConnectionState.STALE,
        )

    @property
    def last_game_time(self) -> float:
        return self._game_tracker.last_game_time

    def stats(self) -> Dict[str, Any]:
        return {
            "state": self._state.name,
            "total_probes": self._total_probes,
            "successful_probes": self._successful_probes,
            "failed_probes": self._failed_probes,
            "last_latency_ms": round(self._last_latency_ms, 2),
            "avg_latency_ms": round(
                self._total_latency_ms / max(1, self._total_probes), 2
            ),
            "backoff_attempt": self._backoff.attempt,
            "backoff_delay_s": round(self._backoff.current_delay, 2),
            "game_time": self._game_tracker.last_game_time,
            "stale_count": self._game_tracker.stale_count,
            "games_detected": self._game_detected_count,
        }

    def health_score(self) -> float:
        """Connection health [0,1] for monitor/fitness integration."""
        if self._total_probes > 0:
            sr = self._successful_probes / self._total_probes
        else:
            sr = 0.0
        avg_lat = self._total_latency_ms / max(1, self._total_probes)
        lf = max(0.0, 1.0 - avg_lat / 2000.0)
        state_scores = {
            ConnectionState.GAME_ACTIVE: 1.0, ConnectionState.CONNECTED: 0.8,
            ConnectionState.STALE: 0.4, ConnectionState.CONNECTING: 0.3,
            ConnectionState.DISCONNECTED: 0.1, ConnectionState.ERROR: 0.0,
        }
        sf = state_scores.get(self._state, 0.0)
        return sr * 0.4 + lf * 0.3 + sf * 0.3

    def force_reconnect(self) -> None:
        """Force immediate reconnection (resets backoff)."""
        self._backoff.reset()
        self._state = ConnectionState.CONNECTING
