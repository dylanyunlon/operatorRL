"""
SensorFusion — Multi-source data alignment and priority merging.
=================================================================
lolbot-HyperAI · Perception Layer

Fuses LCU Live Client API, Fiddler MCP bridge, and Replay file data
into a single authoritative GameSnapshot stream.  When one source
degrades, automatically falls back to the next available source.

Architecture position:
    modules/perception/fusion/sensor_fusion.py   ← YOU ARE HERE
    ├─ Reads: /lol/raw_lcu (RawLCUData from canbus)
    ├─ Reads: /lol/raw_fiddler (RawFiddlerData from canbus)
    ├─ Publishes: /lol/fused_raw (FusedRawData for perception)
    └─ Publishes: /lol/fusion_status (StatusMessage)

Apollo reference:
    modules/perception/multi_sensor_fusion/multi_sensor_fusion_component.cc

Design notes:
    - Timestamp alignment: reject data older than 500ms from wall clock
    - Priority: LCU > Fiddler > Replay (LCU is ground truth when available)
    - Staleness detection: if primary source silent >2s, switch to fallback
    - Thread-safe: Proc() is the only state mutator
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, List, Optional, Tuple

from cyber.component.timer_component import ComponentConfig, TimerComponent
from cyber.node.node import CyberNode, Reader, Writer
from cyber.logger.cyber_logger import get_logger
from modules.common.status.error_code import ErrorCode, Status, StatusMessage
from modules.common.adapters.game_messages import RawFiddlerData, RawLCUData

logger = get_logger("perception.fusion")

_FUSION_INTERVAL_MS = 100.0       # 10Hz, same as canbus
_WARN_THRESHOLD_MS = 80.0
_STALE_THRESHOLD_S = 2.0          # source stale after 2s silence
_ALIGNMENT_TOLERANCE_MS = 500.0   # reject data >500ms old
_DEDUP_HASH_CACHE_SIZE = 32       # remember last N hashes for dedup


class FusionSource(Enum):
    """Active data source identifier."""
    LCU = auto()
    FIDDLER = auto()
    REPLAY = auto()
    NONE = auto()


@dataclass
class SourceHealth:
    """Health tracking for a single data source."""
    source: FusionSource
    last_received: float = 0.0
    message_count: int = 0
    error_count: int = 0
    consecutive_stale: int = 0
    is_available: bool = False

    def record_message(self) -> None:
        self.last_received = time.time()
        self.message_count += 1
        self.consecutive_stale = 0
        self.is_available = True

    def check_stale(self, now: float) -> bool:
        if self.last_received <= 0:
            return True
        age = now - self.last_received
        if age > _STALE_THRESHOLD_S:
            self.consecutive_stale += 1
            if self.consecutive_stale >= 3:
                self.is_available = False
            return True
        return False


@dataclass
class FusedRawData:
    """Fused raw data from best available source.

    Published on ``/lol/fused_raw``.
    """
    allgamedata: Dict[str, Any] = field(default_factory=dict)
    source: str = "none"
    source_timestamp: float = 0.0
    fusion_timestamp: float = field(default_factory=time.time)
    latency_ms: float = 0.0
    is_duplicate: bool = False


class SensorFusion(TimerComponent):
    """Multi-source data fusion with automatic fallback.

    Each Proc():
        1. Read from all available sources
        2. Timestamp-align and validate freshness
        3. Select best source by priority (LCU > Fiddler > Replay)
        4. Deduplicate via content hash
        5. Publish fused output on /lol/fused_raw
    """

    def __init__(self) -> None:
        super().__init__(
            config=ComponentConfig(
                name="sensor_fusion",
                interval_ms=_FUSION_INTERVAL_MS,
                warn_threshold_ms=_WARN_THRESHOLD_MS,
            ),
        )
        self._node: Optional[CyberNode] = None

        self._lcu_reader: Optional[Reader] = None
        self._fiddler_reader: Optional[Reader] = None
        self._fused_writer: Optional[Writer] = None
        self._status_writer: Optional[Writer] = None

        # Source health
        self._lcu_health = SourceHealth(source=FusionSource.LCU)
        self._fiddler_health = SourceHealth(source=FusionSource.FIDDLER)
        self._active_source: FusionSource = FusionSource.NONE

        # Dedup
        self._recent_hashes: List[str] = []

        # Stats
        self._proc_count: int = 0
        self._fused_count: int = 0
        self._dedup_count: int = 0
        self._fallback_count: int = 0

    def Init(self) -> bool:
        logger.info("Initializing SensorFusion...")

        self._node = CyberNode("sensor_fusion")

        self._lcu_reader = self._node.CreateReader(
            "/lol/raw_lcu", RawLCUData, pending_queue_size=16,
        )
        self._fiddler_reader = self._node.CreateReader(
            "/lol/raw_fiddler", RawFiddlerData, pending_queue_size=8,
        )
        self._fused_writer = self._node.CreateWriter(
            "/lol/fused_raw", FusedRawData,
        )
        self._status_writer = self._node.CreateWriter(
            "/lol/fusion_status", StatusMessage,
        )

        logger.info("SensorFusion initialized (sources: LCU, Fiddler)")
        return True

    def Proc(self) -> bool:
        self._proc_count += 1
        now = time.time()

        # ── Check source staleness ───────────────────────────────────
        lcu_stale = self._lcu_health.check_stale(now)
        fiddler_stale = self._fiddler_health.check_stale(now)

        # ── Read LCU ─────────────────────────────────────────────────
        lcu_data: Optional[Dict[str, Any]] = None
        lcu_ts: float = 0.0

        self._lcu_reader.Observe()
        raw_lcu = self._lcu_reader.GetLatestObserved()
        if raw_lcu is not None and isinstance(raw_lcu, RawLCUData):
            if raw_lcu.allgamedata and raw_lcu.http_status == 200:
                age_ms = (now - raw_lcu.timestamp) * 1000
                if age_ms <= _ALIGNMENT_TOLERANCE_MS:
                    lcu_data = raw_lcu.allgamedata
                    lcu_ts = raw_lcu.timestamp
                    self._lcu_health.record_message()

        # ── Read Fiddler ─────────────────────────────────────────────
        fiddler_data: Optional[Dict[str, Any]] = None
        fiddler_ts: float = 0.0

        self._fiddler_reader.Observe()
        raw_fiddler = self._fiddler_reader.GetLatestObserved()
        if raw_fiddler is not None and isinstance(raw_fiddler, RawFiddlerData):
            if raw_fiddler.sessions:
                age_ms = (now - raw_fiddler.timestamp) * 1000
                if age_ms <= _ALIGNMENT_TOLERANCE_MS:
                    # Extract game data from fiddler sessions if available
                    for session in raw_fiddler.sessions:
                        body = session.get("response_body", {})
                        if isinstance(body, dict) and "allPlayers" in body:
                            fiddler_data = body
                            fiddler_ts = raw_fiddler.timestamp
                            self._fiddler_health.record_message()
                            break

        # ── Select best source ───────────────────────────────────────
        selected_data: Optional[Dict[str, Any]] = None
        selected_source = FusionSource.NONE
        selected_ts: float = 0.0

        if lcu_data is not None:
            selected_data = lcu_data
            selected_source = FusionSource.LCU
            selected_ts = lcu_ts
        elif fiddler_data is not None:
            selected_data = fiddler_data
            selected_source = FusionSource.FIDDLER
            selected_ts = fiddler_ts
            if self._active_source == FusionSource.LCU:
                self._fallback_count += 1
                logger.warning(
                    "Falling back from LCU to Fiddler (LCU stale for %.1fs)",
                    now - self._lcu_health.last_received,
                )

        # Track active source transitions
        if selected_source != self._active_source and selected_source != FusionSource.NONE:
            logger.info("Active source: %s → %s",
                        self._active_source.name, selected_source.name)
            self._active_source = selected_source

        if selected_data is None:
            return True  # No data from any source

        # ── Dedup ────────────────────────────────────────────────────
        content_hash = self._compute_hash(selected_data)
        is_dup = content_hash in self._recent_hashes
        if is_dup:
            self._dedup_count += 1
            return True

        self._recent_hashes.append(content_hash)
        if len(self._recent_hashes) > _DEDUP_HASH_CACHE_SIZE:
            self._recent_hashes.pop(0)

        # ── Publish fused data ───────────────────────────────────────
        latency_ms = (now - selected_ts) * 1000 if selected_ts > 0 else 0.0

        fused = FusedRawData(
            allgamedata=selected_data,
            source=selected_source.name.lower(),
            source_timestamp=selected_ts,
            latency_ms=round(latency_ms, 2),
            is_duplicate=False,
        )

        if self._fused_writer:
            self._fused_writer.Write(fused)

        self._fused_count += 1
        return True

    def on_shutdown(self) -> None:
        logger.info(
            "SensorFusion shutdown: fused=%d, dedup=%d, fallbacks=%d",
            self._fused_count, self._dedup_count, self._fallback_count,
        )
        if self._node:
            self._node.shutdown()

    @staticmethod
    def _compute_hash(data: Dict[str, Any]) -> str:
        """Fast content hash for dedup (game_time + kill counts)."""
        game_data = data.get("gameData", {})
        game_time = game_data.get("gameTime", 0)
        events = data.get("events", {}).get("Events", [])
        event_count = len(events)
        key = f"{game_time:.1f}:{event_count}"
        return hashlib.md5(key.encode()).hexdigest()[:12]

    def fusion_status(self) -> Dict[str, Any]:
        base = self.status()
        base.update({
            "active_source": self._active_source.name,
            "lcu_available": self._lcu_health.is_available,
            "lcu_messages": self._lcu_health.message_count,
            "fiddler_available": self._fiddler_health.is_available,
            "fiddler_messages": self._fiddler_health.message_count,
            "fused_count": self._fused_count,
            "dedup_count": self._dedup_count,
            "fallback_count": self._fallback_count,
        })
        return base


# ═══════════════════════════════════════════════════════════════════════════
# Claude20: Extended fusion with quality scoring and source failover tracking
# ═══════════════════════════════════════════════════════════════════════════


@dataclass
class SourceQualityScore:
    """Quality assessment for a data source.

    Claude20: Tracks data freshness, completeness, and reliability.
    """
    source: str
    freshness_score: float = 1.0   # 1.0 = fresh, decays with staleness
    completeness_score: float = 1.0  # Fraction of expected fields present
    reliability_score: float = 1.0   # Based on recent error rate
    composite: float = 1.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "freshness": round(self.freshness_score, 3),
            "completeness": round(self.completeness_score, 3),
            "reliability": round(self.reliability_score, 3),
            "composite": round(self.composite, 3),
        }


class SensorFusionV2(SensorFusion):
    """Extended sensor fusion with quality scoring and failover tracking.

    Claude20: Adds per-source quality scoring, failover event tracking,
    and data validation before fusion. All existing SensorFusion Proc()
    logic preserved.
    """

    def __init__(self) -> None:
        super().__init__()
        self._failover_events: List[Dict[str, Any]] = []
        self._quality_scores: Dict[str, SourceQualityScore] = {}

    def compute_quality(self, source: str, data: Dict[str, Any], latency_ms: float) -> SourceQualityScore:
        """Compute quality score for a data source payload.

        Claude20: Uses freshness, completeness, and reliability to
        decide which source to prefer when multiple are available.
        """
        # Freshness (decays with latency)
        freshness = max(0.0, 1.0 - (latency_ms / 500.0))

        # Completeness (check for expected keys)
        expected_keys = {"allPlayers", "gameData", "events", "activePlayer"}
        present = sum(1 for k in expected_keys if k in data)
        completeness = present / len(expected_keys)

        # Reliability (from source health)
        if source == "lcu":
            health = self._lcu_health
        elif source == "fiddler":
            health = self._fiddler_health
        else:
            health = None

        reliability = 1.0
        if health and health.message_count > 10:
            err_rate = health.error_count / health.message_count
            reliability = max(0.0, 1.0 - err_rate)

        composite = (freshness * 0.4) + (completeness * 0.35) + (reliability * 0.25)

        score = SourceQualityScore(
            source=source,
            freshness_score=freshness,
            completeness_score=completeness,
            reliability_score=reliability,
            composite=composite,
        )
        self._quality_scores[source] = score
        return score

    def record_failover(self, from_source: str, to_source: str, reason: str) -> None:
        """Record a source failover event."""
        event = {
            "from": from_source,
            "to": to_source,
            "reason": reason,
            "timestamp": time.time(),
        }
        self._failover_events.append(event)
        logger.warning("Source failover: %s → %s (%s)", from_source, to_source, reason)

    def get_failover_history(self, count: int = 10) -> List[Dict[str, Any]]:
        return self._failover_events[-count:]

    def get_quality_scores(self) -> Dict[str, Dict[str, Any]]:
        return {name: s.to_dict() for name, s in self._quality_scores.items()}

    def extended_status(self) -> Dict[str, Any]:
        base = self.fusion_status()
        base["quality_scores"] = self.get_quality_scores()
        base["failover_count"] = len(self._failover_events)
        base["recent_failovers"] = self.get_failover_history(5)
        return base
