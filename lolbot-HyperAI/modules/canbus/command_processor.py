"""
modules/canbus/command_processor.py — Apollo Command Processor
================================================================

Apollo reference:
    canbus_component.cc:218-237   OnControlCommand()
    canbus_component.cc:277-282   OnGuardianCommand()
    canbus_component.cc:319-342   OnChassisCommand()

查看 Apollo canbus_component.cc 上现有 OnControlCommand 的实现方式，
理解其模式，特别是 **命令接收** 和 **频率限制** 是如何分离的。

从 Apollo `OnControlCommand` 这个好例子开始。然后，遵循该模式实现
一个新的 `CommandProcessor`，让 CanbusComponent 可以安全地接收
来自 planning/control 的命令，并能防止命令过快导致的问题。

Design notes:
    - Mirrors Apollo's min_cmd_interval check
    - For LoL: rate limit strategy updates
    - Thread-safe command queue
    - Last command caching for timeout detection

Claude30: Initial implementation based on Apollo canbus_component.cc:218-342
"""

from __future__ import annotations

import time
import threading
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Deque, Dict, Generic, Optional, TypeVar

from cyber.logger.cyber_logger import get_logger

logger = get_logger("canbus.command_processor")


# ─── Type variable for generic command type ──────────────────────────────────
T = TypeVar("T")


@dataclass
class CommandProcessorConfig:
    """Command processor configuration.
    
    Apollo equivalent: FLAGS_min_cmd_interval, etc.
    """
    # Minimum interval between commands (milliseconds)
    # Apollo: FLAGS_min_cmd_interval = 5ms
    min_cmd_interval_ms: float = 5.0
    
    # Maximum command queue size
    max_queue_size: int = 100
    
    # Enable command rate limiting
    rate_limit_enabled: bool = True
    
    # Log dropped commands
    log_dropped: bool = True


@dataclass
class ControlCommand:
    """Control command from planning module.
    
    Apollo equivalent: control::ControlCommand protobuf
    For LoL: represents a strategy/action recommendation.
    """
    sequence_num: int = 0
    timestamp_sec: float = 0.0
    
    # Strategy fields
    action: str = ""
    target: str = ""
    priority: float = 0.0
    
    # Header
    header_timestamp_sec: float = 0.0
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ControlCommand:
        """Create from dictionary."""
        return cls(
            sequence_num=data.get("sequence_num", 0),
            timestamp_sec=data.get("timestamp_sec", time.time()),
            action=data.get("action", ""),
            target=data.get("target", ""),
            priority=data.get("priority", 0.0),
            header_timestamp_sec=data.get("header_timestamp_sec", 0.0),
        )


@dataclass
class ChassisCommand:
    """Chassis command for direct vehicle control.
    
    Apollo equivalent: external_command::ChassisCommand protobuf
    For LoL: represents a direct UI/overlay command.
    """
    sequence_num: int = 0
    timestamp_sec: float = 0.0
    
    # Command type
    command_type: str = ""
    payload: Dict[str, Any] = field(default_factory=dict)
    
    # Header
    header_timestamp_sec: float = 0.0


class CommandProcessor(Generic[T]):
    """Generic command processor with rate limiting.
    
    Apollo pattern (canbus_component.cc:218-237):
        void CanbusComponent::OnControlCommand(const ControlCommand &control_command) {
          double current_timestamp = Time::Now().ToMicrosecond();
          if (current_timestamp - last_timestamp_controlcmd_ <
              FLAGS_min_cmd_interval * 1000) {
            ADEBUG << "Control command comes too soon. Ignore.";
            return;
          }
          last_timestamp_controlcmd_ = current_timestamp;
          if (!is_control_cmd_time_delay_) {
            vehicle_object_->UpdateCommand(&control_command);
          }
        }
    
    Usage::
    
        processor = CommandProcessor[ControlCommand](config)
        processor.register_handler(my_handler)
        
        # When command arrives:
        processor.on_command(cmd)
    """
    
    def __init__(self, config: Optional[CommandProcessorConfig] = None) -> None:
        self._config = config or CommandProcessorConfig()
        self._lock = threading.Lock()
        
        # Rate limiting state
        self._last_timestamp_us: float = 0.0
        
        # Command queue
        self._queue: Deque[T] = deque(maxlen=self._config.max_queue_size)
        
        # Last processed command (for timeout detection)
        self._last_command: Optional[T] = None
        self._last_command_time: float = 0.0
        
        # Handler callback
        self._handler: Optional[Callable[[T], None]] = None
        
        # Statistics
        self._stats = {
            "total_received": 0,
            "total_processed": 0,
            "total_dropped_rate_limit": 0,
            "total_dropped_queue_full": 0,
        }
    
    def on_command(self, command: T) -> bool:
        """Process an incoming command.
        
        Apollo equivalent: OnControlCommand, OnChassisCommand
        
        Args:
            command: The command to process
            
        Returns:
            True if command was processed, False if dropped
        """
        current_timestamp_us = time.time() * 1e6  # microseconds
        
        with self._lock:
            self._stats["total_received"] += 1
            
            # Rate limiting check (Apollo: FLAGS_min_cmd_interval)
            if self._config.rate_limit_enabled:
                time_since_last = current_timestamp_us - self._last_timestamp_us
                min_interval_us = self._config.min_cmd_interval_ms * 1000
                
                if time_since_last < min_interval_us:
                    self._stats["total_dropped_rate_limit"] += 1
                    if self._config.log_dropped:
                        logger.debug(
                            "Command dropped (rate limit): interval=%.1fms < min=%.1fms",
                            time_since_last / 1000,
                            self._config.min_cmd_interval_ms,
                        )
                    return False
            
            self._last_timestamp_us = current_timestamp_us
            
            # Queue the command
            if len(self._queue) >= self._config.max_queue_size:
                self._stats["total_dropped_queue_full"] += 1
                if self._config.log_dropped:
                    logger.warning(
                        "Command dropped (queue full): size=%d",
                        len(self._queue),
                    )
                return False
            
            self._queue.append(command)
            self._last_command = command
            self._last_command_time = time.time()
            self._stats["total_processed"] += 1
        
        # Invoke handler outside lock
        if self._handler:
            try:
                self._handler(command)
            except Exception as e:
                logger.error("Command handler failed: %s", e)
        
        return True
    
    def register_handler(self, handler: Callable[[T], None]) -> None:
        """Register command handler callback."""
        self._handler = handler
    
    def get_latest_command(self) -> Optional[T]:
        """Get the most recent command."""
        with self._lock:
            return self._last_command
    
    def get_latest_command_time(self) -> float:
        """Get timestamp of most recent command."""
        with self._lock:
            return self._last_command_time
    
    def drain_queue(self) -> list:
        """Drain all queued commands."""
        with self._lock:
            commands = list(self._queue)
            self._queue.clear()
            return commands
    
    def stats(self) -> Dict[str, Any]:
        """Return processor statistics."""
        with self._lock:
            return {
                **self._stats,
                "queue_size": len(self._queue),
                "last_cmd_age_s": (
                    time.time() - self._last_command_time
                    if self._last_command_time > 0 else -1
                ),
            }


class ControlCommandProcessor(CommandProcessor[ControlCommand]):
    """Specialized processor for ControlCommand.
    
    Apollo equivalent: the control_command_reader_ callback in Init()
    """
    
    def __init__(self, config: Optional[CommandProcessorConfig] = None) -> None:
        super().__init__(config)
        self._is_time_delay: bool = False
    
    def set_time_delay(self, is_delayed: bool) -> None:
        """Set time delay flag.
        
        Apollo pattern: when is_control_cmd_time_delay_ is True,
        vehicle_object_->UpdateCommand is not called.
        """
        self._is_time_delay = is_delayed
    
    @property
    def is_time_delayed(self) -> bool:
        """Check if currently in time delay state."""
        return self._is_time_delay
    
    def on_command(self, command: ControlCommand) -> bool:
        """Process control command with time delay check."""
        # Don't process if in time delay
        if self._is_time_delay:
            logger.debug(
                "Control command ignored (time delay): seq=%d",
                command.sequence_num,
            )
            return False
        
        return super().on_command(command)


class ChassisCommandProcessor(CommandProcessor[ChassisCommand]):
    """Specialized processor for ChassisCommand.
    
    Apollo equivalent: the chassis_command_reader_ callback in Init()
    """
    pass


class GuardianCommandProcessor(CommandProcessor[ControlCommand]):
    """Guardian command processor.
    
    Apollo reference: canbus_component.cc:277-282 OnGuardianCommand
    
    Guardian commands wrap control commands with additional safety info.
    When not in time delay, they are forwarded to the control command handler.
    """
    
    def __init__(
        self,
        control_processor: ControlCommandProcessor,
        config: Optional[CommandProcessorConfig] = None,
    ) -> None:
        super().__init__(config)
        self._control_processor = control_processor
    
    def on_command(self, command: ControlCommand) -> bool:
        """Process guardian command by forwarding to control processor.
        
        Apollo pattern (canbus_component.cc:277-282):
            void CanbusComponent::OnGuardianCommand(
                const GuardianCommand &guardian_command) {
              if (!is_control_cmd_time_delay_) {
                OnControlCommand(guardian_command.control_command());
              }
            }
        """
        if not self._control_processor.is_time_delayed:
            return self._control_processor.on_command(command)
        return False


# ─── Factory for creating command processors ─────────────────────────────────

class CommandProcessorFactory:
    """Factory for creating command processors."""
    
    _config: Optional[CommandProcessorConfig] = None
    _control_processor: Optional[ControlCommandProcessor] = None
    _chassis_processor: Optional[ChassisCommandProcessor] = None
    _guardian_processor: Optional[GuardianCommandProcessor] = None
    
    @classmethod
    def set_config(cls, config: CommandProcessorConfig) -> None:
        """Set global configuration."""
        cls._config = config
    
    @classmethod
    def get_control_processor(cls) -> ControlCommandProcessor:
        """Get or create control command processor (singleton)."""
        if cls._control_processor is None:
            cls._control_processor = ControlCommandProcessor(cls._config)
        return cls._control_processor
    
    @classmethod
    def get_chassis_processor(cls) -> ChassisCommandProcessor:
        """Get or create chassis command processor (singleton)."""
        if cls._chassis_processor is None:
            cls._chassis_processor = ChassisCommandProcessor(cls._config)
        return cls._chassis_processor
    
    @classmethod
    def get_guardian_processor(cls) -> GuardianCommandProcessor:
        """Get or create guardian command processor (singleton)."""
        if cls._guardian_processor is None:
            control = cls.get_control_processor()
            cls._guardian_processor = GuardianCommandProcessor(control, cls._config)
        return cls._guardian_processor
    
    @classmethod
    def reset(cls) -> None:
        """Reset all processors (for testing)."""
        cls._control_processor = None
        cls._chassis_processor = None
        cls._guardian_processor = None
