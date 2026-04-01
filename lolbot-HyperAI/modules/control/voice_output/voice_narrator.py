"""
VoiceNarrator — Text-to-speech voice output for game advice (1Hz).
===================================================================

Reads VoiceCommand messages from ``/lol/voice_command`` and produces
spoken audio output.  Manages a priority queue of pending narrations,
deduplication, and rate-limiting to avoid overwhelming the player.

Architecture position:
    modules/control/voice_output/voice_narrator.py   ← YOU ARE HERE
    ├─ Reads: /lol/voice_command (VoiceCommand from planning)
    ├─ Reads: /lol/win_prediction (for periodic win prob announcements)
    ├─ Output: Audio via system TTS (pyttsx3 / edge-tts / OS native)
    └─ Publishes: /lol/voice_status (StatusMessage)

Apollo reference:
    modules/audio/audio_component.cc — audio processing pipeline
    modules/control/control_component.cc — command output

Design notes:
    - Priority queue: urgent commands (urgency > 0.8) preempt others
    - Rate-limiting: max 1 narration per 5 seconds
    - Auto-expire: discard commands older than max_age_s
    - Win probability periodic announcement every 60s
    - TTS backend abstracted (pyttsx3 for offline, edge-tts for quality)
    - Non-blocking: TTS runs in background thread
"""

from __future__ import annotations

import heapq
import logging
import os
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from cyber.component.timer_component import ComponentConfig, TimerComponent
from cyber.node.node import CyberNode, Reader, Writer
from cyber.logger.cyber_logger import get_logger
from modules.common.status.error_code import ErrorCode, Status, StatusMessage
from modules.common.adapters.game_messages import (
    VoiceCommand,
    WinPrediction,
    TeamSide,
)

logger = get_logger("voice")

# ─── Constants ───────────────────────────────────────────────────────────────

_VOICE_INTERVAL_MS = 1000.0       # 1Hz check cycle
_MIN_NARRATION_GAP_S = 5.0        # min seconds between narrations
_WIN_PROB_ANNOUNCE_INTERVAL_S = 60.0  # announce win prob every 60s
_MAX_QUEUE_SIZE = 20
_WIN_PROB_SIGNIFICANT_CHANGE = 0.1  # announce if prob changes by >10%


# ─── TTS Backend (abstracted) ───────────────────────────────────────────────

class TTSBackend:
    """Abstract TTS backend.

    Default implementation uses OS-native TTS (say on macOS, espeak on Linux).
    Can be replaced with pyttsx3, edge-tts, or cloud TTS.
    """

    def __init__(self) -> None:
        self._speaking = False
        self._speak_thread: Optional[threading.Thread] = None
        self._total_narrations: int = 0
        self._platform = self._detect_platform()

    def _detect_platform(self) -> str:
        import platform
        system = platform.system()
        if system == "Darwin":
            return "macos"
        elif system == "Windows":
            return "windows"
        return "linux"

    def speak(self, text: str) -> None:
        """Speak text asynchronously in a background thread.

        Args:
            text: Text to narrate.
        """
        if self._speaking:
            logger.debug("TTS busy, queueing: %s", text[:50])
            return

        self._speak_thread = threading.Thread(
            target=self._speak_sync,
            args=(text,),
            daemon=True,
            name="tts-speak",
        )
        self._speak_thread.start()

    def _speak_sync(self, text: str) -> None:
        """Synchronous TTS execution (runs in background thread)."""
        self._speaking = True
        self._total_narrations += 1

        try:
            if self._platform == "macos":
                subprocess.run(
                    ["say", "-r", "180", text],
                    timeout=30,
                    capture_output=True,
                )
            elif self._platform == "windows":
                # Use PowerShell SAPI
                ps_cmd = (
                    f"Add-Type -AssemblyName System.Speech; "
                    f"$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                    f"$s.Rate = 2; $s.Speak('{text}')"
                )
                subprocess.run(
                    ["powershell", "-Command", ps_cmd],
                    timeout=30,
                    capture_output=True,
                )
            else:
                # Linux: espeak or pico2wave
                try:
                    subprocess.run(
                        ["espeak", "-s", "160", text],
                        timeout=30,
                        capture_output=True,
                    )
                except FileNotFoundError:
                    logger.debug("TTS not available (no espeak): %s", text[:50])

        except subprocess.TimeoutExpired:
            logger.warning("TTS timed out for: %s", text[:50])
        except Exception as exc:
            logger.warning("TTS error: %s: %s", type(exc).__name__, exc)
        finally:
            self._speaking = False

    @property
    def is_speaking(self) -> bool:
        return self._speaking

    @property
    def narration_count(self) -> int:
        return self._total_narrations


# ─── Priority Queue Item ────────────────────────────────────────────────────

@dataclass(order=True)
class _QueueItem:
    """Priority queue item for voice commands.

    Lower priority value = higher importance.
    """
    priority: int
    timestamp: float = field(compare=False)
    text: str = field(compare=False)
    max_age_s: float = field(compare=False, default=5.0)
    source: str = field(compare=False, default="")

    @property
    def is_expired(self) -> bool:
        return (time.time() - self.timestamp) > self.max_age_s


# ─── VoiceNarratorComponent ─────────────────────────────────────────────────

class VoiceNarratorComponent(TimerComponent):
    """Voice narration component: speaks strategic advice.

    Each Proc() cycle:
    1. Drain new VoiceCommands from /lol/voice_command
    2. Add to priority queue
    3. Check rate-limit timer
    4. Speak highest-priority non-expired command
    5. Periodically announce win probability

    Apollo equivalent: audio_component + control output
    """

    def __init__(self, tts_enabled: bool = True) -> None:
        super().__init__(
            config=ComponentConfig(
                name="voice_narrator",
                interval_ms=_VOICE_INTERVAL_MS,
                warn_threshold_ms=_VOICE_INTERVAL_MS * 2,
            ),
        )
        self._tts_enabled = tts_enabled
        self._node: Optional[CyberNode] = None
        self._voice_reader: Optional[Reader[VoiceCommand]] = None
        self._win_pred_reader: Optional[Reader[WinPrediction]] = None
        self._status_writer: Optional[Writer[StatusMessage]] = None

        self._tts: Optional[TTSBackend] = None
        self._queue: List[_QueueItem] = []
        self._last_speak_time: float = 0.0
        self._last_win_announce_time: float = 0.0
        self._last_announced_win_prob: float = 0.5
        self._narration_count: int = 0
        self._skipped_count: int = 0

        # Recent narrations for dedup
        self._recent_texts: List[Tuple[float, str]] = []

    def Init(self) -> bool:
        logger.info("Initializing VoiceNarratorComponent (enabled=%s)...",
                     self._tts_enabled)

        self._node = CyberNode("voice_narrator")

        self._voice_reader = self._node.CreateReader(
            "/lol/voice_command", VoiceCommand, pending_queue_size=32,
        )
        self._win_pred_reader = self._node.CreateReader(
            "/lol/win_prediction", WinPrediction, pending_queue_size=4,
        )
        self._status_writer = self._node.CreateWriter(
            "/lol/voice_status", StatusMessage,
        )

        if self._tts_enabled:
            self._tts = TTSBackend()

        logger.info("VoiceNarratorComponent initialized")
        return True

    def Proc(self) -> bool:
        """One voice narration cycle."""
        now = time.time()

        # ── Drain incoming voice commands ────────────────────────────
        commands = self._voice_reader.drain()
        for cmd in commands:
            if not cmd.is_expired:
                item = _QueueItem(
                    priority=cmd.priority,
                    timestamp=cmd.timestamp,
                    text=cmd.text,
                    max_age_s=cmd.max_age_s,
                    source=cmd.source_module,
                )
                heapq.heappush(self._queue, item)

        # Trim queue
        while len(self._queue) > _MAX_QUEUE_SIZE:
            heapq.heappop(self._queue)  # drop lowest priority

        # ── Check for win prob announcement ──────────────────────────
        self._check_win_prob_announcement(now)

        # ── Rate-limit check ─────────────────────────────────────────
        if now - self._last_speak_time < _MIN_NARRATION_GAP_S:
            return True

        if self._tts and self._tts.is_speaking:
            return True

        # ── Pop and speak highest-priority non-expired command ────────
        while self._queue:
            item = heapq.heappop(self._queue)
            if item.is_expired:
                self._skipped_count += 1
                continue

            # Dedup: don't repeat same text within 30s
            if self._is_duplicate(item.text, now):
                self._skipped_count += 1
                continue

            # Speak it
            self._speak(item.text, now)
            break

        return True

    def _check_win_prob_announcement(self, now: float) -> None:
        """Periodically announce win probability changes."""
        if now - self._last_win_announce_time < _WIN_PROB_ANNOUNCE_INTERVAL_S:
            return

        self._win_pred_reader.Observe()
        win_pred = self._win_pred_reader.GetLatestObserved()
        if win_pred is None:
            return

        # Determine probability from active player perspective
        prob = win_pred.blue_win_prob  # Adjusted in voice text below
        change = abs(prob - self._last_announced_win_prob)

        if change >= _WIN_PROB_SIGNIFICANT_CHANGE or (
            now - self._last_win_announce_time > _WIN_PROB_ANNOUNCE_INTERVAL_S * 2
        ):
            winner = win_pred.predicted_winner
            pct = max(prob, 1 - prob) * 100
            text = f"Win probability: {winner.name} team {pct:.0f} percent"
            self._queue_high_priority(text, now)
            self._last_announced_win_prob = prob
            self._last_win_announce_time = now

    def _queue_high_priority(self, text: str, now: float) -> None:
        """Add a high-priority narration to the queue."""
        item = _QueueItem(
            priority=3,
            timestamp=now,
            text=text,
            max_age_s=15.0,
            source="voice_narrator",
        )
        heapq.heappush(self._queue, item)

    def _speak(self, text: str, now: float) -> None:
        """Execute TTS for the given text."""
        self._last_speak_time = now
        self._narration_count += 1
        self._recent_texts.append((now, text))

        # Keep recent texts list bounded
        cutoff = now - 60.0
        self._recent_texts = [
            (t, tx) for t, tx in self._recent_texts if t > cutoff
        ]

        logger.info("Speaking: %s", text)
        if self._tts:
            self._tts.speak(text)

    def _is_duplicate(self, text: str, now: float) -> bool:
        """Check if this text was spoken recently (within 30s)."""
        cutoff = now - 30.0
        for t, tx in self._recent_texts:
            if t > cutoff and tx == text:
                return True
        return False

    def on_shutdown(self) -> None:
        if self._node:
            self._node.shutdown()

    def voice_status(self) -> Dict[str, Any]:
        base = self.status()
        base.update({
            "narration_count": self._narration_count,
            "skipped_count": self._skipped_count,
            "queue_size": len(self._queue),
            "tts_enabled": self._tts_enabled,
            "tts_speaking": self._tts.is_speaking if self._tts else False,
        })
        return base
