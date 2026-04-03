"""
canbus/conf/canbus_conf.py — CAN Bus 配置中心
=================================================

查看 Apollo modules/canbus/conf/canbus_conf.pb.txt 上现有 protobuf 文本格式
配置的实现方式, 理解其模式, 特别是配置加载和默认值是如何分离的。从
Apollo GetProtoConfig(&canbus_conf_) 这个好例子开始。然后遵循该模式实现
一个 CanbusConf dataclass + YAML 加载器, 让 canbus_component 可以从
conf/canbus.yaml 读取所有参数, 并能在运行时热更新而不重启进程。

位置: lolbot-HyperAI/modules/canbus/conf/canbus_conf.py
"""

from __future__ import annotations

import copy
import json
import logging
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Default constants — mirrors Apollo FLAGS_* gflags pattern
# ---------------------------------------------------------------------------

_DEFAULT_LCU_HOST = "127.0.0.1"
_DEFAULT_LCU_PORT = 2999
_DEFAULT_LCU_SCHEME = "https"
_DEFAULT_LCU_TIMEOUT_S = 2.0
_DEFAULT_FIDDLER_MCP_URL = "http://127.0.0.1:8866"
_DEFAULT_FIDDLER_MCP_TIMEOUT_S = 3.0
_DEFAULT_POLL_INTERVAL_MS = 100.0
_DEFAULT_FIDDLER_POLL_RATIO = 5
_DEFAULT_BACKOFF_INITIAL_S = 1.0
_DEFAULT_BACKOFF_MAX_S = 30.0
_DEFAULT_BACKOFF_MULTIPLIER = 2.0
_DEFAULT_STALE_THRESHOLD_TICKS = 50
_DEFAULT_STALE_GAME_END_TICKS = 100
_DEFAULT_MAX_CONSECUTIVE_ERRORS = 10
_DEFAULT_HEALTH_CHECK_INTERVAL_S = 5.0
_DEFAULT_RECORDING_DIR = "data/recordings"
_DEFAULT_RECORDING_ENABLED = True
_DEFAULT_REPLAY_SPEED = 1.0
_DEFAULT_SSL_VERIFY = False


# ---------------------------------------------------------------------------
# Schema validation helpers — no external deps
# ---------------------------------------------------------------------------

class _ConfigError(Exception):
    """配置校验失败时抛出."""


def _check_range(
    name: str, value: float, low: float, high: float
) -> None:
    """确保数值在 [low, high] 范围内."""
    if not (low <= value <= high):
        raise _ConfigError(
            f"{name}={value} 超出范围 [{low}, {high}]"
        )


def _check_positive(name: str, value: float) -> None:
    """确保数值为正."""
    if value <= 0:
        raise _ConfigError(f"{name}={value} 必须为正数")


def _check_non_negative(name: str, value: float) -> None:
    if value < 0:
        raise _ConfigError(f"{name}={value} 不能为负数")


def _check_nonempty_string(name: str, value: str) -> None:
    if not value or not value.strip():
        raise _ConfigError(f"{name} 不能为空字符串")


# ---------------------------------------------------------------------------
# LCU sub-config
# ---------------------------------------------------------------------------

@dataclass
class LCUConf:
    """LoL Live Client API 连接配置.

    Attributes:
        host: LCU 监听地址, 固定 127.0.0.1.
        port: Live Client Data API 端口, 固定 2999.
        scheme: HTTP 协议, 必须 https (LCU 自签名证书).
        timeout_s: 单次 HTTP 请求超时.
        ssl_verify: 是否校验 SSL 证书 (通常 False).
        lockfile_paths: 可能的 lockfile 搜索路径列表.
        max_response_bytes: 最大响应体字节数 (防 OOM).
    """
    host: str = _DEFAULT_LCU_HOST
    port: int = _DEFAULT_LCU_PORT
    scheme: str = _DEFAULT_LCU_SCHEME
    timeout_s: float = _DEFAULT_LCU_TIMEOUT_S
    ssl_verify: bool = _DEFAULT_SSL_VERIFY
    lockfile_paths: List[str] = field(default_factory=lambda: [
        "C:/Riot Games/League of Legends/lockfile",
        "/Applications/League of Legends.app/Contents/LoL/lockfile",
    ])
    max_response_bytes: int = 2 * 1024 * 1024  # 2 MiB

    @property
    def base_url(self) -> str:
        return f"{self.scheme}://{self.host}:{self.port}"

    def validate(self) -> None:
        _check_nonempty_string("lcu.host", self.host)
        _check_range("lcu.port", self.port, 1, 65535)
        _check_positive("lcu.timeout_s", self.timeout_s)
        _check_positive("lcu.max_response_bytes", self.max_response_bytes)


# ---------------------------------------------------------------------------
# Fiddler sub-config
# ---------------------------------------------------------------------------

@dataclass
class FiddlerConf:
    """Fiddler MCP 代理配置.

    Attributes:
        enabled: 是否启用 Fiddler 数据源.
        mcp_url: Fiddler MCP bridge 地址.
        timeout_s: MCP 请求超时.
        poll_ratio: 每 N 个 canbus tick 轮询一次 (降频).
    """
    enabled: bool = False
    mcp_url: str = _DEFAULT_FIDDLER_MCP_URL
    timeout_s: float = _DEFAULT_FIDDLER_MCP_TIMEOUT_S
    poll_ratio: int = _DEFAULT_FIDDLER_POLL_RATIO

    def validate(self) -> None:
        if self.enabled:
            _check_nonempty_string("fiddler.mcp_url", self.mcp_url)
            _check_positive("fiddler.timeout_s", self.timeout_s)
            _check_range("fiddler.poll_ratio", self.poll_ratio, 1, 100)


# ---------------------------------------------------------------------------
# Connection resilience sub-config
# ---------------------------------------------------------------------------

@dataclass
class ResilienceConf:
    """重连与容错配置.

    Attributes:
        backoff_initial_s: 首次重连等待.
        backoff_max_s: 最大重连等待.
        backoff_multiplier: 指数退避倍率.
        stale_threshold_ticks: 连续多少 tick 数据不更新算 stale.
        stale_game_end_ticks: 连续多少 tick stale 判定游戏结束.
        max_consecutive_errors: 连续错误上限后告警.
        health_check_interval_s: 健康检查周期.
    """
    backoff_initial_s: float = _DEFAULT_BACKOFF_INITIAL_S
    backoff_max_s: float = _DEFAULT_BACKOFF_MAX_S
    backoff_multiplier: float = _DEFAULT_BACKOFF_MULTIPLIER
    stale_threshold_ticks: int = _DEFAULT_STALE_THRESHOLD_TICKS
    stale_game_end_ticks: int = _DEFAULT_STALE_GAME_END_TICKS
    max_consecutive_errors: int = _DEFAULT_MAX_CONSECUTIVE_ERRORS
    health_check_interval_s: float = _DEFAULT_HEALTH_CHECK_INTERVAL_S

    def validate(self) -> None:
        _check_positive("resilience.backoff_initial_s", self.backoff_initial_s)
        _check_positive("resilience.backoff_max_s", self.backoff_max_s)
        _check_range(
            "resilience.backoff_multiplier", self.backoff_multiplier, 1.0, 10.0
        )
        _check_range(
            "resilience.stale_threshold_ticks",
            self.stale_threshold_ticks, 1, 10000,
        )
        if self.stale_game_end_ticks <= self.stale_threshold_ticks:
            raise _ConfigError(
                "stale_game_end_ticks 必须大于 stale_threshold_ticks"
            )


# ---------------------------------------------------------------------------
# Recording sub-config
# ---------------------------------------------------------------------------

@dataclass
class RecordingConf:
    """录制与回放配置.

    Attributes:
        enabled: 是否录制消息流.
        directory: 录制文件保存目录.
        replay_speed: 回放速率倍数 (1.0 = 实时).
        compress: 是否压缩录制文件.
        max_file_size_mb: 单文件最大 MB, 超出自动轮转.
    """
    enabled: bool = _DEFAULT_RECORDING_ENABLED
    directory: str = _DEFAULT_RECORDING_DIR
    replay_speed: float = _DEFAULT_REPLAY_SPEED
    compress: bool = True
    max_file_size_mb: int = 50

    def validate(self) -> None:
        _check_nonempty_string("recording.directory", self.directory)
        _check_positive("recording.replay_speed", self.replay_speed)
        _check_range("recording.max_file_size_mb", self.max_file_size_mb, 1, 1000)


# ---------------------------------------------------------------------------
# Channel configuration
# ---------------------------------------------------------------------------

@dataclass
class ChannelConf:
    """CAN 总线发布频道名配置.

    将频道名集中管理, 修改频道名时只改配置文件.
    """
    raw_lcu: str = "/lol/raw_lcu"
    raw_fiddler: str = "/lol/raw_fiddler"
    canbus_status: str = "/lol/canbus_status"
    game_state: str = "/lol/game_state"
    events: str = "/lol/events"
    kill_feed: str = "/lol/kill_feed"
    minimap_state: str = "/lol/minimap_state"
    win_probability: str = "/lol/win_probability"
    strategy: str = "/lol/strategy_recommendation"
    voice_output: str = "/lol/voice_output"
    system_heartbeat: str = "/lol/system/heartbeat"
    system_error: str = "/lol/system/error"
    monitor_status: str = "/lol/monitor_status"

    def validate(self) -> None:
        for fld_name in self.__dataclass_fields__:
            val = getattr(self, fld_name)
            if not val.startswith("/"):
                raise _ConfigError(
                    f"channel.{fld_name}={val!r} 必须以 '/' 开头"
                )


# ---------------------------------------------------------------------------
# Top-level CanbusConf
# ---------------------------------------------------------------------------

@dataclass
class CanbusConf:
    """CAN Bus 组件顶级配置.

    聚合所有子配置, 支持从 YAML/JSON 文件加载.

    Attributes:
        poll_interval_ms: Proc() 周期 (毫秒).
        data_source: 数据源类型 ("lcu", "replay", "mock").
        replay_file: 回放文件路径 (data_source="replay" 时使用).
        lcu: LCU 连接配置.
        fiddler: Fiddler 配置.
        resilience: 容错配置.
        recording: 录制配置.
        channels: 频道名配置.
    """
    poll_interval_ms: float = _DEFAULT_POLL_INTERVAL_MS
    data_source: str = "lcu"
    replay_file: str = ""
    lcu: LCUConf = field(default_factory=LCUConf)
    fiddler: FiddlerConf = field(default_factory=FiddlerConf)
    resilience: ResilienceConf = field(default_factory=ResilienceConf)
    recording: RecordingConf = field(default_factory=RecordingConf)
    channels: ChannelConf = field(default_factory=ChannelConf)

    def validate(self) -> List[str]:
        """校验全部配置, 返回错误列表 (空列表=通过).

        Returns:
            错误消息列表.
        """
        errors: List[str] = []
        try:
            _check_range("poll_interval_ms", self.poll_interval_ms, 10, 10000)
        except _ConfigError as e:
            errors.append(str(e))

        if self.data_source not in ("lcu", "replay", "mock", "fiddler"):
            errors.append(
                f"data_source={self.data_source!r} 不在合法值列表中"
            )

        if self.data_source == "replay" and not self.replay_file:
            errors.append("data_source=replay 但 replay_file 为空")

        for sub in (
            self.lcu, self.fiddler, self.resilience,
            self.recording, self.channels,
        ):
            try:
                sub.validate()
            except _ConfigError as e:
                errors.append(str(e))

        return errors

    def validate_or_raise(self) -> None:
        """校验, 有错误时抛出 _ConfigError."""
        errors = self.validate()
        if errors:
            raise _ConfigError(
                "配置校验失败:\n  " + "\n  ".join(errors)
            )

    def to_dict(self) -> Dict[str, Any]:
        """序列化为嵌套 dict (JSON 兼容)."""
        return asdict(self)

    def diff(self, other: CanbusConf) -> Dict[str, Tuple[Any, Any]]:
        """比较两份配置的差异.

        Returns:
            dict of {field_path: (old_value, new_value)}.
        """
        result: Dict[str, Tuple[Any, Any]] = {}
        _diff_dicts(self.to_dict(), other.to_dict(), "", result)
        return result


def _diff_dicts(
    a: Dict[str, Any],
    b: Dict[str, Any],
    prefix: str,
    out: Dict[str, Tuple[Any, Any]],
) -> None:
    """递归比较两个 dict."""
    all_keys = set(a.keys()) | set(b.keys())
    for k in sorted(all_keys):
        path = f"{prefix}.{k}" if prefix else k
        va = a.get(k)
        vb = b.get(k)
        if isinstance(va, dict) and isinstance(vb, dict):
            _diff_dicts(va, vb, path, out)
        elif va != vb:
            out[path] = (va, vb)


# ---------------------------------------------------------------------------
# Loader — from JSON/YAML file (YAML parsed as subset via JSON fallback)
# ---------------------------------------------------------------------------

def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """递归合并 override 到 base, 返回新 dict."""
    merged = copy.deepcopy(base)
    for key, val in override.items():
        if (
            key in merged
            and isinstance(merged[key], dict)
            and isinstance(val, dict)
        ):
            merged[key] = _deep_merge(merged[key], val)
        else:
            merged[key] = copy.deepcopy(val)
    return merged


def _dict_to_conf(data: Dict[str, Any]) -> CanbusConf:
    """从扁平 dict 构建 CanbusConf, 忽略多余 key."""
    lcu_data = data.get("lcu", {})
    fiddler_data = data.get("fiddler", {})
    resilience_data = data.get("resilience", {})
    recording_data = data.get("recording", {})
    channels_data = data.get("channels", {})

    # 过滤合法字段
    def _filter(cls, d: Dict[str, Any]) -> Dict[str, Any]:
        valid = set(cls.__dataclass_fields__.keys())
        return {k: v for k, v in d.items() if k in valid}

    return CanbusConf(
        poll_interval_ms=data.get("poll_interval_ms", _DEFAULT_POLL_INTERVAL_MS),
        data_source=data.get("data_source", "lcu"),
        replay_file=data.get("replay_file", ""),
        lcu=LCUConf(**_filter(LCUConf, lcu_data)),
        fiddler=FiddlerConf(**_filter(FiddlerConf, fiddler_data)),
        resilience=ResilienceConf(**_filter(ResilienceConf, resilience_data)),
        recording=RecordingConf(**_filter(RecordingConf, recording_data)),
        channels=ChannelConf(**_filter(ChannelConf, channels_data)),
    )


def load_canbus_conf(
    filepath: Optional[Path] = None,
    overrides: Optional[Dict[str, Any]] = None,
) -> CanbusConf:
    """从文件加载配置, 合并命令行覆盖.

    支持 JSON 格式. 文件不存在时使用全默认值.

    Args:
        filepath: 配置文件路径 (None → 纯默认).
        overrides: 命令行或环境变量覆盖值.

    Returns:
        校验通过的 CanbusConf.

    Raises:
        _ConfigError: 配置校验失败.
    """
    file_data: Dict[str, Any] = {}

    if filepath is not None:
        p = Path(filepath)
        if p.exists():
            try:
                raw = p.read_text(encoding="utf-8")
                file_data = json.loads(raw)
                logger.info("已加载配置文件: %s", p)
            except json.JSONDecodeError as exc:
                raise _ConfigError(f"配置文件 JSON 解析失败: {p}: {exc}")
            except OSError as exc:
                raise _ConfigError(f"配置文件读取失败: {p}: {exc}")
        else:
            logger.info("配置文件不存在, 使用默认值: %s", p)

    # 环境变量覆盖: LOLBOT_CANBUS_* → 扁平 key
    env_data = _collect_env_overrides()
    merged = _deep_merge(file_data, env_data)
    if overrides:
        merged = _deep_merge(merged, overrides)

    conf = _dict_to_conf(merged)
    conf.validate_or_raise()
    return conf


def _collect_env_overrides() -> Dict[str, Any]:
    """收集 LOLBOT_CANBUS_* 环境变量.

    映射规则:
        LOLBOT_CANBUS_POLL_INTERVAL_MS=50 → {"poll_interval_ms": 50}
        LOLBOT_CANBUS_LCU_TIMEOUT_S=3.0 → {"lcu": {"timeout_s": 3.0}}
    """
    prefix = "LOLBOT_CANBUS_"
    result: Dict[str, Any] = {}

    for key, val in os.environ.items():
        if not key.startswith(prefix):
            continue
        suffix = key[len(prefix):].lower()
        parts = suffix.split("_", 1)

        # 尝试类型转换
        typed_val: Any = val
        try:
            typed_val = int(val)
        except ValueError:
            try:
                typed_val = float(val)
            except ValueError:
                if val.lower() in ("true", "false"):
                    typed_val = val.lower() == "true"

        if len(parts) == 2 and parts[0] in (
            "lcu", "fiddler", "resilience", "recording", "channels"
        ):
            sub = result.setdefault(parts[0], {})
            sub[parts[1]] = typed_val
        else:
            result[suffix] = typed_val

    return result


# ---------------------------------------------------------------------------
# Hot-reload watcher — 文件变更时自动重载
# ---------------------------------------------------------------------------

class ConfigWatcher:
    """监控配置文件变更, 触发回调.

    非线程阻塞: 在 Proc() 中定期调用 check() 即可.

    Usage::

        watcher = ConfigWatcher(Path("conf/canbus.json"), on_change)
        # 在每个 Proc() tick 中:
        watcher.check()
    """

    def __init__(
        self,
        filepath: Path,
        callback: Callable[[CanbusConf, CanbusConf], None],
        check_interval_s: float = 5.0,
    ) -> None:
        self._filepath = Path(filepath)
        self._callback = callback
        self._check_interval_s = check_interval_s
        self._last_check: float = 0.0
        self._last_mtime: float = 0.0
        self._current_conf: Optional[CanbusConf] = None
        self._lock = threading.Lock()

    def set_current(self, conf: CanbusConf) -> None:
        """设置当前生效配置 (首次加载后调用)."""
        with self._lock:
            self._current_conf = conf
            if self._filepath.exists():
                self._last_mtime = self._filepath.stat().st_mtime

    def check(self) -> bool:
        """检查配置文件是否有变更.

        Returns:
            True 如果配置已重载.
        """
        now = time.monotonic()
        if now - self._last_check < self._check_interval_s:
            return False
        self._last_check = now

        if not self._filepath.exists():
            return False

        try:
            current_mtime = self._filepath.stat().st_mtime
        except OSError:
            return False

        if current_mtime <= self._last_mtime:
            return False

        self._last_mtime = current_mtime

        try:
            new_conf = load_canbus_conf(self._filepath)
        except _ConfigError as exc:
            logger.error("热更新配置校验失败, 保持原配置: %s", exc)
            return False

        with self._lock:
            old_conf = self._current_conf
            if old_conf is None:
                self._current_conf = new_conf
                return True

            diff = old_conf.diff(new_conf)
            if not diff:
                return False

            logger.info(
                "配置热更新: %d 项变更",
                len(diff),
            )
            for path, (old_v, new_v) in diff.items():
                logger.info("  %s: %r → %r", path, old_v, new_v)

            self._current_conf = new_conf
            try:
                self._callback(old_conf, new_conf)
            except Exception:
                logger.exception("配置热更新回调异常")

            return True

    @property
    def current(self) -> Optional[CanbusConf]:
        with self._lock:
            return self._current_conf


# ---------------------------------------------------------------------------
# Convenience: dump default config to file
# ---------------------------------------------------------------------------

def dump_default_conf(filepath: Path) -> None:
    """将默认配置写入 JSON 文件 (用于生成模板)."""
    conf = CanbusConf()
    filepath.parent.mkdir(parents=True, exist_ok=True)
    filepath.write_text(
        json.dumps(conf.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    logger.info("默认配置已写入: %s", filepath)
