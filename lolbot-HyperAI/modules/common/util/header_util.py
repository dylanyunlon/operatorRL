"""
modules/common/util/header_util.py — Apollo FillHeader Utility
================================================================

Apollo reference:
    modules/common/util/message_util.h   FillHeader()

查看 Apollo common/util/message_util.h 上现有 FillHeader 的实现方式，
理解其模式，特别是 **消息头填充** 和 **时间戳管理** 是如何标准化的。

从 Apollo `FillHeader` 这个好例子开始。然后，遵循该模式实现
一个新的 `fill_header`，让所有消息发布可以统一填充标准头信息。

Claude30: Initial implementation
"""

from __future__ import annotations

import time
import threading
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol, runtime_checkable

from cyber.logger.cyber_logger import get_logger

logger = get_logger("common.util.header")


# ─── Sequence number generator ────────────────────────────────────────────────

class SequenceGenerator:
    """Thread-safe sequence number generator."""
    
    _instance: Optional[SequenceGenerator] = None
    _lock = threading.Lock()
    
    def __init__(self) -> None:
        self._sequences: dict = {}
        self._seq_lock = threading.Lock()
    
    @classmethod
    def instance(cls) -> SequenceGenerator:
        """Get singleton instance."""
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance
    
    def next(self, module_name: str) -> int:
        """Get next sequence number for a module."""
        with self._seq_lock:
            if module_name not in self._sequences:
                self._sequences[module_name] = 0
            self._sequences[module_name] += 1
            return self._sequences[module_name]
    
    def current(self, module_name: str) -> int:
        """Get current sequence number (without incrementing)."""
        with self._seq_lock:
            return self._sequences.get(module_name, 0)
    
    def reset(self, module_name: Optional[str] = None) -> None:
        """Reset sequence number(s)."""
        with self._seq_lock:
            if module_name:
                self._sequences[module_name] = 0
            else:
                self._sequences.clear()


# ─── Message Header ───────────────────────────────────────────────────────────

@dataclass
class MessageHeader:
    """Standard message header.
    
    Apollo equivalent: common_msgs/basic_msgs/header.proto
    """
    timestamp_sec: float = 0.0
    module_name: str = ""
    sequence_num: int = 0
    
    # Optional fields
    lidar_timestamp: float = 0.0
    camera_timestamp: float = 0.0
    radar_timestamp: float = 0.0
    
    # Frame ID for coordinate transforms
    frame_id: str = ""
    
    # Status code
    status_code: int = 0
    
    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "timestamp_sec": self.timestamp_sec,
            "module_name": self.module_name,
            "sequence_num": self.sequence_num,
            "lidar_timestamp": self.lidar_timestamp,
            "camera_timestamp": self.camera_timestamp,
            "radar_timestamp": self.radar_timestamp,
            "frame_id": self.frame_id,
            "status_code": self.status_code,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> MessageHeader:
        """Create from dictionary."""
        return cls(
            timestamp_sec=data.get("timestamp_sec", 0.0),
            module_name=data.get("module_name", ""),
            sequence_num=data.get("sequence_num", 0),
            lidar_timestamp=data.get("lidar_timestamp", 0.0),
            camera_timestamp=data.get("camera_timestamp", 0.0),
            radar_timestamp=data.get("radar_timestamp", 0.0),
            frame_id=data.get("frame_id", ""),
            status_code=data.get("status_code", 0),
        )


# ─── Protocol for messages with headers ───────────────────────────────────────

@runtime_checkable
class HasHeader(Protocol):
    """Protocol for messages that have a header field."""
    header: MessageHeader


@runtime_checkable
class HasMutableHeader(Protocol):
    """Protocol for messages with mutable header."""
    def mutable_header(self) -> MessageHeader: ...


# ─── FillHeader implementation ────────────────────────────────────────────────

def fill_header(module_name: str, message: Any) -> None:
    """Fill standard header fields in a message.
    
    Apollo equivalent: common::util::FillHeader(module_name, message)
    
    This sets:
        - timestamp_sec: current time
        - module_name: the publishing module
        - sequence_num: auto-incrementing sequence
    
    Args:
        module_name: Name of the publishing module
        message: Message object with 'header' attribute or 'mutable_header()' method
        
    Example::
    
        prediction_msg = PredictionObstacles()
        fill_header("prediction", prediction_msg)
        writer.Write(prediction_msg)
    """
    seq_gen = SequenceGenerator.instance()
    current_time = time.time()
    seq_num = seq_gen.next(module_name)
    
    # Try to get header via mutable_header() method (protobuf style)
    if hasattr(message, 'mutable_header') and callable(message.mutable_header):
        header = message.mutable_header()
        header.timestamp_sec = current_time
        header.module_name = module_name
        header.sequence_num = seq_num
        return
    
    # Try direct header attribute
    if hasattr(message, 'header'):
        header = message.header
        if isinstance(header, MessageHeader):
            header.timestamp_sec = current_time
            header.module_name = module_name
            header.sequence_num = seq_num
        elif isinstance(header, dict):
            header["timestamp_sec"] = current_time
            header["module_name"] = module_name
            header["sequence_num"] = seq_num
        return
    
    # Try to set header as new attribute
    if hasattr(message, '__dict__'):
        message.header = MessageHeader(
            timestamp_sec=current_time,
            module_name=module_name,
            sequence_num=seq_num,
        )
        return
    
    logger.warning(
        "Cannot fill header for message type %s",
        type(message).__name__,
    )


def create_header(module_name: str) -> MessageHeader:
    """Create a new header with standard fields filled.
    
    Use this when the message type doesn't have a header field,
    and you need to create one separately.
    
    Args:
        module_name: Name of the publishing module
        
    Returns:
        New MessageHeader with timestamp, module_name, sequence_num filled
    """
    seq_gen = SequenceGenerator.instance()
    return MessageHeader(
        timestamp_sec=time.time(),
        module_name=module_name,
        sequence_num=seq_gen.next(module_name),
    )


# ─── Timestamp utilities ──────────────────────────────────────────────────────

def now_sec() -> float:
    """Get current time in seconds (float)."""
    return time.time()


def now_us() -> int:
    """Get current time in microseconds (int)."""
    return int(time.time() * 1_000_000)


def sec_to_us(sec: float) -> int:
    """Convert seconds to microseconds."""
    return int(sec * 1_000_000)


def us_to_sec(us: int) -> float:
    """Convert microseconds to seconds."""
    return us / 1_000_000


def timestamp_age_sec(timestamp_sec: float) -> float:
    """Calculate age of a timestamp in seconds."""
    return time.time() - timestamp_sec


def timestamp_age_ms(timestamp_sec: float) -> float:
    """Calculate age of a timestamp in milliseconds."""
    return (time.time() - timestamp_sec) * 1000


def is_timestamp_fresh(
    timestamp_sec: float,
    max_age_sec: float,
) -> bool:
    """Check if a timestamp is within acceptable age.
    
    Args:
        timestamp_sec: Timestamp to check
        max_age_sec: Maximum acceptable age in seconds
        
    Returns:
        True if timestamp is fresh (age <= max_age_sec)
    """
    return timestamp_age_sec(timestamp_sec) <= max_age_sec
