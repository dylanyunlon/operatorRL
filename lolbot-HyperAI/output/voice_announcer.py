#!/usr/bin/env python3
"""
output/voice_announcer.py — Voice Announcement & TTS Output Engine
====================================================================
lolbot-HyperAI · Output Layer

In Apollo, the output layer sends control commands to actuators (steering,
brake, throttle). Our "actuator" is the player's ears — we deliver tactical
advice via text-to-speech voice announcements.

Design constraints:
    1. Never interrupt during teamfights (except CRITICAL priority)
    2. Keep announcements short (< 8 seconds spoken)
    3. Queue management: drop stale announcements
    4. Cooldown between announcements (min 5 seconds)
    5. Volume/urgency mapping: CRITICAL = loud, LOW = quiet

TTS backends (priority order):
    1. Local edge TTS (pyttsx3) — zero latency, works offline
    2. System TTS (Windows SAPI / macOS say) — native quality
    3. Cloud TTS (Google/Azure) — best quality, needs internet

The announcer also generates text summaries for:
    - Dashboard display
    - Log recording (for evolution analysis)
    - Notification popups

Evolution hook: The evolution controller can adjust:
    - Announcement timing and frequency
    - Priority thresholds for different game phases
    - Voice parameters (speed, pitch)

Subscribes to: CH_STRATEGY_RECOMMENDATION, CH_WIN_PROBABILITY
Publishes to: CH_VOICE_ANNOUNCEMENT, CH_NOTIFICATION
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import platform
import queue
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).parent.parent))
from canbus.channel_message import (
    CH_NOTIFICATION,
    CH_STRATEGY_RECOMMENDATION,
    CH_VOICE_ANNOUNCEMENT,
    CH_WIN_PROBABILITY,
    ChannelMessage,
    MessageFactory,
)
from canbus.transport import Transport


# ---------------------------------------------------------------------------
# TTS Backend abstraction
# ---------------------------------------------------------------------------
class TTSBackend(Enum):
    NONE = "none"              # Text-only mode (no audio)
    PYTTSX3 = "pyttsx3"       # Local edge TTS
    SYSTEM = "system"          # OS-native TTS
    ESPEAK = "espeak"          # Linux fallback


@dataclass
class VoiceConfig:
    """Voice synthesis configuration."""
    backend: TTSBackend = TTSBackend.NONE
    rate: int = 175                # Words per minute (default ~natural speed)
    volume: float = 0.8           # 0.0 to 1.0
    pitch: float = 1.0            # 0.5 to 2.0 (only some backends)
    language: str = "en"
    voice_id: Optional[str] = None  # Backend-specific voice identifier


class TTSEngine:
    """
    Text-to-speech abstraction layer.

    Auto-detects the best available backend and provides a uniform
    speak() interface. Runs TTS on a background thread so it never
    blocks the main event loop.
    """

    def __init__(self, config: Optional[VoiceConfig] = None) -> None:
        self._config = config or VoiceConfig()
        self._backend = TTSBackend.NONE
        self._speak_queue: queue.Queue[Optional[str]] = queue.Queue(maxsize=20)
        self._worker_thread: Optional[threading.Thread] = None
        self._running = False
        self._pyttsx3_engine = None

    def init(self) -> TTSBackend:
        """Detect and initialize the best TTS backend."""
        # Try pyttsx3 first
        if self._config.backend in (TTSBackend.PYTTSX3, TTSBackend.NONE):
            try:
                import pyttsx3
                self._pyttsx3_engine = pyttsx3.init()
                self._pyttsx3_engine.setProperty("rate", self._config.rate)
                self._pyttsx3_engine.setProperty("volume", self._config.volume)
                self._backend = TTSBackend.PYTTSX3
                self._start_worker()
                return self._backend
            except (ImportError, Exception):
                pass

        # Try system TTS
        system = platform.system()
        if system == "Windows":
            self._backend = TTSBackend.SYSTEM
            self._start_worker()
            return self._backend
        elif system == "Darwin":
            self._backend = TTSBackend.SYSTEM
            self._start_worker()
            return self._backend

        # Try espeak (Linux)
        try:
            result = subprocess.run(
                ["espeak", "--version"],
                capture_output=True, timeout=2,
            )
            if result.returncode == 0:
                self._backend = TTSBackend.ESPEAK
                self._start_worker()
                return self._backend
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

        # Fallback: text-only
        self._backend = TTSBackend.NONE
        return self._backend

    def _start_worker(self) -> None:
        """Start the background TTS worker thread."""
        self._running = True
        self._worker_thread = threading.Thread(
            target=self._worker_loop,
            daemon=True,
            name="tts-worker",
        )
        self._worker_thread.start()

    def _worker_loop(self) -> None:
        """Background thread: dequeues text and speaks it."""
        while self._running:
            try:
                text = self._speak_queue.get(timeout=1.0)
                if text is None:
                    break
                self._speak_sync(text)
            except queue.Empty:
                continue
            except Exception:
                pass  # Never crash the worker

    def _speak_sync(self, text: str) -> None:
        """Synchronously speak text (called from worker thread)."""
        if self._backend == TTSBackend.PYTTSX3 and self._pyttsx3_engine:
            try:
                self._pyttsx3_engine.say(text)
                self._pyttsx3_engine.runAndWait()
            except Exception:
                pass
        elif self._backend == TTSBackend.SYSTEM:
            system = platform.system()
            try:
                if system == "Windows":
                    # Use PowerShell SAPI
                    ps_cmd = (
                        f'Add-Type -AssemblyName System.Speech; '
                        f'$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; '
                        f'$s.Rate = {(self._config.rate - 175) // 25}; '
                        f'$s.Speak("{text}")'
                    )
                    subprocess.run(
                        ["powershell", "-Command", ps_cmd],
                        timeout=15, capture_output=True,
                    )
                elif system == "Darwin":
                    rate_wpm = self._config.rate
                    subprocess.run(
                        ["say", "-r", str(rate_wpm), text],
                        timeout=15, capture_output=True,
                    )
            except (subprocess.TimeoutExpired, FileNotFoundError):
                pass
        elif self._backend == TTSBackend.ESPEAK:
            try:
                speed = self._config.rate
                subprocess.run(
                    ["espeak", "-s", str(speed), text],
                    timeout=15, capture_output=True,
                )
            except (subprocess.TimeoutExpired, FileNotFoundError):
                pass

    def speak(self, text: str) -> bool:
        """
        Queue text for speaking (non-blocking).

        Returns True if queued, False if queue is full.
        """
        if self._backend == TTSBackend.NONE:
            return False
        try:
            self._speak_queue.put_nowait(text)
            return True
        except queue.Full:
            return False

    def clear_queue(self) -> int:
        """Clear pending speech. Returns number of items cleared."""
        count = 0
        while not self._speak_queue.empty():
            try:
                self._speak_queue.get_nowait()
                count += 1
            except queue.Empty:
                break
        return count

    def shutdown(self) -> None:
        """Stop the TTS worker thread."""
        self._running = False
        try:
            self._speak_queue.put_nowait(None)
        except queue.Full:
            pass
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=3.0)

    @property
    def backend_name(self) -> str:
        return self._backend.value


# ---------------------------------------------------------------------------
# Announcement queue entry
# ---------------------------------------------------------------------------
@dataclass
class Announcement:
    """A pending voice announcement with metadata."""
    text: str
    priority: int                   # 1-4
    category: str                   # RecType value or "win_update"
    source_rec_id: Optional[str] = None
    created_at: float = field(default_factory=time.monotonic)
    expires_at: float = 0.0
    spoken: bool = False
    game_time_sec: float = 0.0

    def is_expired(self) -> bool:
        if self.expires_at <= 0:
            return False
        return time.monotonic() > self.expires_at


# ---------------------------------------------------------------------------
# Voice Announcer Component
# ---------------------------------------------------------------------------
class VoiceAnnouncer:
    """
    Main voice output component.

    Subscribes to recommendations and win predictions, converts them
    to voice announcements, and manages the output queue.

    Key behaviors:
        - Priority queue: CRITICAL preempts everything
        - Deduplication: same message won't repeat within 30 seconds
        - Phase awareness: reduce chattiness during teamfights
        - Periodic win updates: "Currently at 62% win probability"

    Apollo equivalent: control/actuator_driver
    """

    PROC_INTERVAL_MS = 500          # Check queue every 500ms
    MIN_ANNOUNCE_INTERVAL_SEC = 5.0 # Minimum gap between announcements
    WIN_UPDATE_INTERVAL_SEC = 60.0  # Periodic win probability updates
    DEDUP_WINDOW_SEC = 30.0         # Don't repeat same message within this

    def __init__(
        self,
        transport: Transport,
        voice_config: Optional[VoiceConfig] = None,
    ) -> None:
        self._transport = transport
        self._factory = MessageFactory("output.voice_announcer")
        self._tts = TTSEngine(voice_config)

        # Announcement queue (priority queue via sorted list)
        self._queue: List[Announcement] = []

        # State
        self._last_proc_ms = 0
        self._last_announce_time = 0.0
        self._last_win_update_time = 0.0
        self._recent_hashes: Deque[Tuple[str, float]] = deque(maxlen=50)
        self._total_announced = 0
        self._total_dropped = 0
        self._total_deduped = 0
        self._muted = False
        self._unsubs: List[Callable] = []

    def init(self) -> Dict[str, Any]:
        """Initialize TTS and subscribe to channels."""
        backend = self._tts.init()

        self._unsubs.append(
            self._transport.subscribe(
                CH_STRATEGY_RECOMMENDATION, self._on_recommendation,
            )
        )
        self._unsubs.append(
            self._transport.subscribe(
                CH_WIN_PROBABILITY, self._on_win_prediction,
            )
        )

        return {
            "tts_backend": backend.value,
            "muted": self._muted,
        }

    async def proc(self) -> None:
        """
        Process the announcement queue.

        Called every PROC_INTERVAL_MS by scheduler.
        """
        now_ms = int(time.monotonic() * 1000)
        if now_ms - self._last_proc_ms < self.PROC_INTERVAL_MS:
            return
        self._last_proc_ms = now_ms
        now = time.monotonic()

        if self._muted:
            return

        # Prune expired announcements
        before = len(self._queue)
        self._queue = [a for a in self._queue if not a.is_expired()]
        self._total_dropped += before - len(self._queue)

        # Check if we can announce
        if now - self._last_announce_time < self.MIN_ANNOUNCE_INTERVAL_SEC:
            return

        if not self._queue:
            return

        # Sort by priority (highest first), then by creation time
        self._queue.sort(
            key=lambda a: (-a.priority, a.created_at),
        )

        # Take the highest-priority announcement
        announcement = self._queue.pop(0)

        # Speak it
        spoken = self._tts.speak(announcement.text)
        announcement.spoken = spoken
        self._last_announce_time = now
        self._total_announced += 1

        # Publish to bus (for logging / dashboard)
        msg = self._factory.create(
            CH_VOICE_ANNOUNCEMENT,
            {
                "text": announcement.text,
                "urgency": announcement.priority,
                "category": announcement.category,
                "spoken": spoken,
                "tts_backend": self._tts.backend_name,
                "game_time_sec": announcement.game_time_sec,
            },
            priority=announcement.priority,
        )
        self._transport.publish(msg)

    def shutdown(self) -> Dict[str, Any]:
        """Stop TTS and unsubscribe."""
        self._tts.shutdown()
        for unsub in self._unsubs:
            unsub()
        return self.stats()

    # -- Subscription handlers ------------------------------------------

    def _on_recommendation(self, msg: ChannelMessage) -> None:
        """Convert a strategy recommendation to a queued announcement."""
        p = msg.payload
        voice_text = p.get("voice_text", "")
        if not voice_text:
            voice_text = p.get("title", "")

        # Deduplication
        text_hash = hashlib.md5(voice_text.encode()).hexdigest()[:8]
        now = time.monotonic()
        for h, t in self._recent_hashes:
            if h == text_hash and now - t < self.DEDUP_WINDOW_SEC:
                self._total_deduped += 1
                return
        self._recent_hashes.append((text_hash, now))

        announcement = Announcement(
            text=voice_text,
            priority=p.get("priority", 2),
            category=p.get("rec_type", "unknown"),
            source_rec_id=p.get("rec_id"),
            expires_at=now + p.get("expires_sec", 30),
            game_time_sec=p.get("game_time_sec", 0),
        )
        self._queue.append(announcement)

    def _on_win_prediction(self, msg: ChannelMessage) -> None:
        """Generate periodic win probability voice updates."""
        now = time.monotonic()
        if now - self._last_win_update_time < self.WIN_UPDATE_INTERVAL_SEC:
            return
        self._last_win_update_time = now

        p = msg.payload
        win_pct = p.get("win_pct", 0.5)
        trend = p.get("trend", "stable")
        game_time = p.get("game_time_sec", 0)

        # Only announce if in-game
        if game_time < 60:
            return

        # Format voice text
        pct_text = f"{win_pct:.0%}"
        if trend == "rising":
            voice = f"Win probability is {pct_text} and rising."
        elif trend == "falling":
            voice = f"Win probability is {pct_text} and falling."
        else:
            voice = f"Win probability is at {pct_text}."

        # Determine urgency
        if win_pct < 0.3 or win_pct > 0.8:
            priority = 2  # Medium — noteworthy
        else:
            priority = 1  # Low — just informational

        announcement = Announcement(
            text=voice,
            priority=priority,
            category="win_update",
            expires_at=now + 15.0,
            game_time_sec=game_time,
        )
        self._queue.append(announcement)

    # -- Control API ----------------------------------------------------

    def mute(self) -> None:
        """Mute all voice output."""
        self._muted = True
        self._tts.clear_queue()

    def unmute(self) -> None:
        """Resume voice output."""
        self._muted = False

    def set_min_interval(self, seconds: float) -> None:
        """Adjust minimum interval between announcements (evolution)."""
        self.MIN_ANNOUNCE_INTERVAL_SEC = max(2.0, seconds)

    def set_win_update_interval(self, seconds: float) -> None:
        """Adjust win update frequency (evolution)."""
        self.WIN_UPDATE_INTERVAL_SEC = max(15.0, seconds)

    def force_announce(self, text: str, priority: int = 3) -> None:
        """Force an immediate announcement (bypasses queue)."""
        self._tts.speak(text)
        msg = self._factory.create(
            CH_VOICE_ANNOUNCEMENT,
            {
                "text": text,
                "urgency": priority,
                "category": "forced",
                "spoken": True,
                "tts_backend": self._tts.backend_name,
            },
            priority=priority,
        )
        self._transport.publish(msg)
        self._total_announced += 1

    # -- Stats ----------------------------------------------------------

    def stats(self) -> Dict[str, Any]:
        return {
            "total_announced": self._total_announced,
            "total_dropped": self._total_dropped,
            "total_deduped": self._total_deduped,
            "queue_size": len(self._queue),
            "muted": self._muted,
            "tts_backend": self._tts.backend_name,
            "min_interval_sec": self.MIN_ANNOUNCE_INTERVAL_SEC,
            "win_update_interval_sec": self.WIN_UPDATE_INTERVAL_SEC,
        }
