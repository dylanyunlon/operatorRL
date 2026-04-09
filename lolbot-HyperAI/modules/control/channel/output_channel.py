"""
modules/control/channel/output_channel.py
==========================================
从 Apollo ``control/controller_agent.cc`` 这个好例子开始——它将子控制器
(lat/lon/mpc) 从 control_component.cc 分离到独立文件。然后, 遵循该模式
实现 ``OutputChannel`` 基类, 让 ``ControlComponent`` 可以通过 registry
模式管理输出渠道, 并能独立测试每个渠道的 cooldown/priority/error 逻辑。

Extracted verbatim from ControlComponent (Claude25 b0178fa):
    OutputChannelState, OutputChannelStats, OutputChannel
Zero logic changes.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict

from cyber.logger.cyber_logger import get_logger
from modules.control.action_dispatch.action_dispatcher import (
    ActionPriority,
    DispatchAction,
)

logger = get_logger("control.channel")


class OutputChannelState(Enum):
    ACTIVE = auto()
    DEGRADED = auto()
    DISABLED = auto()


@dataclass
class OutputChannelStats:
    total_dispatched: int = 0
    total_dropped: int = 0
    total_errors: int = 0
    last_dispatch_time: float = 0.0
    last_error: str = ""
    state: OutputChannelState = OutputChannelState.ACTIVE
    consecutive_errors: int = 0

    def record_dispatch(self) -> None:
        self.total_dispatched += 1
        self.last_dispatch_time = time.monotonic()
        self.consecutive_errors = 0

    def record_error(self, error: str) -> None:
        self.total_errors += 1
        self.consecutive_errors += 1
        self.last_error = error

    def record_drop(self) -> None:
        self.total_dropped += 1

    def snapshot(self) -> Dict[str, Any]:
        return {
            "total_dispatched": self.total_dispatched,
            "total_dropped": self.total_dropped,
            "total_errors": self.total_errors,
            "state": self.state.name,
            "consecutive_errors": self.consecutive_errors,
            "last_error": self.last_error,
        }


class OutputChannel:
    """Abstract output channel. Verbatim from Claude25 control_component.py."""

    MAX_CONSECUTIVE_ERRORS = 10

    def __init__(
        self, name: str,
        min_priority: ActionPriority = ActionPriority.LOW,
        cooldown_s: float = 0.0,
    ) -> None:
        self.name = name
        self.min_priority = min_priority
        self.cooldown_s = cooldown_s
        self.stats = OutputChannelStats()
        self._cooldown_tracker: Dict[str, float] = {}

    def dispatch(self, action: DispatchAction) -> bool:
        if action.priority.value < self.min_priority.value:
            self.stats.record_drop()
            return False
        if self.stats.state == OutputChannelState.DISABLED:
            self.stats.record_drop()
            return False
        if self.cooldown_s > 0 and action.dedup_key:
            last = self._cooldown_tracker.get(action.dedup_key, 0.0)
            if time.monotonic() - last < self.cooldown_s:
                self.stats.record_drop()
                return False
        try:
            self._do_output(action)
            self.stats.record_dispatch()
            if action.dedup_key:
                self._cooldown_tracker[action.dedup_key] = time.monotonic()
            if self.stats.state == OutputChannelState.DEGRADED:
                self.stats.state = OutputChannelState.ACTIVE
            return True
        except Exception as exc:
            self.stats.record_error(f"{type(exc).__name__}: {exc}")
            if self.stats.consecutive_errors >= self.MAX_CONSECUTIVE_ERRORS:
                self.stats.state = OutputChannelState.DISABLED
                logger.warning("[OutputChannel:%s] auto-disabled after %d errors",
                               self.name, self.stats.consecutive_errors)
            elif self.stats.consecutive_errors >= 3:
                self.stats.state = OutputChannelState.DEGRADED
            return False

    def _do_output(self, action: DispatchAction) -> None:
        raise NotImplementedError

    def enable(self) -> None:
        self.stats.state = OutputChannelState.ACTIVE
        self.stats.consecutive_errors = 0

    def disable(self) -> None:
        self.stats.state = OutputChannelState.DISABLED

    def flush(self) -> None:
        pass

    def shutdown(self) -> None:
        self.flush()
