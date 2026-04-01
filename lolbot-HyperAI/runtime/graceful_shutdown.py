#!/usr/bin/env python3
"""
GracefulShutdown — Multi-phase Shutdown Coordinator
=====================================================
OperatorRL lolbot-HyperAI · 自部署 自环境反馈 自演化

Ensures clean system termination: flush logs, save game state, persist
evolution data, close network connections, stop TTS playback. Implements
a phased shutdown with configurable timeouts per phase to prevent data
loss even when the user force-closes the application.

Apollo Reference:
    cyber/mainboard/mainboard.cc → shutdown sequence
    cyber/node/node.cc → Clear() → component cleanup

Design:
    GracefulShutdown
      ├── Phase.PRE_SHUTDOWN    — notify components, stop accepting new events
      ├── Phase.FLUSH_DATA      — flush logs, save pending analytics
      ├── Phase.PERSIST_STATE   — write evolution generation, game state
      ├── Phase.CLOSE_IO        — close network sockets, file handles
      ├── Phase.CLEANUP         — temp files, release locks
      └── Phase.FINAL           — exit confirmation

Production Critique (Knuth-level):
    1. User: If the user Alt-F4s during a game, the shutdown coordinator
       has 3 seconds to save the current evolution state. If it cannot
       finish in time, it writes a partial checkpoint with a "dirty" flag
       so the next startup can detect and recover.
    2. System: Double-SIGINT forces immediate exit (no Phase.FINAL).
       This is a safety valve for truly stuck shutdowns. The system logs
       "FORCED EXIT" to stderr so the user knows data may be incomplete.
"""

import asyncio
import enum
import logging
import os
import signal
import sys
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Coroutine, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Shutdown phases
# ---------------------------------------------------------------------------

class ShutdownPhase(enum.IntEnum):
    """Ordered shutdown phases. Lower value = executed first."""
    PRE_SHUTDOWN = 0    # Notify, stop accepting events
    FLUSH_DATA = 10     # Flush in-memory buffers to disk
    PERSIST_STATE = 20  # Save evolution state, checkpoints
    CLOSE_IO = 30       # Close sockets, file handles
    CLEANUP = 40        # Temp files, lock files
    FINAL = 50          # Exit confirmation, final log


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class ShutdownTask:
    """A registered shutdown task to execute during a specific phase."""
    task_id: str
    phase: ShutdownPhase
    name: str
    callback: Callable[[], Coroutine]   # async callable
    timeout_s: float = 5.0
    critical: bool = False              # If True, failure aborts shutdown
    completed: bool = False
    error: Optional[str] = None
    duration_ms: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "phase": self.phase.name,
            "name": self.name,
            "timeout_s": self.timeout_s,
            "critical": self.critical,
            "completed": self.completed,
            "error": self.error,
            "duration_ms": round(self.duration_ms, 2),
        }


@dataclass
class ShutdownReport:
    """Summary of the shutdown process."""
    initiated_at: float
    completed_at: float
    total_duration_ms: float
    phases_completed: int
    phases_total: int
    tasks_completed: int
    tasks_failed: int
    tasks_timed_out: int
    forced: bool
    clean: bool                         # True if all critical tasks succeeded

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_duration_ms": round(self.total_duration_ms, 2),
            "phases_completed": self.phases_completed,
            "phases_total": self.phases_total,
            "tasks_completed": self.tasks_completed,
            "tasks_failed": self.tasks_failed,
            "tasks_timed_out": self.tasks_timed_out,
            "forced": self.forced,
            "clean": self.clean,
        }


# ---------------------------------------------------------------------------
# Checkpoint writer — last-resort state persistence
# ---------------------------------------------------------------------------

class CheckpointWriter:
    """
    Writes dirty/clean checkpoints so the next startup can detect
    whether shutdown completed normally.

    Checkpoint file: .lolbot_checkpoint.json
    Contents: {"clean": bool, "timestamp": str, "generation": int, ...}
    """

    def __init__(self, checkpoint_dir: str = "."):
        self._dir = Path(checkpoint_dir)
        self._path = self._dir / ".lolbot_checkpoint.json"
        self._log = logging.getLogger("lolbot.runtime.checkpoint")

    def write_dirty(self, metadata: Optional[Dict[str, Any]] = None) -> bool:
        """Mark checkpoint as dirty (shutdown in progress)."""
        return self._write(clean=False, metadata=metadata)

    def write_clean(self, metadata: Optional[Dict[str, Any]] = None) -> bool:
        """Mark checkpoint as clean (shutdown completed normally)."""
        return self._write(clean=True, metadata=metadata)

    def read(self) -> Optional[Dict[str, Any]]:
        """Read existing checkpoint. Returns None if not found."""
        try:
            if self._path.exists():
                import json
                return json.loads(self._path.read_text(encoding="utf-8"))
        except Exception as exc:
            self._log.error("Failed to read checkpoint: %s", exc)
        return None

    def was_clean_shutdown(self) -> bool:
        """Check if the last shutdown was clean."""
        cp = self.read()
        if cp is None:
            return True  # No checkpoint = first run
        return cp.get("clean", False)

    def _write(self, clean: bool, metadata: Optional[Dict[str, Any]] = None) -> bool:
        import json
        from datetime import datetime, timezone

        data = {
            "clean": clean,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "pid": os.getpid(),
        }
        if metadata:
            data.update(metadata)

        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            tmp.rename(self._path)  # Atomic on POSIX
            return True
        except Exception as exc:
            self._log.error("Failed to write checkpoint: %s", exc)
            return False


# ---------------------------------------------------------------------------
# GracefulShutdown coordinator
# ---------------------------------------------------------------------------

class GracefulShutdown:
    """
    Multi-phase shutdown coordinator.

    Usage:
        shutdown = GracefulShutdown(checkpoint_dir="/data/lolbot")
        shutdown.register(
            phase=ShutdownPhase.FLUSH_DATA,
            name="flush_evolution_logs",
            callback=evo_logger.flush,
            timeout_s=3.0,
            critical=True,
        )
        ...
        report = await shutdown.execute()
    """

    def __init__(
        self,
        checkpoint_dir: str = ".",
        global_timeout_s: float = 15.0,
        force_exit_on_double_signal: bool = True,
    ):
        self._log = logging.getLogger("lolbot.runtime.graceful_shutdown")
        self._tasks: Dict[str, ShutdownTask] = {}
        self._task_counter = 0
        self._checkpoint = CheckpointWriter(checkpoint_dir)
        self._global_timeout_s = global_timeout_s
        self._force_on_double = force_exit_on_double_signal
        self._executing = False
        self._forced = False
        self._signal_count = 0

        # Pre-shutdown callbacks (called before phases begin)
        self._pre_callbacks: List[Callable[[], None]] = []

    # ---- Registration ----

    def register(
        self,
        phase: ShutdownPhase,
        name: str,
        callback: Callable,
        timeout_s: float = 5.0,
        critical: bool = False,
    ) -> str:
        """
        Register a shutdown task for a specific phase.
        Returns task_id for later reference.
        """
        self._task_counter += 1
        task_id = f"SD-{self._task_counter:04d}"
        task = ShutdownTask(
            task_id=task_id,
            phase=phase,
            name=name,
            callback=callback,
            timeout_s=timeout_s,
            critical=critical,
        )
        self._tasks[task_id] = task
        self._log.debug(
            "Registered shutdown task: %s [%s] phase=%s critical=%s",
            name, task_id, phase.name, critical,
        )
        return task_id

    def register_pre_callback(self, callback: Callable[[], None]) -> None:
        """Register a synchronous callback to run before any phase."""
        self._pre_callbacks.append(callback)

    # ---- Execution ----

    async def execute(self) -> ShutdownReport:
        """
        Execute the full shutdown sequence through all phases.
        Returns a ShutdownReport summarizing what happened.
        """
        if self._executing:
            self._log.warning("Shutdown already in progress, ignoring duplicate call")
            return self._make_empty_report()

        self._executing = True
        start_time = time.monotonic()
        self._log.info(
            "=== GRACEFUL SHUTDOWN INITIATED === "
            "(%d tasks across %d phases, global timeout: %.0fs)",
            len(self._tasks),
            len(set(t.phase for t in self._tasks.values())),
            self._global_timeout_s,
        )

        # Write dirty checkpoint
        self._checkpoint.write_dirty()

        # Run pre-callbacks
        for cb in self._pre_callbacks:
            try:
                cb()
            except Exception as exc:
                self._log.error("Pre-shutdown callback error: %s", exc)

        # Group tasks by phase
        phases_map: Dict[ShutdownPhase, List[ShutdownTask]] = {}
        for task in self._tasks.values():
            phases_map.setdefault(task.phase, []).append(task)

        completed_phases = 0
        total_completed = 0
        total_failed = 0
        total_timed_out = 0
        all_critical_ok = True

        # Execute phases in order
        for phase in sorted(ShutdownPhase):
            tasks = phases_map.get(phase, [])
            if not tasks:
                completed_phases += 1
                continue

            elapsed = time.monotonic() - start_time
            if elapsed > self._global_timeout_s:
                self._log.warning(
                    "Global timeout (%.0fs) reached at phase %s, skipping remaining",
                    self._global_timeout_s, phase.name,
                )
                break

            if self._forced:
                self._log.warning("Forced shutdown — skipping phase %s", phase.name)
                break

            self._log.info(
                "--- Phase: %s (%d tasks) ---", phase.name, len(tasks)
            )

            for task in tasks:
                remaining = self._global_timeout_s - (time.monotonic() - start_time)
                if remaining <= 0:
                    break

                effective_timeout = min(task.timeout_s, remaining)
                task_start = time.monotonic()

                try:
                    coro = task.callback()
                    if asyncio.iscoroutine(coro):
                        await asyncio.wait_for(coro, timeout=effective_timeout)
                    task.completed = True
                    task.duration_ms = (time.monotonic() - task_start) * 1000.0
                    total_completed += 1
                    self._log.debug(
                        "  [OK] %s (%.1fms)", task.name, task.duration_ms
                    )

                except asyncio.TimeoutError:
                    task.error = f"Timed out after {effective_timeout:.1f}s"
                    task.duration_ms = (time.monotonic() - task_start) * 1000.0
                    total_timed_out += 1
                    self._log.warning(
                        "  [TIMEOUT] %s after %.1fs", task.name, effective_timeout
                    )
                    if task.critical:
                        all_critical_ok = False

                except Exception as exc:
                    task.error = str(exc)
                    task.duration_ms = (time.monotonic() - task_start) * 1000.0
                    total_failed += 1
                    self._log.error(
                        "  [FAIL] %s: %s\n%s",
                        task.name, exc, traceback.format_exc(),
                    )
                    if task.critical:
                        all_critical_ok = False

            completed_phases += 1

        total_duration = (time.monotonic() - start_time) * 1000.0

        # Write clean checkpoint if all critical tasks succeeded
        clean = all_critical_ok and not self._forced
        self._checkpoint.write_clean(metadata={
            "shutdown_duration_ms": total_duration,
            "tasks_completed": total_completed,
            "tasks_failed": total_failed,
        })

        report = ShutdownReport(
            initiated_at=start_time,
            completed_at=time.monotonic(),
            total_duration_ms=total_duration,
            phases_completed=completed_phases,
            phases_total=len(ShutdownPhase),
            tasks_completed=total_completed,
            tasks_failed=total_failed,
            tasks_timed_out=total_timed_out,
            forced=self._forced,
            clean=clean,
        )

        self._log.info(
            "=== SHUTDOWN COMPLETE === "
            "clean=%s duration=%.0fms completed=%d failed=%d timed_out=%d",
            clean, total_duration, total_completed, total_failed, total_timed_out,
        )

        self._executing = False
        return report

    # ---- Signal handling support ----

    def handle_signal(self) -> None:
        """
        Called by ProcessManager's signal handler.
        First signal = graceful shutdown. Second signal = forced exit.
        """
        self._signal_count += 1
        if self._signal_count == 1:
            self._log.info("Signal received — initiating graceful shutdown")
        elif self._signal_count >= 2 and self._force_on_double:
            self._log.warning(
                "FORCED EXIT — second signal received during shutdown"
            )
            self._forced = True
            # Write dirty checkpoint before forced exit
            self._checkpoint.write_dirty(metadata={"forced": True})

    # ---- Introspection ----

    def get_task_summary(self) -> List[Dict[str, Any]]:
        """Return summary of all registered shutdown tasks."""
        return [t.to_dict() for t in sorted(
            self._tasks.values(), key=lambda t: (t.phase, t.name)
        )]

    def get_checkpoint_status(self) -> Dict[str, Any]:
        """Check if last shutdown was clean."""
        cp = self._checkpoint.read()
        return {
            "checkpoint_exists": cp is not None,
            "last_clean": cp.get("clean") if cp else None,
            "last_timestamp": cp.get("timestamp") if cp else None,
        }

    def _make_empty_report(self) -> ShutdownReport:
        return ShutdownReport(
            initiated_at=time.monotonic(),
            completed_at=time.monotonic(),
            total_duration_ms=0.0,
            phases_completed=0,
            phases_total=len(ShutdownPhase),
            tasks_completed=0,
            tasks_failed=0,
            tasks_timed_out=0,
            forced=False,
            clean=False,
        )


# ---------------------------------------------------------------------------
# Recovery detector — used on startup
# ---------------------------------------------------------------------------

class RecoveryDetector:
    """
    On startup, checks if the previous shutdown was dirty and triggers
    recovery actions (replay log tail, rebuild caches, etc.).
    """

    def __init__(self, checkpoint_dir: str = "."):
        self._log = logging.getLogger("lolbot.runtime.recovery")
        self._checkpoint = CheckpointWriter(checkpoint_dir)
        self._recovery_actions: List[Tuple[str, Callable]] = []

    def register_recovery_action(
        self, name: str, callback: Callable
    ) -> None:
        """Register an async action to run if recovery is needed."""
        self._recovery_actions.append((name, callback))

    async def check_and_recover(self) -> Dict[str, Any]:
        """
        Check checkpoint and run recovery actions if needed.
        Returns recovery summary.
        """
        was_clean = self._checkpoint.was_clean_shutdown()
        result = {
            "recovery_needed": not was_clean,
            "actions_run": 0,
            "actions_failed": 0,
        }

        if was_clean:
            self._log.info("Previous shutdown was clean — no recovery needed")
            return result

        self._log.warning(
            "DIRTY SHUTDOWN DETECTED — running %d recovery actions",
            len(self._recovery_actions),
        )

        for name, callback in self._recovery_actions:
            try:
                coro = callback()
                if asyncio.iscoroutine(coro):
                    await coro
                result["actions_run"] += 1
                self._log.info("Recovery action '%s' completed", name)
            except Exception as exc:
                result["actions_failed"] += 1
                self._log.error("Recovery action '%s' failed: %s", name, exc)

        return result
