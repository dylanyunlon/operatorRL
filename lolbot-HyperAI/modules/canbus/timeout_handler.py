"""
modules/canbus/timeout_handler.py — Apollo Guardian Timeout Handler
=====================================================================

Apollo reference:
    canbus_component.cc:376-383   ProcessGuardianCmdTimeout()
    canbus_component.cc:349-373   ProcessTimeoutByClearCanSender()

查看 Apollo canbus_component.cc 上现有 ProcessGuardianCmdTimeout 的实现方式，
理解其模式，特别是 **超时时紧急停止** 和 **清除发送协议** 是如何分离的。

从 Apollo `ProcessGuardianCmdTimeout` 这个好例子开始。然后，遵循该模式实现
一个新的 `GuardianTimeoutHandler`，让 CanbusComponent 可以在命令超时时
执行紧急降级，并能保护游戏状态一致性。

Design notes:
    - Mirrors Apollo's estop brake pattern (set throttle=0, brake=FLAGS_estop_brake)
    - For LoL: timeout → stop issuing aggressive recommendations
    - Graceful degradation: still provide basic info, just no predictions
    - Recovery: auto-resume when commands arrive within threshold

Claude30: Initial implementation based on Apollo canbus_component.cc:376-383
"""

from __future__ import annotations

import time
import threading
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Dict, Optional

from cyber.logger.cyber_logger import get_logger

logger = get_logger("canbus.timeout_handler")


# ─── Configuration (Apollo gflags equivalent) ────────────────────────────────

@dataclass
class GuardianTimeoutConfig:
    """Guardian timeout configuration.
    
    Apollo equivalent: FLAGS_estop_brake, FLAGS_max_control_miss_num, etc.
    """
    # Maximum allowed time delay for commands (seconds)
    max_cmd_delay_s: float = 0.5  # Apollo: FLAGS_max_control_miss_num * FLAGS_control_period
    
    # Maximum consecutive timeout events before escalation
    max_consecutive_timeouts: int = 5
    
    # Cooldown before recovery attempt (seconds)
    recovery_cooldown_s: float = 2.0
    
    # Enable timeout checking
    enabled: bool = True
    
    # Estop brake equivalent — for LoL, this is "conservative mode"
    # (e.g., stop recommending aggressive plays)
    estop_conservative_mode: bool = True


class TimeoutState(Enum):
    """Guardian timeout state machine."""
    NORMAL = auto()
    WARNING = auto()       # approaching timeout
    TIMEOUT = auto()       # timeout occurred
    RECOVERY = auto()      # attempting recovery
    ESTOP = auto()         # emergency stop mode


@dataclass
class GuardianCommand:
    """Guardian command data structure.
    
    Apollo equivalent: guardian::GuardianCommand protobuf
    For LoL: represents a control command from planning to control.
    """
    sequence_num: int = 0
    timestamp_sec: float = 0.0
    
    # Control fields (Apollo: throttle, brake, steering)
    # For LoL: confidence, aggressiveness, action_type
    confidence: float = 1.0
    aggressiveness: float = 0.5
    action_type: str = "normal"
    
    # Header info
    module_name: str = ""
    
    def copy(self) -> GuardianCommand:
        """Create a copy of this command."""
        return GuardianCommand(
            sequence_num=self.sequence_num,
            timestamp_sec=self.timestamp_sec,
            confidence=self.confidence,
            aggressiveness=self.aggressiveness,
            action_type=self.action_type,
            module_name=self.module_name,
        )


class GuardianTimeoutHandler:
    """Apollo ProcessGuardianCmdTimeout equivalent.
    
    Monitors command timing and triggers emergency degradation when
    commands arrive too late. This is the safety net for the entire
    pipeline — if perception/prediction/planning stalls, we don't
    want to keep acting on stale recommendations.
    
    Apollo pattern (canbus_component.cc:376-383):
        void CanbusComponent::ProcessGuardianCmdTimeout(
            GuardianCommand *guardian_command) {
          AINFO << "Into cmd timeout process, set estop.";
          guardian_command->mutable_control_command()->set_throttle(0.0);
          guardian_command->mutable_control_command()->set_steering_target(0.0);
          guardian_command->mutable_control_command()->set_steering_rate(25.0);
          guardian_command->mutable_control_command()->set_brake(FLAGS_estop_brake);
        }
    
    For LoL, "estop" means:
        - Set confidence to 0 (don't trust stale predictions)
        - Set aggressiveness to 0 (be conservative)
        - Set action_type to "timeout_degraded"
    
    Usage::
    
        handler = GuardianTimeoutHandler(config)
        
        # In Proc() loop:
        if handler.check_timeout(last_cmd_timestamp):
            handler.process_timeout(current_command)
    """
    
    def __init__(self, config: Optional[GuardianTimeoutConfig] = None) -> None:
        self._config = config or GuardianTimeoutConfig()
        self._state = TimeoutState.NORMAL
        self._consecutive_timeouts = 0
        self._last_timeout_time = 0.0
        self._last_recovery_time = 0.0
        self._lock = threading.Lock()
        
        # Callbacks
        self._on_timeout_callback: Optional[Callable[[GuardianCommand], None]] = None
        self._on_recovery_callback: Optional[Callable[[], None]] = None
        
        # Statistics
        self._stats = {
            "total_timeouts": 0,
            "total_recoveries": 0,
            "current_state": self._state.name,
            "max_consecutive": 0,
        }
    
    def check_timeout(self, cmd_timestamp_sec: float) -> bool:
        """Check if command is timed out.
        
        Apollo equivalent: OnControlCommandCheck (canbus_component.cc:239-275)
        
        Args:
            cmd_timestamp_sec: Timestamp when command was issued
            
        Returns:
            True if command is timed out (exceeds max_cmd_delay_s)
        """
        if not self._config.enabled:
            return False
            
        current_time = time.time()
        cmd_delay = current_time - cmd_timestamp_sec
        
        if cmd_delay > self._config.max_cmd_delay_s:
            with self._lock:
                self._consecutive_timeouts += 1
                self._stats["total_timeouts"] += 1
                self._stats["max_consecutive"] = max(
                    self._stats["max_consecutive"],
                    self._consecutive_timeouts,
                )
                
                if self._consecutive_timeouts >= self._config.max_consecutive_timeouts:
                    self._state = TimeoutState.ESTOP
                else:
                    self._state = TimeoutState.TIMEOUT
                    
                self._stats["current_state"] = self._state.name
                self._last_timeout_time = current_time
                
            logger.warning(
                "Guardian cmd timeout: delay=%.3fs > threshold=%.3fs, "
                "consecutive=%d, state=%s",
                cmd_delay, self._config.max_cmd_delay_s,
                self._consecutive_timeouts, self._state.name,
            )
            return True
            
        # Command is within threshold — check for recovery
        if self._state in (TimeoutState.TIMEOUT, TimeoutState.ESTOP, TimeoutState.RECOVERY):
            self._attempt_recovery()
            
        return False
    
    def process_timeout(self, command: GuardianCommand) -> GuardianCommand:
        """Process a timed-out command by applying estop.
        
        Apollo equivalent: ProcessGuardianCmdTimeout (canbus_component.cc:376-383)
        
        This modifies the command to be "safe" — for LoL, this means:
        - Zero confidence (don't trust stale predictions)
        - Zero aggressiveness (be conservative)
        - Mark as degraded
        
        Args:
            command: The command to process
            
        Returns:
            Modified command with estop values
        """
        logger.info("Processing guardian cmd timeout, applying estop/degradation")
        
        # Create copy to avoid modifying original
        safe_cmd = command.copy()
        
        # Apollo pattern: set throttle=0, brake=estop_brake
        # For LoL: set confidence=0, aggressiveness=0
        safe_cmd.confidence = 0.0
        safe_cmd.aggressiveness = 0.0
        safe_cmd.action_type = "timeout_degraded"
        
        # Invoke callback if registered
        if self._on_timeout_callback:
            try:
                self._on_timeout_callback(safe_cmd)
            except Exception as e:
                logger.error("Timeout callback failed: %s", e)
        
        return safe_cmd
    
    def _attempt_recovery(self) -> None:
        """Attempt to recover from timeout state.
        
        Apollo pattern: check if commands are arriving on time again,
        then transition back to NORMAL state.
        """
        current_time = time.time()
        
        if current_time - self._last_timeout_time < self._config.recovery_cooldown_s:
            # Still in cooldown
            self._state = TimeoutState.RECOVERY
            return
            
        with self._lock:
            self._consecutive_timeouts = 0
            self._state = TimeoutState.NORMAL
            self._stats["total_recoveries"] += 1
            self._stats["current_state"] = self._state.name
            self._last_recovery_time = current_time
            
        logger.info("Guardian timeout recovery complete, state=NORMAL")
        
        if self._on_recovery_callback:
            try:
                self._on_recovery_callback()
            except Exception as e:
                logger.error("Recovery callback failed: %s", e)
    
    def register_timeout_callback(
        self,
        callback: Callable[[GuardianCommand], None],
    ) -> None:
        """Register callback for timeout events."""
        self._on_timeout_callback = callback
    
    def register_recovery_callback(self, callback: Callable[[], None]) -> None:
        """Register callback for recovery events."""
        self._on_recovery_callback = callback
    
    @property
    def state(self) -> TimeoutState:
        """Current timeout state."""
        return self._state
    
    @property
    def is_timed_out(self) -> bool:
        """Check if currently in timeout or estop state."""
        return self._state in (TimeoutState.TIMEOUT, TimeoutState.ESTOP)
    
    def stats(self) -> Dict[str, Any]:
        """Return timeout statistics."""
        with self._lock:
            return dict(self._stats)


class TimeoutRecovery:
    """Apollo ProcessTimeoutByClearCanSender equivalent.
    
    Apollo reference: canbus_component.cc:349-373
    
    Manages the transition between timeout and normal states,
    including clearing/restoring send protocols.
    """
    
    def __init__(self) -> None:
        self._is_protocol_cleared = False
        self._lock = threading.Lock()
    
    def clear_send_protocol(self) -> None:
        """Clear send protocol during timeout.
        
        Apollo equivalent: vehicle_object_->ClearSendProtocol()
        For LoL: stop sending predictions/recommendations.
        """
        with self._lock:
            if not self._is_protocol_cleared:
                logger.info("Clearing send protocol due to timeout")
                self._is_protocol_cleared = True
    
    def add_send_protocol(self) -> None:
        """Restore send protocol after recovery.
        
        Apollo equivalent: vehicle_object_->AddSendProtocol()
        For LoL: resume sending predictions/recommendations.
        """
        with self._lock:
            if self._is_protocol_cleared:
                logger.info("Restoring send protocol after recovery")
                self._is_protocol_cleared = False
    
    def is_protocol_cleared(self) -> bool:
        """Check if send protocol is currently cleared."""
        with self._lock:
            return self._is_protocol_cleared
    
    def process_timeout_transition(
        self,
        was_timed_out: bool,
        is_timed_out: bool,
    ) -> None:
        """Handle state transitions for timeout.
        
        Apollo pattern (canbus_component.cc:349-373):
            if (!is_control_cmd_time_delay_previous_ && is_control_cmd_time_delay_) {
                vehicle_object_->ClearSendProtocol();
            } else if (is_control_cmd_time_delay_previous_ && !is_control_cmd_time_delay_) {
                vehicle_object_->AddSendProtocol();
            }
        """
        if not was_timed_out and is_timed_out:
            # Entering timeout
            self.clear_send_protocol()
        elif was_timed_out and not is_timed_out:
            # Exiting timeout
            self.add_send_protocol()
