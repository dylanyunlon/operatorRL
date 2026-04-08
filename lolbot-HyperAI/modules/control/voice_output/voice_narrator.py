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


# ═══════════════════════════════════════════════════════════════════════════════
# Claude22 V3: TTS engine abstraction + edge-tts async + SSML support
# ═══════════════════════════════════════════════════════════════════════════════
#
# Design spec (Apollo pattern):
#   从 VoiceNarratorComponent 的 Init()/Proc() 语音循环 这个好例子开始。
#   然后，遵循该模式实现 TTSEngineRegistry，让 语音系统 可以 在多个 TTS 引擎
#   之间切换（OS native / edge-tts / pyttsx3），并能 自动降级。
#   接着 SSMLBuilder 引入 语音标记语言支持，使 语音 能够 控制语速/停顿/强调，
#   同时 VoiceQueue 优化 优先级队列以支持插队和取消。

import io
from typing import Protocol as TypingProtocol, runtime_checkable


# ─── TTS engine interface ────────────────────────────────────────────────────

@runtime_checkable
class TTSEngine(TypingProtocol):
    """Protocol for pluggable TTS engines.

    Each engine must implement speak() and is_available().
    The registry tries engines in priority order and uses the
    first available one.
    """

    def speak(self, text: str, rate: int = 180) -> bool:
        """Speak text synchronously. Returns True on success."""
        ...

    def speak_ssml(self, ssml: str) -> bool:
        """Speak SSML-formatted text. Returns True on success."""
        ...

    def is_available(self) -> bool:
        """Check if this engine is usable on the current platform."""
        ...

    @property
    def engine_name(self) -> str:
        ...


# ─── OS-native TTS engine (wraps existing TTSBackend) ────────────────────────

class NativeTTSEngine:
    """OS-native TTS engine (say/espeak/PowerShell).

    Wraps the existing TTSBackend implementation for the registry.
    """

    def __init__(self) -> None:
        self._backend = TTSBackend()

    def speak(self, text: str, rate: int = 180) -> bool:
        try:
            self._backend._speak_sync(text)
            return True
        except Exception:
            return False

    def speak_ssml(self, ssml: str) -> bool:
        # Native engines don't support SSML; strip tags
        import re
        plain = re.sub(r'<[^>]+>', '', ssml)
        return self.speak(plain)

    def is_available(self) -> bool:
        import shutil
        return bool(
            shutil.which("say") or
            shutil.which("espeak") or
            shutil.which("powershell")
        )

    @property
    def engine_name(self) -> str:
        return "native"


# ─── Pyttsx3 TTS engine ─────────────────────────────────────────────────────

class Pyttsx3Engine:
    """pyttsx3-based TTS engine for cross-platform offline TTS."""

    def __init__(self) -> None:
        self._engine = None

    def _get_engine(self):
        if self._engine is None:
            try:
                import pyttsx3
                self._engine = pyttsx3.init()
                self._engine.setProperty("rate", 180)
            except Exception:
                pass
        return self._engine

    def speak(self, text: str, rate: int = 180) -> bool:
        eng = self._get_engine()
        if eng is None:
            return False
        try:
            eng.setProperty("rate", rate)
            eng.say(text)
            eng.runAndWait()
            return True
        except Exception:
            return False

    def speak_ssml(self, ssml: str) -> bool:
        import re
        plain = re.sub(r'<[^>]+>', '', ssml)
        return self.speak(plain)

    def is_available(self) -> bool:
        try:
            import pyttsx3
            return True
        except ImportError:
            return False

    @property
    def engine_name(self) -> str:
        return "pyttsx3"


# ─── Edge-TTS engine (Microsoft) ─────────────────────────────────────────────

class EdgeTTSEngine:
    """Edge-TTS engine for high-quality cloud TTS.

    Uses Microsoft Edge's TTS API via the edge-tts library.
    Supports SSML, multiple voices, and high quality output.
    """

    def __init__(
        self,
        voice: str = "en-US-AriaNeural",
        zh_voice: str = "zh-CN-XiaoxiaoNeural",
    ) -> None:
        self._voice = voice
        self._zh_voice = zh_voice

    def speak(self, text: str, rate: int = 180) -> bool:
        try:
            import edge_tts
            # Detect language (simple heuristic)
            voice = self._zh_voice if _contains_chinese(text) else self._voice
            rate_str = f"+{rate - 180}%" if rate > 180 else f"{rate - 180}%"

            communicate = edge_tts.Communicate(text, voice, rate=rate_str)
            # edge-tts is async; run in event loop
            import asyncio
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    # Can't await in running loop; skip
                    return False
            except RuntimeError:
                pass

            asyncio.run(self._speak_async(communicate))
            return True
        except Exception:
            return False

    async def _speak_async(self, communicate) -> None:
        """Async speak implementation for edge-tts."""
        audio_data = b""
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_data += chunk["data"]
        # In production, would play audio_data via sounddevice/pyaudio
        # For now, save to temp file and play
        if audio_data:
            import tempfile
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
                f.write(audio_data)
                tmp_path = f.name
            try:
                subprocess.run(
                    ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet",
                     tmp_path],
                    timeout=30, capture_output=True,
                )
            except (FileNotFoundError, subprocess.TimeoutExpired):
                pass
            finally:
                os.unlink(tmp_path)

    def speak_ssml(self, ssml: str) -> bool:
        # edge-tts supports SSML natively
        return self.speak(ssml)

    def is_available(self) -> bool:
        try:
            import edge_tts
            return True
        except ImportError:
            return False

    @property
    def engine_name(self) -> str:
        return "edge_tts"


def _contains_chinese(text: str) -> bool:
    """Detect if text contains Chinese characters."""
    for ch in text:
        if '\u4e00' <= ch <= '\u9fff':
            return True
    return False


# ─── TTS engine registry ────────────────────────────────────────────────────

class TTSEngineRegistry:
    """Registry for TTS engines with automatic fallback.

    Tries engines in priority order (edge-tts → pyttsx3 → native).
    Automatically falls back to the next engine on failure.

    Apollo parallel: drivers/canbus/can_client/ — multiple CAN client
    implementations with factory selection.

    Usage::
        registry = TTSEngineRegistry()
        registry.register(EdgeTTSEngine(), priority=0)
        registry.register(Pyttsx3Engine(), priority=1)
        registry.register(NativeTTSEngine(), priority=2)

        registry.speak("Hello world")  # tries edge-tts first
    """

    def __init__(self) -> None:
        self._engines: List[Tuple[int, Any]] = []  # (priority, engine)
        self._active_engine: Optional[Any] = None
        self._speak_count: int = 0
        self._fallback_count: int = 0

    def register(self, engine: Any, priority: int = 10) -> None:
        """Register a TTS engine with priority (lower = preferred)."""
        self._engines.append((priority, engine))
        self._engines.sort(key=lambda x: x[0])

    def auto_detect(self) -> Optional[str]:
        """Detect the best available engine.

        Returns the engine name, or None if no engine is available.
        """
        for _, engine in self._engines:
            if engine.is_available():
                self._active_engine = engine
                return engine.engine_name
        return None

    def speak(self, text: str, rate: int = 180) -> bool:
        """Speak text using the best available engine."""
        self._speak_count += 1

        # Try active engine first
        if self._active_engine:
            try:
                if self._active_engine.speak(text, rate=rate):
                    return True
            except Exception:
                pass

        # Fallback through registry
        for _, engine in self._engines:
            if engine is self._active_engine:
                continue
            if not engine.is_available():
                continue
            try:
                if engine.speak(text, rate=rate):
                    self._active_engine = engine
                    self._fallback_count += 1
                    return True
            except Exception:
                continue

        return False

    def speak_ssml(self, ssml: str) -> bool:
        """Speak SSML-formatted text."""
        if self._active_engine:
            try:
                return self._active_engine.speak_ssml(ssml)
            except Exception:
                pass
        return False

    @property
    def active_engine_name(self) -> str:
        if self._active_engine:
            return self._active_engine.engine_name
        return "none"

    def stats(self) -> Dict[str, Any]:
        return {
            "active_engine": self.active_engine_name,
            "registered_engines": len(self._engines),
            "available_engines": sum(
                1 for _, e in self._engines if e.is_available()),
            "speak_count": self._speak_count,
            "fallback_count": self._fallback_count,
        }


# ─── SSML builder ────────────────────────────────────────────────────────────

class SSMLBuilder:
    """Build SSML markup for TTS engines that support it.

    Provides fluent API for adding speech effects like pauses,
    emphasis, speed changes, and prosody modifications.

    Usage::
        ssml = (SSMLBuilder()
            .text("Dragon secured!")
            .pause(500)
            .emphasis("Great play!", level="strong")
            .rate("Win probability now at 65%", rate="slow")
            .build()
        )
    """

    def __init__(self, lang: str = "en-US") -> None:
        self._parts: List[str] = []
        self._lang = lang

    def text(self, content: str) -> "SSMLBuilder":
        self._parts.append(content)
        return self

    def pause(self, ms: int = 500) -> "SSMLBuilder":
        self._parts.append(f'<break time="{ms}ms"/>')
        return self

    def emphasis(
        self, content: str, level: str = "moderate"
    ) -> "SSMLBuilder":
        self._parts.append(f'<emphasis level="{level}">{content}</emphasis>')
        return self

    def rate(
        self, content: str, rate: str = "medium"
    ) -> "SSMLBuilder":
        self._parts.append(
            f'<prosody rate="{rate}">{content}</prosody>')
        return self

    def pitch(
        self, content: str, pitch: str = "medium"
    ) -> "SSMLBuilder":
        self._parts.append(
            f'<prosody pitch="{pitch}">{content}</prosody>')
        return self

    def volume(
        self, content: str, volume: str = "medium"
    ) -> "SSMLBuilder":
        self._parts.append(
            f'<prosody volume="{volume}">{content}</prosody>')
        return self

    def build(self) -> str:
        body = " ".join(self._parts)
        return (
            f'<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" '
            f'xml:lang="{self._lang}">{body}</speak>'
        )

    def reset(self) -> "SSMLBuilder":
        self._parts.clear()
        return self
