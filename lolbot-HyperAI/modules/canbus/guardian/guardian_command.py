"""
modules/canbus/guardian/guardian_command.py — Guardian Command Data Structure
===============================================================================

Apollo reference:
    modules/guardian/proto/guardian.proto
    modules/guardian/guardian.cc

查看 Apollo guardian.proto 上现有 GuardianCommand 的定义，理解其模式，
特别是 **控制命令包装** 和 **安全状态** 是如何组合的。

从 Apollo GuardianCommand protobuf 这个好例子开始。然后，遵循该模式实现
一个新的 Python dataclass，让 lolbot-HyperAI 可以传递带安全标记的命令。

Design notes:
    - Wraps ControlCommand with safety metadata
    - Includes timestamp for timeout detection
    - Supports estop flag for emergency situations
    - Builder pattern for convenient construction

Claude30: Initial implementation
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, Optional

from modules.common.util.header_util import MessageHeader, create_header


class GuardianStatus(Enum):
    """Guardian safety status."""
    NORMAL = auto()           # Normal operation
    TIMEOUT_WARNING = auto()  # Approaching timeout threshold
    TIMEOUT = auto()          # Command timed out
    ESTOP = auto()            # Emergency stop active
    RECOVERING = auto()       # Recovering from estop
    MANUAL = auto()           # Manual override active


@dataclass
class ControlCommandContent:
    """Control command content (wrapped by GuardianCommand).
    
    Apollo equivalent: control::ControlCommand fields
    For LoL: strategy/action recommendations
    """
    # Throttle/brake equivalent — for LoL: aggressiveness
    throttle: float = 0.0      # 0.0 = passive, 1.0 = aggressive
    brake: float = 0.0         # 0.0 = none, 1.0 = full stop
    
    # Steering equivalent — for LoL: target selection
    steering_target: float = 0.0   # -1.0 = left, 1.0 = right
    steering_rate: float = 25.0    # degrees per second
    
    # LoL-specific fields
    action: str = ""           # Action type: "engage", "retreat", "farm", etc.
    target: str = ""           # Target: champion name, objective, etc.
    confidence: float = 1.0    # Prediction confidence
    priority: float = 0.5      # Action priority
    
    # Debug info
    debug_info: str = ""
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "throttle": self.throttle,
            "brake": self.brake,
            "steering_target": self.steering_target,
            "steering_rate": self.steering_rate,
            "action": self.action,
            "target": self.target,
            "confidence": self.confidence,
            "priority": self.priority,
            "debug_info": self.debug_info,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ControlCommandContent:
        """Create from dictionary."""
        return cls(
            throttle=data.get("throttle", 0.0),
            brake=data.get("brake", 0.0),
            steering_target=data.get("steering_target", 0.0),
            steering_rate=data.get("steering_rate", 25.0),
            action=data.get("action", ""),
            target=data.get("target", ""),
            confidence=data.get("confidence", 1.0),
            priority=data.get("priority", 0.5),
            debug_info=data.get("debug_info", ""),
        )
    
    def apply_estop(self) -> None:
        """Apply emergency stop values.
        
        Apollo pattern (canbus_component.cc:376-383):
            set_throttle(0.0)
            set_steering_target(0.0)
            set_brake(FLAGS_estop_brake)
        """
        self.throttle = 0.0
        self.steering_target = 0.0
        self.steering_rate = 25.0
        self.brake = 1.0  # FLAGS_estop_brake equivalent
        self.confidence = 0.0
        self.action = "estop"


@dataclass
class GuardianCommand:
    """Guardian command wrapping a control command.
    
    Apollo equivalent: guardian::GuardianCommand protobuf
    
    This wraps a ControlCommand with additional safety metadata:
    - Timestamp for timeout detection
    - Sequence number for ordering
    - Status for safety state
    - Estop flag for emergency situations
    
    Usage::
    
        cmd = GuardianCommandBuilder()\\
            .with_action("engage")\\
            .with_target("enemy_adc")\\
            .with_confidence(0.85)\\
            .build()
        
        # Check if timed out
        if cmd.is_timed_out(max_delay_s=0.5):
            cmd.apply_estop()
    """
    header: MessageHeader = field(default_factory=lambda: create_header("guardian"))
    control_command: ControlCommandContent = field(default_factory=ControlCommandContent)
    
    # Safety status
    status: GuardianStatus = GuardianStatus.NORMAL
    
    # Emergency stop flag
    estop: bool = False
    estop_reason: str = ""
    
    # Ultrasonic distance (Apollo: for obstacle detection)
    # For LoL: threat level (0.0 = safe, 1.0 = immediate danger)
    ultrasonic_distance: float = 0.0
    
    # Turn signal (Apollo: left/right indicator)
    # For LoL: ping type (0 = none, 1 = danger, 2 = assist, etc.)
    turn_signal: int = 0
    
    def mutable_header(self) -> MessageHeader:
        """Get mutable header (protobuf style)."""
        return self.header
    
    def mutable_control_command(self) -> ControlCommandContent:
        """Get mutable control command (protobuf style)."""
        return self.control_command
    
    def is_timed_out(self, max_delay_s: float) -> bool:
        """Check if this command is timed out.
        
        Args:
            max_delay_s: Maximum allowed delay in seconds
            
        Returns:
            True if command is older than max_delay_s
        """
        age = time.time() - self.header.timestamp_sec
        return age > max_delay_s
    
    def get_age_sec(self) -> float:
        """Get command age in seconds."""
        return time.time() - self.header.timestamp_sec
    
    def get_age_ms(self) -> float:
        """Get command age in milliseconds."""
        return self.get_age_sec() * 1000
    
    def apply_estop(self, reason: str = "timeout") -> None:
        """Apply emergency stop to this command.
        
        This modifies the control command to be "safe":
        - Zero throttle/confidence
        - Full brake
        - Status set to ESTOP
        """
        self.estop = True
        self.estop_reason = reason
        self.status = GuardianStatus.ESTOP
        self.control_command.apply_estop()
    
    def copy(self) -> GuardianCommand:
        """Create a deep copy of this command."""
        return GuardianCommand(
            header=MessageHeader(
                timestamp_sec=self.header.timestamp_sec,
                module_name=self.header.module_name,
                sequence_num=self.header.sequence_num,
                frame_id=self.header.frame_id,
            ),
            control_command=ControlCommandContent(
                throttle=self.control_command.throttle,
                brake=self.control_command.brake,
                steering_target=self.control_command.steering_target,
                steering_rate=self.control_command.steering_rate,
                action=self.control_command.action,
                target=self.control_command.target,
                confidence=self.control_command.confidence,
                priority=self.control_command.priority,
            ),
            status=self.status,
            estop=self.estop,
            estop_reason=self.estop_reason,
            ultrasonic_distance=self.ultrasonic_distance,
            turn_signal=self.turn_signal,
        )
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "header": self.header.to_dict(),
            "control_command": self.control_command.to_dict(),
            "status": self.status.name,
            "estop": self.estop,
            "estop_reason": self.estop_reason,
            "ultrasonic_distance": self.ultrasonic_distance,
            "turn_signal": self.turn_signal,
        }
    
    def short_debug_string(self) -> str:
        """Get short debug string (Apollo ShortDebugString pattern)."""
        return (
            f"GuardianCommand(seq={self.header.sequence_num}, "
            f"age={self.get_age_ms():.1f}ms, "
            f"status={self.status.name}, "
            f"action={self.control_command.action}, "
            f"conf={self.control_command.confidence:.2f}, "
            f"estop={self.estop})"
        )


class GuardianCommandBuilder:
    """Builder for constructing GuardianCommand.
    
    Usage::
    
        cmd = GuardianCommandBuilder()\\
            .with_action("engage")\\
            .with_target("enemy_adc")\\
            .with_confidence(0.85)\\
            .with_priority(0.9)\\
            .build()
    """
    
    def __init__(self) -> None:
        self._cmd = GuardianCommand()
        self._cmd.header = create_header("guardian")
    
    def with_action(self, action: str) -> GuardianCommandBuilder:
        """Set action type."""
        self._cmd.control_command.action = action
        return self
    
    def with_target(self, target: str) -> GuardianCommandBuilder:
        """Set target."""
        self._cmd.control_command.target = target
        return self
    
    def with_confidence(self, confidence: float) -> GuardianCommandBuilder:
        """Set confidence (0.0-1.0)."""
        self._cmd.control_command.confidence = max(0.0, min(1.0, confidence))
        return self
    
    def with_priority(self, priority: float) -> GuardianCommandBuilder:
        """Set priority (0.0-1.0)."""
        self._cmd.control_command.priority = max(0.0, min(1.0, priority))
        return self
    
    def with_throttle(self, throttle: float) -> GuardianCommandBuilder:
        """Set throttle/aggressiveness (0.0-1.0)."""
        self._cmd.control_command.throttle = max(0.0, min(1.0, throttle))
        return self
    
    def with_brake(self, brake: float) -> GuardianCommandBuilder:
        """Set brake (0.0-1.0)."""
        self._cmd.control_command.brake = max(0.0, min(1.0, brake))
        return self
    
    def with_status(self, status: GuardianStatus) -> GuardianCommandBuilder:
        """Set guardian status."""
        self._cmd.status = status
        return self
    
    def with_estop(self, reason: str = "") -> GuardianCommandBuilder:
        """Set estop flag."""
        self._cmd.estop = True
        self._cmd.estop_reason = reason
        self._cmd.status = GuardianStatus.ESTOP
        return self
    
    def with_threat_level(self, level: float) -> GuardianCommandBuilder:
        """Set threat level (ultrasonic_distance equivalent)."""
        self._cmd.ultrasonic_distance = max(0.0, min(1.0, level))
        return self
    
    def with_ping_type(self, ping_type: int) -> GuardianCommandBuilder:
        """Set ping type (turn_signal equivalent)."""
        self._cmd.turn_signal = ping_type
        return self
    
    def with_module_name(self, name: str) -> GuardianCommandBuilder:
        """Set source module name."""
        self._cmd.header.module_name = name
        return self
    
    def build(self) -> GuardianCommand:
        """Build and return the GuardianCommand."""
        # Ensure timestamp is current
        self._cmd.header.timestamp_sec = time.time()
        return self._cmd


# ─── Factory functions ────────────────────────────────────────────────────────

def create_estop_command(reason: str = "emergency") -> GuardianCommand:
    """Create an estop GuardianCommand.
    
    Convenience function for quickly creating an emergency stop command.
    """
    return GuardianCommandBuilder()\
        .with_estop(reason)\
        .with_action("estop")\
        .with_confidence(0.0)\
        .with_throttle(0.0)\
        .with_brake(1.0)\
        .build()


def create_normal_command(
    action: str,
    target: str = "",
    confidence: float = 1.0,
) -> GuardianCommand:
    """Create a normal GuardianCommand.
    
    Convenience function for common case.
    """
    return GuardianCommandBuilder()\
        .with_action(action)\
        .with_target(target)\
        .with_confidence(confidence)\
        .with_status(GuardianStatus.NORMAL)\
        .build()
