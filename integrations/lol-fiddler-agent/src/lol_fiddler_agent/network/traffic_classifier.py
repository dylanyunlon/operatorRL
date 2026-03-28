"""
Traffic Classifier - Pattern-based classification of LoL network traffic.

Identifies game-phase transitions, detects anomalous traffic patterns,
and provides a priority scoring system for packet processing order.

Uses a finite-state machine for lifecycle tracking with hysteresis
to avoid flickering between phases on transient traffic bursts.
"""

from __future__ import annotations

import logging
import time
from collections import Counter
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from lol_fiddler_agent.network.packet_analyzer import (
    AnalyzedPacket,
    APIEndpointCategory,
    GameLifecyclePhase,
)

logger = logging.getLogger(__name__)


class TrafficPriority(int, Enum):
    """Processing priority for captured traffic.

    Lower numeric value = higher priority.
    """
    CRITICAL = 0    # Game state during teamfight
    HIGH = 1        # Live client data during game
    NORMAL = 2      # Lobby / champ select data
    LOW = 3         # Background API calls
    IGNORE = 4      # Unrelated traffic


class AnomalyType(str, Enum):
    """Detected traffic anomalies."""
    BURST = "burst"                   # Sudden spike in traffic
    SILENCE = "silence"               # Unexpected absence of traffic
    ERROR_STORM = "error_storm"       # Many 4xx/5xx responses
    LATENCY_SPIKE = "latency_spike"   # High response times
    DUPLICATE_STORM = "duplicate"     # Excessive identical responses
    UNEXPECTED_PHASE = "unexpected"   # Traffic from wrong game phase


@dataclass(frozen=True)
class TrafficAnomaly:
    """A detected traffic anomaly."""
    anomaly_type: AnomalyType
    severity: float  # 0.0 to 1.0
    description: str
    detected_at: float = field(default_factory=time.time)
    context: dict[str, Any] = field(default_factory=dict)


@dataclass
class PhaseTransitionRule:
    """Rule for valid lifecycle phase transitions."""
    from_phase: GameLifecyclePhase
    to_phase: GameLifecyclePhase
    required_signals: int = 1
    cooldown_seconds: float = 0.0

    def can_transition(self, signal_count: int, time_in_phase: float) -> bool:
        return (
            signal_count >= self.required_signals
            and time_in_phase >= self.cooldown_seconds
        )


# Valid transitions with hysteresis requirements
_TRANSITION_RULES: list[PhaseTransitionRule] = [
    PhaseTransitionRule(GameLifecyclePhase.IDLE, GameLifecyclePhase.LOBBY, 1, 0),
    PhaseTransitionRule(GameLifecyclePhase.LOBBY, GameLifecyclePhase.MATCHMAKING, 1, 0),
    PhaseTransitionRule(GameLifecyclePhase.MATCHMAKING, GameLifecyclePhase.CHAMP_SELECT, 1, 0),
    PhaseTransitionRule(GameLifecyclePhase.CHAMP_SELECT, GameLifecyclePhase.LOADING, 2, 3.0),
    PhaseTransitionRule(GameLifecyclePhase.LOADING, GameLifecyclePhase.IN_GAME, 2, 1.0),
    PhaseTransitionRule(GameLifecyclePhase.IN_GAME, GameLifecyclePhase.POST_GAME, 3, 5.0),
    PhaseTransitionRule(GameLifecyclePhase.POST_GAME, GameLifecyclePhase.LOBBY, 2, 3.0),
    PhaseTransitionRule(GameLifecyclePhase.POST_GAME, GameLifecyclePhase.IDLE, 1, 10.0),
    # Reconnection paths
    PhaseTransitionRule(GameLifecyclePhase.IN_GAME, GameLifecyclePhase.RECONNECTING, 1, 0),
    PhaseTransitionRule(GameLifecyclePhase.RECONNECTING, GameLifecyclePhase.IN_GAME, 1, 0),
    # Dodge / leave
    PhaseTransitionRule(GameLifecyclePhase.CHAMP_SELECT, GameLifecyclePhase.LOBBY, 1, 0),
    PhaseTransitionRule(GameLifecyclePhase.MATCHMAKING, GameLifecyclePhase.LOBBY, 1, 0),
]

# Priority assignment per category
_CATEGORY_PRIORITY: dict[APIEndpointCategory, TrafficPriority] = {
    APIEndpointCategory.LIVE_CLIENT_ALL_GAME: TrafficPriority.CRITICAL,
    APIEndpointCategory.LIVE_CLIENT_ACTIVE_PLAYER: TrafficPriority.HIGH,
    APIEndpointCategory.LIVE_CLIENT_EVENTS: TrafficPriority.HIGH,
    APIEndpointCategory.LIVE_CLIENT_PLAYER_SCORES: TrafficPriority.HIGH,
    APIEndpointCategory.LIVE_CLIENT_PLAYER_ITEMS: TrafficPriority.NORMAL,
    APIEndpointCategory.LIVE_CLIENT_PLAYER_LIST: TrafficPriority.NORMAL,
    APIEndpointCategory.LIVE_CLIENT_GAME_STATS: TrafficPriority.NORMAL,
    APIEndpointCategory.RIOT_CLIENT_CHAMP_SELECT: TrafficPriority.NORMAL,
    APIEndpointCategory.RIOT_CLIENT_LOBBY: TrafficPriority.LOW,
    APIEndpointCategory.RIOT_CLIENT_MATCHMAKING: TrafficPriority.LOW,
    APIEndpointCategory.RIOT_CLIENT_AUTH: TrafficPriority.LOW,
    APIEndpointCategory.RIOT_CLIENT_CHAT: TrafficPriority.IGNORE,
    APIEndpointCategory.RIOT_CLIENT_SUMMONER: TrafficPriority.LOW,
    APIEndpointCategory.RIOT_CLIENT_RUNES: TrafficPriority.LOW,
    APIEndpointCategory.RIOT_API_MATCH: TrafficPriority.LOW,
    APIEndpointCategory.RIOT_API_SUMMONER: TrafficPriority.LOW,
    APIEndpointCategory.RIOT_API_LEAGUE: TrafficPriority.LOW,
    APIEndpointCategory.RIOT_API_CHAMPION: TrafficPriority.LOW,
    APIEndpointCategory.UNKNOWN: TrafficPriority.IGNORE,
}


class TrafficClassifier:
    """Classifies and prioritises captured LoL traffic.

    Maintains a finite-state machine for lifecycle tracking with
    hysteresis to prevent phase-flickering.

    Example::

        classifier = TrafficClassifier()
        for packet in stream:
            priority = classifier.get_priority(packet)
            phase = classifier.update_phase(packet)
            anomalies = classifier.check_anomalies()
    """

    def __init__(
        self,
        anomaly_window: float = 30.0,
        burst_threshold: int = 50,
        silence_threshold: float = 15.0,
        error_ratio_threshold: float = 0.3,
    ) -> None:
        self._current_phase = GameLifecyclePhase.IDLE
        self._phase_entered_at: float = time.time()
        self._phase_signal_counts: Counter[GameLifecyclePhase] = Counter()

        # Anomaly detection config
        self._anomaly_window = anomaly_window
        self._burst_threshold = burst_threshold
        self._silence_threshold = silence_threshold
        self._error_ratio_threshold = error_ratio_threshold

        # Sliding window for anomaly detection
        self._recent_timestamps: list[float] = []
        self._recent_errors: int = 0
        self._recent_total: int = 0
        self._recent_duplicates: int = 0
        self._last_packet_time: float = time.time()
        self._detected_anomalies: list[TrafficAnomaly] = []

    # ── Priority ──────────────────────────────────────────────────────────

    def get_priority(self, packet: AnalyzedPacket) -> TrafficPriority:
        """Get processing priority for a packet."""
        base = _CATEGORY_PRIORITY.get(packet.category, TrafficPriority.IGNORE)

        # Boost priority during in-game phase
        if self._current_phase == GameLifecyclePhase.IN_GAME:
            if base == TrafficPriority.HIGH:
                return TrafficPriority.CRITICAL
            if base == TrafficPriority.NORMAL:
                return TrafficPriority.HIGH

        return base

    # ── Phase Tracking ────────────────────────────────────────────────────

    def update_phase(self, packet: AnalyzedPacket) -> GameLifecyclePhase:
        """Update lifecycle phase based on packet, with hysteresis."""
        hint = packet.lifecycle_hint
        if hint is None or hint == self._current_phase:
            return self._current_phase

        # Accumulate signals for the hinted phase
        self._phase_signal_counts[hint] += 1
        signal_count = self._phase_signal_counts[hint]
        time_in_phase = time.time() - self._phase_entered_at

        # Check transition rules
        for rule in _TRANSITION_RULES:
            if rule.from_phase == self._current_phase and rule.to_phase == hint:
                if rule.can_transition(signal_count, time_in_phase):
                    old = self._current_phase
                    self._current_phase = hint
                    self._phase_entered_at = time.time()
                    self._phase_signal_counts.clear()
                    logger.info("Phase transition: %s → %s (signals=%d)", old.value, hint.value, signal_count)
                    return hint
                break

        return self._current_phase

    @property
    def current_phase(self) -> GameLifecyclePhase:
        return self._current_phase

    # ── Anomaly Detection ─────────────────────────────────────────────────

    def record_packet(self, packet: AnalyzedPacket) -> None:
        """Record a packet for anomaly detection."""
        now = time.time()
        self._recent_timestamps.append(now)
        self._recent_total += 1
        self._last_packet_time = now

        if packet.session.status_code >= 400:
            self._recent_errors += 1
        if packet.is_duplicate:
            self._recent_duplicates += 1

        # Prune old entries
        cutoff = now - self._anomaly_window
        self._recent_timestamps = [t for t in self._recent_timestamps if t > cutoff]

    def check_anomalies(self) -> list[TrafficAnomaly]:
        """Check for traffic anomalies in the current window."""
        anomalies: list[TrafficAnomaly] = []
        now = time.time()

        # Burst detection
        window_count = len(self._recent_timestamps)
        if window_count > self._burst_threshold:
            anomalies.append(TrafficAnomaly(
                anomaly_type=AnomalyType.BURST,
                severity=min(1.0, window_count / (self._burst_threshold * 2)),
                description=f"{window_count} packets in {self._anomaly_window}s window",
                context={"count": window_count, "threshold": self._burst_threshold},
            ))

        # Silence detection (only during in-game)
        silence = now - self._last_packet_time
        if (
            self._current_phase == GameLifecyclePhase.IN_GAME
            and silence > self._silence_threshold
        ):
            anomalies.append(TrafficAnomaly(
                anomaly_type=AnomalyType.SILENCE,
                severity=min(1.0, silence / (self._silence_threshold * 3)),
                description=f"No traffic for {silence:.1f}s during in-game",
                context={"silence_seconds": silence},
            ))

        # Error storm
        if self._recent_total > 10:
            error_ratio = self._recent_errors / self._recent_total
            if error_ratio > self._error_ratio_threshold:
                anomalies.append(TrafficAnomaly(
                    anomaly_type=AnomalyType.ERROR_STORM,
                    severity=min(1.0, error_ratio),
                    description=f"{self._recent_errors}/{self._recent_total} errors ({error_ratio:.0%})",
                    context={"errors": self._recent_errors, "total": self._recent_total},
                ))

        # Duplicate storm
        if self._recent_total > 10:
            dup_ratio = self._recent_duplicates / self._recent_total
            if dup_ratio > 0.8:
                anomalies.append(TrafficAnomaly(
                    anomaly_type=AnomalyType.DUPLICATE_STORM,
                    severity=dup_ratio,
                    description=f"{self._recent_duplicates}/{self._recent_total} duplicates",
                ))

        self._detected_anomalies = anomalies
        return anomalies

    def reset_counters(self) -> None:
        """Reset anomaly detection counters."""
        self._recent_timestamps.clear()
        self._recent_errors = 0
        self._recent_total = 0
        self._recent_duplicates = 0
        self._detected_anomalies.clear()

    def get_summary(self) -> dict[str, Any]:
        return {
            "current_phase": self._current_phase.value,
            "phase_duration": time.time() - self._phase_entered_at,
            "recent_packets": len(self._recent_timestamps),
            "recent_errors": self._recent_errors,
            "recent_duplicates": self._recent_duplicates,
            "anomalies": len(self._detected_anomalies),
        }
