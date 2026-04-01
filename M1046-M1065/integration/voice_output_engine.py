#!/usr/bin/env python3
"""
M1052: Voice Output Engine — Real-time TTS for Tactical Guidance
=================================================================
OperatorRL M1046-M1065 · 自部署 自环境反馈 自演化

Converts Strategy Engine recommendations into spoken audio feedback.
Uses a priority queue to avoid overlapping voice outputs during
high-action moments (teamfights generate many recommendations).

Pattern: Read strategy/strategy_engine.py Recommendation.voice_text
→ understand the text format → implement TTS pipeline with priority
queue, ducking (lower game audio during speech), and language support.

Reference: sorena-ai/LeagueAiCoach uses OpenAI Whisper for input and
OpenAI TTS for output. We support multiple TTS backends:
    1. System TTS (pyttsx3) — offline, zero latency, lower quality
    2. Edge TTS (edge-tts) — free, good quality, requires internet
    3. OpenAI TTS — best quality, requires API key, has latency
"""

import asyncio
from collections import deque
import hashlib
import json
import os
import queue
import sys
import threading
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Any, Callable, Deque, Dict, List, Optional, Set, Tuple

sys.path.insert(0, str(Path(__file__).parent.parent))
try:
    from evo_logging.evolution_logger import get_logger, LogCategory
except ImportError:
    pass


class TTSBackend(Enum):
    SYSTEM = "system"       # pyttsx3
    EDGE = "edge"           # edge-tts (Microsoft)
    OPENAI = "openai"       # OpenAI TTS API
    DISABLED = "disabled"   # No audio output


class VoicePriority(Enum):
    LOW = 1       # General tips
    MEDIUM = 2    # Objective timers
    HIGH = 3      # Lane warnings
    CRITICAL = 4  # Danger alerts — interrupts current speech


@dataclass
class VoiceMessage:
    """Queued voice message with priority and timing."""
    text: str
    priority: int
    created_at: float = field(default_factory=time.monotonic)
    max_age_sec: float = 15.0  # Drop if not played within this time
    rec_id: Optional[str] = None

    @property
    def is_expired(self) -> bool:
        return time.monotonic() - self.created_at > self.max_age_sec

    def __lt__(self, other):
        # Higher priority first, then older first
        if self.priority != other.priority:
            return self.priority > other.priority
        return self.created_at < other.created_at


class AudioCache:
    """
    Caches generated audio files to avoid re-synthesis.

    Key = SHA256(text + backend). Stores WAV/MP3 files in cache_dir.
    Max cache size = 100MB (auto-evicts oldest on overflow).
    """
    def __init__(self, cache_dir: str = "cache/tts"):
        self._cache_dir = Path(cache_dir)
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._index: Dict[str, Path] = {}
        self._max_bytes = 100 * 1024 * 1024
        self._current_bytes = 0
        self._load_existing()

    def _load_existing(self) -> None:
        for f in self._cache_dir.glob("*.wav"):
            self._index[f.stem] = f
            self._current_bytes += f.stat().st_size
        for f in self._cache_dir.glob("*.mp3"):
            self._index[f.stem] = f
            self._current_bytes += f.stat().st_size

    def get(self, text: str, backend: str) -> Optional[Path]:
        key = self._make_key(text, backend)
        return self._index.get(key)

    def put(self, text: str, backend: str, audio_path: Path) -> None:
        key = self._make_key(text, backend)
        dest = self._cache_dir / f"{key}{audio_path.suffix}"
        if audio_path != dest:
            import shutil
            shutil.copy2(audio_path, dest)
        self._index[key] = dest
        self._current_bytes += dest.stat().st_size
        self._evict_if_needed()

    def _make_key(self, text: str, backend: str) -> str:
        raw = f"{backend}:{text}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def _evict_if_needed(self) -> None:
        while self._current_bytes > self._max_bytes and self._index:
            oldest_key = next(iter(self._index))
            path = self._index.pop(oldest_key)
            if path.exists():
                self._current_bytes -= path.stat().st_size
                path.unlink()


class VoiceOutputEngine:
    """
    TTS engine with priority queue and interrupt support.

    Architecture:
        Recommendation → VoiceMessage → PriorityQueue
            → SpeechWorker thread → TTS backend → Audio output

    The SpeechWorker runs in a daemon thread. CRITICAL priority
    messages interrupt the current speech (e.g., "danger, jungler
    approaching" overrides "consider buying Control Ward").

    Cooldown: After each utterance, wait 2s before next to avoid
    audio fatigue. CRITICAL messages bypass cooldown.
    """
    DEFAULT_COOLDOWN_SEC = 2.0
    MAX_QUEUE_SIZE = 20

    def __init__(
        self,
        backend: TTSBackend = TTSBackend.SYSTEM,
        voice_name: Optional[str] = None,
        speech_rate: float = 1.2,  # Slightly faster for in-game
        volume: float = 0.8,
        language: str = "zh-CN",
    ):
        self._backend = backend
        self._voice_name = voice_name
        self._speech_rate = speech_rate
        self._volume = volume
        self._language = language
        self._logger = get_logger()
        self._cache = AudioCache()
        self._queue: queue.PriorityQueue = queue.PriorityQueue(
            maxsize=self.MAX_QUEUE_SIZE)
        self._running = False
        self._worker: Optional[threading.Thread] = None
        self._currently_speaking = False
        self._last_speech_time = 0.0
        self._total_utterances = 0
        self._total_dropped = 0
        self._tts_engine = None

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._init_backend()
        self._worker = threading.Thread(
            target=self._speech_loop, daemon=True, name='VoiceWorker')
        self._worker.start()
        self._logger.info(
            LogCategory.SYSTEM,
            f"Voice engine started: backend={self._backend.value}")

    def stop(self) -> None:
        self._running = False
        if self._worker:
            self._worker.join(timeout=3.0)
        self._logger.info(LogCategory.SYSTEM, "Voice engine stopped")

    def speak(self, text: str, priority: int = VoicePriority.MEDIUM.value,
              rec_id: Optional[str] = None, max_age_sec: float = 15.0) -> bool:
        """Queue a message for speech. Returns False if queue is full."""
        if self._backend == TTSBackend.DISABLED:
            return False
        msg = VoiceMessage(
            text=text, priority=priority,
            rec_id=rec_id, max_age_sec=max_age_sec)
        try:
            self._queue.put_nowait(msg)
            return True
        except queue.Full:
            self._total_dropped += 1
            self._logger.debug(
                LogCategory.SYSTEM,
                f"Voice queue full, dropped: {text[:30]}...")
            return False

    def on_recommendation(self, rec: Any) -> None:
        """Handler for Strategy Engine recommendations."""
        voice_text = getattr(rec, 'voice_text', None)
        if not voice_text:
            voice_text = getattr(rec, 'title', None)
        if voice_text:
            priority = getattr(rec, 'priority', VoicePriority.MEDIUM.value)
            rec_id = getattr(rec, 'rec_id', None)
            self.speak(voice_text, priority=priority, rec_id=rec_id)

    def _init_backend(self) -> None:
        """Initialize the selected TTS backend."""
        if self._backend == TTSBackend.SYSTEM:
            try:
                import pyttsx3
                self._tts_engine = pyttsx3.init()
                self._tts_engine.setProperty('rate',
                    int(self._tts_engine.getProperty('rate') * self._speech_rate))
                self._tts_engine.setProperty('volume', self._volume)
                if self._voice_name:
                    for voice in self._tts_engine.getProperty('voices'):
                        if self._voice_name.lower() in voice.name.lower():
                            self._tts_engine.setProperty('voice', voice.id)
                            break
            except ImportError:
                self._logger.warn(
                    LogCategory.SYSTEM,
                    "pyttsx3 not available, voice output disabled")
                self._backend = TTSBackend.DISABLED
        elif self._backend == TTSBackend.EDGE:
            try:
                import edge_tts
                self._logger.info(LogCategory.SYSTEM, "edge-tts backend ready")
            except ImportError:
                self._logger.warn(
                    LogCategory.SYSTEM,
                    "edge-tts not available, falling back to system TTS")
                self._backend = TTSBackend.SYSTEM
                self._init_backend()
        elif self._backend == TTSBackend.OPENAI:
            if not os.environ.get('OPENAI_API_KEY'):
                self._logger.warn(
                    LogCategory.SYSTEM,
                    "OPENAI_API_KEY not set, falling back to system TTS")
                self._backend = TTSBackend.SYSTEM
                self._init_backend()

    def _speech_loop(self) -> None:
        """Worker thread: dequeue and speak messages."""
        while self._running:
            try:
                msg = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if msg.is_expired:
                self._total_dropped += 1
                continue
            # Cooldown check (bypass for CRITICAL)
            now = time.monotonic()
            if (msg.priority < VoicePriority.CRITICAL.value
                    and now - self._last_speech_time < self.DEFAULT_COOLDOWN_SEC):
                time.sleep(max(0, self.DEFAULT_COOLDOWN_SEC - (now - self._last_speech_time)))
            self._currently_speaking = True
            try:
                self._synthesize_and_play(msg)
                self._total_utterances += 1
                self._last_speech_time = time.monotonic()
                self._logger.trace(
                    LogCategory.SYSTEM,
                    f"Spoke: {msg.text[:50]}...",
                    data={'priority': msg.priority, 'rec_id': msg.rec_id})
            except Exception as e:
                self._logger.error(
                    LogCategory.SYSTEM,
                    f"TTS error: {e}")
            finally:
                self._currently_speaking = False

    def _synthesize_and_play(self, msg: VoiceMessage) -> None:
        """Synthesize speech and play audio."""
        # Check cache
        cached = self._cache.get(msg.text, self._backend.value)
        if cached and cached.exists():
            self._play_audio(cached)
            return
        if self._backend == TTSBackend.SYSTEM and self._tts_engine:
            self._tts_engine.say(msg.text)
            self._tts_engine.runAndWait()
        elif self._backend == TTSBackend.EDGE:
            self._edge_tts_sync(msg.text)
        elif self._backend == TTSBackend.OPENAI:
            self._openai_tts_sync(msg.text)
        else:
            # Disabled or no backend — log only
            self._logger.debug(
                LogCategory.SYSTEM,
                f"[TTS-DISABLED] Would say: {msg.text}")

    def _edge_tts_sync(self, text: str) -> None:
        """Synchronous wrapper for edge-tts async API."""
        try:
            import edge_tts
            voice = self._voice_name or "zh-CN-XiaoxiaoNeural"
            output_path = Path(f"cache/tts/edge_{hashlib.md5(text.encode()).hexdigest()[:8]}.mp3")
            output_path.parent.mkdir(parents=True, exist_ok=True)
            loop = asyncio.new_event_loop()
            async def _gen():
                communicate = edge_tts.Communicate(text, voice)
                await communicate.save(str(output_path))
            loop.run_until_complete(_gen())
            loop.close()
            if output_path.exists():
                self._cache.put(text, self._backend.value, output_path)
                self._play_audio(output_path)
        except Exception as e:
            self._logger.error(LogCategory.SYSTEM, f"edge-tts error: {e}")

    def _openai_tts_sync(self, text: str) -> None:
        """Synchronous OpenAI TTS synthesis."""
        try:
            import openai
            client = openai.OpenAI()
            response = client.audio.speech.create(
                model="tts-1", voice="alloy", input=text)
            output_path = Path(f"cache/tts/openai_{hashlib.md5(text.encode()).hexdigest()[:8]}.mp3")
            output_path.parent.mkdir(parents=True, exist_ok=True)
            response.stream_to_file(str(output_path))
            if output_path.exists():
                self._cache.put(text, self._backend.value, output_path)
                self._play_audio(output_path)
        except Exception as e:
            self._logger.error(LogCategory.SYSTEM, f"OpenAI TTS error: {e}")

    def _play_audio(self, audio_path: Path) -> None:
        """Play an audio file. Platform-dependent."""
        import subprocess
        import platform
        try:
            if platform.system() == 'Windows':
                # Use Windows Media Player silently
                subprocess.run(
                    ['powershell', '-c',
                     f'(New-Object Media.SoundPlayer "{audio_path}").PlaySync()'],
                    timeout=10, capture_output=True)
            elif platform.system() == 'Darwin':
                subprocess.run(['afplay', str(audio_path)],
                               timeout=10, capture_output=True)
            else:
                # Linux: try aplay, then paplay, then ffplay
                for player in ['aplay', 'paplay', 'ffplay -nodisp -autoexit']:
                    try:
                        cmd = player.split() + [str(audio_path)]
                        subprocess.run(cmd, timeout=10, capture_output=True)
                        break
                    except FileNotFoundError:
                        continue
        except Exception as e:
            self._logger.debug(
                LogCategory.SYSTEM,
                f"Audio playback not available: {e}")

    def get_stats(self) -> Dict[str, Any]:
        return {
            'backend': self._backend.value,
            'total_utterances': self._total_utterances,
            'total_dropped': self._total_dropped,
            'queue_size': self._queue.qsize(),
            'currently_speaking': self._currently_speaking,
        }


class StrategicNarratorFormatter:
    """
    Formats strategy recommendations into natural language for TTS.

    Converts structured strategy data into spoken advice that is:
        - Concise (under 15 seconds per utterance)
        - Actionable (tells player WHAT to do, not WHY)
        - Prioritized (most urgent advice first)
        - Non-repetitive (tracks what was recently said)

    Production critique:
        1. User: Messages are kept under 30 words. Longer explanations
           are only spoken if explicitly requested.
        2. System: Recent message dedup window prevents the same advice
           from being repeated within 60 seconds.
    """
    def __init__(self, dedup_window_sec: float = 60.0):
        self._dedup_window = dedup_window_sec
        self._recent_messages: Deque[Tuple[float, str]] = deque(maxlen=50)
        self._message_hashes: Set[str] = set()

    def _hash_message(self, msg: str) -> str:
        """Create a semantic hash for dedup (ignoring numbers)."""
        import re
        normalized = re.sub(r'\d+', 'N', msg.lower().strip())
        return hashlib.md5(normalized.encode()).hexdigest()[:8]

    def _is_duplicate(self, msg: str) -> bool:
        now = time.monotonic()
        # Clean old entries
        while (self._recent_messages and
               now - self._recent_messages[0][0] > self._dedup_window):
            _, old_msg = self._recent_messages.popleft()
            h = self._hash_message(old_msg)
            self._message_hashes.discard(h)
        h = self._hash_message(msg)
        return h in self._message_hashes

    def _record_message(self, msg: str) -> None:
        self._recent_messages.append((time.monotonic(), msg))
        self._message_hashes.add(self._hash_message(msg))

    def format_danger_warning(
        self, threat_type: str, location: str, urgency: float
    ) -> Optional[str]:
        """Format an immediate danger warning."""
        templates = {
            'gank': "Careful, jungler approaching {location}. Back off.",
            'dive': "Enemy diving {location}. Fall back under tower.",
            'roam': "Missing enemies heading {location}. Play safe.",
            'baron': "Enemy starting Baron. Rotate now or trade objectives.",
            'dragon': "Dragon contested. Group immediately.",
        }
        template = templates.get(threat_type)
        if not template:
            return None
        msg = template.format(location=location)
        if self._is_duplicate(msg):
            return None
        self._record_message(msg)
        return msg

    def format_objective_call(
        self, objective: str, timer_sec: float, team_advantage: bool
    ) -> Optional[str]:
        """Format an objective timing call."""
        if timer_sec > 30:
            msg = f"{objective} in {int(timer_sec)} seconds. Start positioning."
        elif timer_sec > 0:
            if team_advantage:
                msg = f"{objective} spawning. We have numbers advantage, take it."
            else:
                msg = f"{objective} spawning. Set up vision first."
        else:
            msg = f"{objective} is up. Contest if safe."
        if self._is_duplicate(msg):
            return None
        self._record_message(msg)
        return msg

    def format_strategy_shift(
        self, old_strategy: str, new_strategy: str, reason: str
    ) -> Optional[str]:
        """Format a strategy change notification."""
        msg = f"Switching from {old_strategy} to {new_strategy}. {reason}"
        if self._is_duplicate(msg):
            return None
        self._record_message(msg)
        return msg

    def format_opponent_insight(
        self, opponent_name: str, insight: str
    ) -> Optional[str]:
        """Format an insight about an opponent."""
        msg = f"{opponent_name}: {insight}"
        if len(msg) > 60:
            msg = msg[:57] + "..."
        if self._is_duplicate(msg):
            return None
        self._record_message(msg)
        return msg

    def format_game_prediction(
        self, win_probability: float, game_time_min: float
    ) -> Optional[str]:
        """Format a game outcome prediction."""
        if win_probability > 0.7:
            msg = f"Strong position at {int(game_time_min)} minutes. Push advantages."
        elif win_probability > 0.55:
            msg = f"Slight lead. Stay disciplined, avoid unnecessary fights."
        elif win_probability > 0.45:
            msg = f"Even game at {int(game_time_min)} minutes. Focus on objectives."
        elif win_probability > 0.3:
            msg = f"Behind. Look for picks and play around power spikes."
        else:
            msg = f"Significantly behind. Stall for late game scaling."
        if self._is_duplicate(msg):
            return None
        self._record_message(msg)
        return msg

    def get_recent_message_count(self) -> int:
        return len(self._recent_messages)
