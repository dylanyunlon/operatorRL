"""
launch/guardian_manager.py — Guardian Lifecycle Manager
=========================================================

Apollo reference:
    modules/guardian/guardian.cc          Guardian module
    cyber/mainboard/module_controller.cc  Module lifecycle

查看 Apollo guardian.cc 上现有 Guardian 的实现方式，理解其模式，
特别是 **超时监控** 和 **紧急降级** 是如何在主循环中集成的。

从 Apollo Guardian 模块这个好例子开始。然后，遵循该模式实现
一个新的 `GuardianManager`，让 MainLoop 可以统一管理所有
Guardian 相关功能。

Design notes:
    - Coordinates timeout_handler, watchdog, estop_handler
    - Single point of integration for main_loop.py
    - Manages Guardian lifecycle (init, start, stop)
    - Provides unified status API

Claude30: Initial implementation
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Set

from cyber.logger.cyber_logger import get_logger
from cyber.timer.watchdog import WatchdogManager, WatchdogConfig
from cyber.sysmo.guardian_monitor import GuardianMonitor, GuardianMonitorConfig
from modules.canbus.timeout_handler import (
    GuardianTimeoutHandler,
    GuardianTimeoutConfig,
    TimeoutRecovery,
)
from modules.control.estop_handler import EstopHandler, EstopConfig, EstopTrigger

logger = get_logger("launch.guardian")


@dataclass
class GuardianManagerConfig:
    """Guardian manager configuration."""
    # Enable guardian functionality
    enabled: bool = True
    
    # Component timeout config
    timeout_config: GuardianTimeoutConfig = None
    
    # Watchdog config
    watchdog_config: WatchdogConfig = None
    
    # Monitor config
    monitor_config: GuardianMonitorConfig = None
    
    # Estop config
    estop_config: EstopConfig = None
    
    # Components to protect
    protected_components: List[str] = None
    
    def __post_init__(self):
        if self.timeout_config is None:
            self.timeout_config = GuardianTimeoutConfig()
        if self.watchdog_config is None:
            self.watchdog_config = WatchdogConfig()
        if self.monitor_config is None:
            self.monitor_config = GuardianMonitorConfig()
        if self.estop_config is None:
            self.estop_config = EstopConfig()
        if self.protected_components is None:
            self.protected_components = [
                "canbus", "perception", "prediction", "planning", "control"
            ]


class GuardianManager:
    """Unified manager for all Guardian functionality.
    
    This integrates:
    - GuardianTimeoutHandler: per-component timeout detection
    - WatchdogManager: Proc() execution monitoring
    - GuardianMonitor: system-wide timeout aggregation
    - EstopHandler: emergency stop coordination
    
    Apollo pattern: Guardian module runs at 10Hz, monitors
    control commands, triggers emergency brake when timeout.
    
    Usage::
    
        manager = GuardianManager(config)
        manager.init()
        manager.start()
        
        # In Proc() loop:
        manager.on_component_proc_start("canbus")
        # ... do work ...
        manager.on_component_proc_end("canbus")
        
        # On command:
        manager.on_command_received("canbus", cmd_timestamp)
        
        # On shutdown:
        manager.stop()
    """
    
    _instance: Optional[GuardianManager] = None
    _lock = threading.Lock()
    
    def __init__(self, config: Optional[GuardianManagerConfig] = None) -> None:
        self._config = config or GuardianManagerConfig()
        
        # Sub-managers
        self._timeout_handlers: Dict[str, GuardianTimeoutHandler] = {}
        self._timeout_recovery = TimeoutRecovery()
        self._watchdog_manager = WatchdogManager.instance()
        self._guardian_monitor = GuardianMonitor.instance()
        self._estop_handler = EstopHandler.instance()
        
        # State
        self._initialized = False
        self._running = False
        self._component_states: Dict[str, Dict[str, Any]] = {}
        
        # Callbacks
        self._degraded_callbacks: List[Callable[[Set[str]], None]] = []
        self._estop_callbacks: List[Callable[[str], None]] = []
        self._recovery_callbacks: List[Callable[[], None]] = []
    
    @classmethod
    def instance(cls) -> GuardianManager:
        """Get singleton instance."""
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance
    
    def init(self) -> bool:
        """Initialize Guardian manager.
        
        Returns:
            True if initialization succeeded
        """
        if self._initialized:
            logger.warning("GuardianManager already initialized")
            return True
        
        if not self._config.enabled:
            logger.info("Guardian functionality disabled")
            self._initialized = True
            return True
        
        logger.info("Initializing GuardianManager...")
        
        # Create timeout handler for each component
        for component in self._config.protected_components:
            handler = GuardianTimeoutHandler(self._config.timeout_config)
            handler.register_timeout_callback(
                lambda cmd, c=component: self._on_component_timeout(c, cmd)
            )
            handler.register_recovery_callback(
                lambda c=component: self._on_component_recovery(c)
            )
            self._timeout_handlers[component] = handler
            
            # Create watchdog
            self._watchdog_manager.create_watchdog(
                component,
                self._config.watchdog_config,
            )
            
            # Initialize state
            self._component_states[component] = {
                "last_cmd_time": 0.0,
                "last_proc_time": 0.0,
                "is_timed_out": False,
                "is_proc_running": False,
            }
        
        # Configure guardian monitor
        self._guardian_monitor = GuardianMonitor(self._config.monitor_config)
        self._guardian_monitor.register_estop_callback(self._on_global_estop)
        self._guardian_monitor.register_recovery_callback(self._on_global_recovery)
        self._guardian_monitor.register_degraded_callback(self._on_degraded)
        
        # Configure estop handler
        self._estop_handler = EstopHandler(self._config.estop_config)
        self._estop_handler.register_estop_callback(self._on_estop_triggered)
        self._estop_handler.register_recovery_callback(self._on_estop_recovery)
        
        self._initialized = True
        logger.info(
            "GuardianManager initialized: %d components protected",
            len(self._config.protected_components),
        )
        return True
    
    def start(self) -> bool:
        """Start Guardian manager.
        
        Returns:
            True if start succeeded
        """
        if not self._initialized:
            logger.error("GuardianManager not initialized")
            return False
        
        if self._running:
            logger.warning("GuardianManager already running")
            return True
        
        if not self._config.enabled:
            self._running = True
            return True
        
        logger.info("Starting GuardianManager...")
        
        # Start watchdogs
        self._watchdog_manager.start_all()
        
        # Start guardian monitor
        self._guardian_monitor.start()
        
        self._running = True
        logger.info("GuardianManager started")
        return True
    
    def stop(self) -> None:
        """Stop Guardian manager."""
        if not self._running:
            return
        
        logger.info("Stopping GuardianManager...")
        
        # Stop watchdogs
        self._watchdog_manager.stop_all()
        
        # Stop guardian monitor
        self._guardian_monitor.stop()
        
        self._running = False
        logger.info("GuardianManager stopped")
    
    # ─── Component event handlers ─────────────────────────────────────────
    
    def on_component_proc_start(self, component: str) -> None:
        """Called when a component's Proc() starts."""
        if component not in self._component_states:
            return
        
        watchdog = self._watchdog_manager.get_watchdog(component)
        if watchdog:
            watchdog.begin_proc()
        
        self._component_states[component]["is_proc_running"] = True
        self._component_states[component]["last_proc_time"] = time.time()
    
    def on_component_proc_end(self, component: str) -> None:
        """Called when a component's Proc() ends."""
        if component not in self._component_states:
            return
        
        watchdog = self._watchdog_manager.get_watchdog(component)
        if watchdog:
            watchdog.end_proc()
        
        self._component_states[component]["is_proc_running"] = False
    
    def on_command_received(self, component: str, cmd_timestamp: float) -> bool:
        """Called when a command is received for a component.
        
        Args:
            component: Component name
            cmd_timestamp: Command timestamp
            
        Returns:
            True if command is valid (not timed out)
        """
        if component not in self._timeout_handlers:
            return True
        
        self._component_states[component]["last_cmd_time"] = cmd_timestamp
        
        handler = self._timeout_handlers[component]
        is_timed_out = handler.check_timeout(cmd_timestamp)
        
        prev_timed_out = self._component_states[component]["is_timed_out"]
        self._component_states[component]["is_timed_out"] = is_timed_out
        
        # Handle state transition
        self._timeout_recovery.process_timeout_transition(
            prev_timed_out, is_timed_out
        )
        
        return not is_timed_out
    
    # ─── Internal callbacks ───────────────────────────────────────────────
    
    def _on_component_timeout(self, component: str, cmd: Any) -> None:
        """Handle component timeout."""
        logger.warning("Component %s timed out", component)
        self._guardian_monitor.report_timeout(component, 0.0)
    
    def _on_component_recovery(self, component: str) -> None:
        """Handle component recovery."""
        logger.info("Component %s recovered", component)
        self._guardian_monitor.report_recovery(component)
    
    def _on_global_estop(self) -> None:
        """Handle global estop from guardian monitor."""
        logger.critical("Global ESTOP triggered by GuardianMonitor")
        self._estop_handler.trigger(EstopTrigger.GUARDIAN, "guardian_monitor")
        
        for callback in self._estop_callbacks:
            try:
                callback("guardian_monitor")
            except Exception as e:
                logger.error("Estop callback failed: %s", e)
    
    def _on_global_recovery(self) -> None:
        """Handle global recovery from guardian monitor."""
        logger.info("Global recovery from GuardianMonitor")
        self._estop_handler.request_recovery()
        
        for callback in self._recovery_callbacks:
            try:
                callback()
            except Exception as e:
                logger.error("Recovery callback failed: %s", e)
    
    def _on_degraded(self, timed_out: Set[str]) -> None:
        """Handle degraded state."""
        logger.warning("System degraded: %s", ", ".join(timed_out))
        
        for callback in self._degraded_callbacks:
            try:
                callback(timed_out)
            except Exception as e:
                logger.error("Degraded callback failed: %s", e)
    
    def _on_estop_triggered(self, trigger: EstopTrigger, reason: str) -> None:
        """Handle estop trigger."""
        logger.error("Estop triggered: %s - %s", trigger.name, reason)
    
    def _on_estop_recovery(self) -> None:
        """Handle estop recovery."""
        logger.info("Estop recovery complete")
    
    # ─── Callback registration ────────────────────────────────────────────
    
    def register_degraded_callback(
        self,
        callback: Callable[[Set[str]], None],
    ) -> None:
        """Register callback for degraded state."""
        self._degraded_callbacks.append(callback)
    
    def register_estop_callback(
        self,
        callback: Callable[[str], None],
    ) -> None:
        """Register callback for estop."""
        self._estop_callbacks.append(callback)
    
    def register_recovery_callback(
        self,
        callback: Callable[[], None],
    ) -> None:
        """Register callback for recovery."""
        self._recovery_callbacks.append(callback)
    
    # ─── Status APIs ──────────────────────────────────────────────────────
    
    @property
    def is_estopped(self) -> bool:
        """Check if in estop state."""
        return self._estop_handler.is_estopped
    
    @property
    def is_degraded(self) -> bool:
        """Check if in degraded state."""
        return self._guardian_monitor.is_degraded
    
    def get_timed_out_components(self) -> Set[str]:
        """Get currently timed out components."""
        return self._guardian_monitor.get_timed_out_components()
    
    def stats(self) -> Dict[str, Any]:
        """Return comprehensive Guardian statistics."""
        component_stats = {}
        for component in self._config.protected_components:
            handler = self._timeout_handlers.get(component)
            watchdog = self._watchdog_manager.get_watchdog(component)
            component_stats[component] = {
                "state": self._component_states.get(component, {}),
                "timeout": handler.stats() if handler else {},
                "watchdog": watchdog.stats() if watchdog else {},
            }
        
        return {
            "enabled": self._config.enabled,
            "initialized": self._initialized,
            "running": self._running,
            "is_estopped": self.is_estopped,
            "is_degraded": self.is_degraded,
            "timed_out_components": list(self.get_timed_out_components()),
            "monitor": self._guardian_monitor.stats(),
            "estop": self._estop_handler.stats(),
            "components": component_stats,
        }
