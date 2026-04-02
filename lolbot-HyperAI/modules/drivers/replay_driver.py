"""
ReplayDriver — Feed CyberNode channels from .cyberrecord files.
================================================================

A TimerComponent that reads recorded messages via RecordReader
and publishes them to CyberNode channels, simulating live data
input for offline testing and development.

Architecture position:
    modules/drivers/replay_driver.py   ← YOU ARE HERE
    ├─ Reads: .cyberrecord files via cyber/record/record_reader.py
    ├─ Publishes: all recorded channels via CyberNode
    └─ Replaces: modules/canbus/ during offline replay

Apollo reference:
    cyber/tools/cyber_recorder/player.cc — record playback
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set

from cyber.component.timer_component import (
    ComponentConfig, TimerComponent,
)
from cyber.node.node import CyberNode, Writer
from cyber.record.record_reader import RecordReader, ReplayConfig

logger = logging.getLogger(__name__)

_REPLAY_INTERVAL_MS: float = 50.0  # 20Hz check loop


@dataclass
class ReplayDriverConfig:
    """Configuration for the replay driver."""
    record_path: str = ""
    speed: float = 1.0
    include_channels: Optional[Set[str]] = None
    exclude_channels: Set[str] = None
    start_time_ns: int = 0
    end_time_ns: int = 0
    loop: bool = False
    auto_start: bool = True

    def __post_init__(self):
        if self.exclude_channels is None:
            self.exclude_channels = set()


class ReplayDriver(TimerComponent):
    """Replay recorded messages into live CyberNode channels.

    Usage::

        driver = ReplayDriver(ReplayDriverConfig(
            record_path="data/records/session.cyberrecord",
            speed=2.0,
        ))
        driver.initialize()
        driver.start()
        # Messages now flow into CyberNode channels at 2x speed
    """

    def __init__(self, config: Optional[ReplayDriverConfig] = None) -> None:
        super().__init__(
            config=ComponentConfig(
                name="replay_driver",
                interval_ms=_REPLAY_INTERVAL_MS,
                warn_threshold_ms=200.0,
            )
        )
        self._replay_config = config or ReplayDriverConfig()
        self._node: Optional[CyberNode] = None
        self._reader: Optional[RecordReader] = None
        self._writers: Dict[str, Writer] = {}
        self._message_count: int = 0
        self._channel_counts: Dict[str, int] = {}
        self._is_playing: bool = False
        self._play_thread: Optional[threading.Thread] = None

    def Init(self) -> bool:
        path = Path(self._replay_config.record_path)
        if not path.exists():
            logger.error("Replay file not found: %s", path)
            return False

        self._node = CyberNode("replay_driver")

        replay_conf = ReplayConfig(
            speed=self._replay_config.speed,
            include_channels=self._replay_config.include_channels,
            exclude_channels=self._replay_config.exclude_channels,
            start_time_ns=self._replay_config.start_time_ns,
            end_time_ns=self._replay_config.end_time_ns,
            loop=self._replay_config.loop,
            callback=self._on_message,
        )
        self._reader = RecordReader(replay_conf)

        try:
            header = self._reader.load(path)
            logger.info(
                "ReplayDriver loaded: %s (msgs=%d, channels=%d)",
                path.name,
                header.message_count,
                header.channel_count,
            )
        except Exception as exc:
            logger.error("Failed to load replay: %s", exc)
            return False

        if self._replay_config.auto_start:
            self._start_playback()

        return True

    def _start_playback(self) -> None:
        if self._is_playing or self._reader is None:
            return
        self._is_playing = True
        self._reader.play(blocking=False)
        logger.info("ReplayDriver playback started (speed=%.1fx)",
                     self._replay_config.speed)

    def _on_message(self, channel: str, payload: Any, ts_ns: int) -> None:
        """Callback from RecordReader for each replayed message."""
        if channel not in self._writers and self._node:
            self._writers[channel] = self._node.create_writer(channel)

        writer = self._writers.get(channel)
        if writer:
            writer.write(payload)

        self._message_count += 1
        self._channel_counts[channel] = (
            self._channel_counts.get(channel, 0) + 1
        )

    def Proc(self) -> bool:
        """Check replay status each tick."""
        if self._reader is None:
            return True

        if self._reader.state.name == "FINISHED":
            if not self._replay_config.loop:
                logger.info("ReplayDriver finished: %d messages replayed",
                            self._message_count)
                self._is_playing = False
            return True

        return True

    def pause(self) -> None:
        if self._reader:
            self._reader.pause()

    def resume(self) -> None:
        if self._reader:
            self._reader.resume()

    def on_shutdown(self) -> None:
        if self._reader:
            self._reader.close()
        self._is_playing = False

    def status_dict(self) -> Dict[str, Any]:
        reader_state = "N/A"
        if self._reader:
            reader_state = self._reader.state.name

        return {
            "is_playing": self._is_playing,
            "reader_state": reader_state,
            "message_count": self._message_count,
            "channels_written": len(self._writers),
            "channel_counts": dict(self._channel_counts),
            "speed": self._replay_config.speed,
            "record_path": self._replay_config.record_path,
        }
