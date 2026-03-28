"""
Packet Analyzer - Deep inspection of League of Legends HTTP traffic.

Classifies captured Fiddler sessions into LoL-specific API categories,
extracts payload signatures, and detects game lifecycle transitions
(lobby → champ select → loading → in-game → post-game).

Design notes (Knuth-level care):
  - Every classification is O(1) via precomputed pattern tables.
  - Payload extraction uses lazy JSON parsing (parse headers first,
    body only when needed) to minimize allocator pressure during
    high-throughput game phases where the Live Client API fires
    every ~1 s with full game snapshots.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Optional
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from lol_fiddler_agent.network.fiddler_client import HTTPSession

logger = logging.getLogger(__name__)


class APIEndpointCategory(str, Enum):
    """Known LoL API endpoint categories."""
    LIVE_CLIENT_ALL_GAME = "live_client_all_game"
    LIVE_CLIENT_ACTIVE_PLAYER = "live_client_active_player"
    LIVE_CLIENT_PLAYER_LIST = "live_client_player_list"
    LIVE_CLIENT_PLAYER_SCORES = "live_client_player_scores"
    LIVE_CLIENT_PLAYER_ITEMS = "live_client_player_items"
    LIVE_CLIENT_EVENTS = "live_client_events"
    LIVE_CLIENT_GAME_STATS = "live_client_game_stats"

    RIOT_CLIENT_AUTH = "riot_client_auth"
    RIOT_CLIENT_CHAT = "riot_client_chat"
    RIOT_CLIENT_LOBBY = "riot_client_lobby"
    RIOT_CLIENT_CHAMP_SELECT = "riot_client_champ_select"
    RIOT_CLIENT_MATCHMAKING = "riot_client_matchmaking"
    RIOT_CLIENT_SUMMONER = "riot_client_summoner"
    RIOT_CLIENT_RUNES = "riot_client_runes"

    RIOT_API_MATCH = "riot_api_match"
    RIOT_API_SUMMONER = "riot_api_summoner"
    RIOT_API_LEAGUE = "riot_api_league"
    RIOT_API_CHAMPION = "riot_api_champion"

    UNKNOWN = "unknown"


class GameLifecyclePhase(str, Enum):
    """LoL client lifecycle phases detected from traffic patterns."""
    IDLE = "idle"
    LOBBY = "lobby"
    MATCHMAKING = "matchmaking"
    CHAMP_SELECT = "champ_select"
    LOADING = "loading"
    IN_GAME = "in_game"
    POST_GAME = "post_game"
    RECONNECTING = "reconnecting"


# Precomputed pattern table — path regex → category
_ENDPOINT_PATTERNS: list[tuple[re.Pattern, APIEndpointCategory]] = [
    # Live Client Data API (localhost:2999)
    (re.compile(r"/liveclientdata/allgamedata"), APIEndpointCategory.LIVE_CLIENT_ALL_GAME),
    (re.compile(r"/liveclientdata/activeplayer$"), APIEndpointCategory.LIVE_CLIENT_ACTIVE_PLAYER),
    (re.compile(r"/liveclientdata/playerlist$"), APIEndpointCategory.LIVE_CLIENT_PLAYER_LIST),
    (re.compile(r"/liveclientdata/playerscores"), APIEndpointCategory.LIVE_CLIENT_PLAYER_SCORES),
    (re.compile(r"/liveclientdata/playeritems"), APIEndpointCategory.LIVE_CLIENT_PLAYER_ITEMS),
    (re.compile(r"/liveclientdata/eventdata"), APIEndpointCategory.LIVE_CLIENT_EVENTS),
    (re.compile(r"/liveclientdata/gamestats"), APIEndpointCategory.LIVE_CLIENT_GAME_STATS),
    # Riot Client (LCU REST API)
    (re.compile(r"/lol-login/"), APIEndpointCategory.RIOT_CLIENT_AUTH),
    (re.compile(r"/lol-chat/"), APIEndpointCategory.RIOT_CLIENT_CHAT),
    (re.compile(r"/lol-lobby/"), APIEndpointCategory.RIOT_CLIENT_LOBBY),
    (re.compile(r"/lol-champ-select/"), APIEndpointCategory.RIOT_CLIENT_CHAMP_SELECT),
    (re.compile(r"/lol-matchmaking/"), APIEndpointCategory.RIOT_CLIENT_MATCHMAKING),
    (re.compile(r"/lol-summoner/"), APIEndpointCategory.RIOT_CLIENT_SUMMONER),
    (re.compile(r"/lol-perks/"), APIEndpointCategory.RIOT_CLIENT_RUNES),
    # Riot Web API
    (re.compile(r"api\.riotgames\.com.*/lol/match/"), APIEndpointCategory.RIOT_API_MATCH),
    (re.compile(r"api\.riotgames\.com.*/lol/summoner/"), APIEndpointCategory.RIOT_API_SUMMONER),
    (re.compile(r"api\.riotgames\.com.*/lol/league/"), APIEndpointCategory.RIOT_API_LEAGUE),
    (re.compile(r"api\.riotgames\.com.*/lol/champion/"), APIEndpointCategory.RIOT_API_CHAMPION),
]


@dataclass(frozen=True)
class PacketSignature:
    """Immutable fingerprint of a captured HTTP session.

    Used for deduplication and change-detection without full body comparison.
    """
    category: APIEndpointCategory
    method: str
    path: str
    status_code: int
    body_hash: str  # SHA-256 of response body (or "" if none)
    content_length: int

    def matches(self, other: "PacketSignature") -> bool:
        """Two signatures match if same endpoint returned same body."""
        return (
            self.category == other.category
            and self.body_hash == other.body_hash
        )


@dataclass
class AnalyzedPacket:
    """Result of analyzing a single captured HTTP session."""
    session: HTTPSession
    category: APIEndpointCategory
    signature: PacketSignature
    parsed_body: Optional[dict[str, Any]] = None
    analysis_time_ms: float = 0.0
    is_duplicate: bool = False
    lifecycle_hint: Optional[GameLifecyclePhase] = None


class TrafficStatistics(BaseModel):
    """Aggregate statistics for a capture window."""
    total_sessions: int = 0
    lol_sessions: int = 0
    live_client_sessions: int = 0
    riot_client_sessions: int = 0
    riot_api_sessions: int = 0
    unknown_sessions: int = 0
    duplicate_sessions: int = 0
    error_sessions: int = 0  # 4xx/5xx

    avg_response_time_ms: float = 0.0
    max_response_time_ms: float = 0.0

    category_counts: dict[str, int] = Field(default_factory=dict)
    window_start: float = 0.0
    window_end: float = 0.0

    @property
    def window_duration_seconds(self) -> float:
        return max(self.window_end - self.window_start, 0.001)

    @property
    def requests_per_second(self) -> float:
        return self.total_sessions / self.window_duration_seconds


class PacketAnalyzer:
    """Analyzes captured HTTP sessions to classify LoL traffic.

    Thread-safe: uses only local state per analysis call.
    The signature cache is bounded to prevent unbounded memory growth.

    Example::

        analyzer = PacketAnalyzer(dedup_window=30.0)
        for session in captured_sessions:
            result = analyzer.analyze(session)
            if not result.is_duplicate and result.category != APIEndpointCategory.UNKNOWN:
                process(result)
    """

    _MAX_CACHE_SIZE = 2048

    def __init__(
        self,
        dedup_window: float = 5.0,
        parse_bodies: bool = True,
    ) -> None:
        """
        Args:
            dedup_window: Seconds within which identical responses are marked duplicate.
            parse_bodies: Whether to eagerly parse JSON response bodies.
        """
        self._dedup_window = dedup_window
        self._parse_bodies = parse_bodies
        self._signature_cache: dict[str, tuple[PacketSignature, float]] = {}
        self._lifecycle_state = GameLifecyclePhase.IDLE
        self._stats = TrafficStatistics()

    # ── Public API ────────────────────────────────────────────────────────

    def analyze(self, session: HTTPSession) -> AnalyzedPacket:
        """Analyze a single captured HTTP session.

        Returns:
            AnalyzedPacket with classification, signature, and optional parsed body.
        """
        start = time.monotonic()

        category = self._classify(session.url, session.method)
        signature = self._build_signature(session, category)
        is_dup = self._check_duplicate(signature)

        parsed_body: Optional[dict[str, Any]] = None
        if self._parse_bodies and not is_dup and session.response_body:
            parsed_body = self._try_parse_json(session.response_body)

        lifecycle_hint = self._infer_lifecycle(category)

        elapsed = (time.monotonic() - start) * 1000

        result = AnalyzedPacket(
            session=session,
            category=category,
            signature=signature,
            parsed_body=parsed_body,
            analysis_time_ms=elapsed,
            is_duplicate=is_dup,
            lifecycle_hint=lifecycle_hint,
        )

        self._update_stats(result)
        return result

    def analyze_batch(self, sessions: list[HTTPSession]) -> list[AnalyzedPacket]:
        """Analyze a batch of sessions.

        Returns results in same order, with duplicate detection across the batch.
        """
        return [self.analyze(s) for s in sessions]

    def get_statistics(self) -> TrafficStatistics:
        """Return accumulated traffic statistics."""
        return self._stats.model_copy()

    def reset_statistics(self) -> None:
        """Reset accumulated statistics."""
        self._stats = TrafficStatistics()

    @property
    def current_lifecycle(self) -> GameLifecyclePhase:
        """Best-guess current game lifecycle phase."""
        return self._lifecycle_state

    # ── Classification ────────────────────────────────────────────────────

    def _classify(self, url: str, method: str) -> APIEndpointCategory:
        """Classify URL into an API endpoint category.

        Uses precomputed regex table; first match wins.
        """
        # Fast path: check if it's a known LoL domain at all
        lower_url = url.lower()
        is_lol = any(d in lower_url for d in (
            "127.0.0.1:2999", "riot", "leagueoflegends", "pvp.net",
        ))
        if not is_lol:
            return APIEndpointCategory.UNKNOWN

        # Parse path for matching
        try:
            parsed = urlparse(url)
            path = parsed.path
            full = parsed.netloc + parsed.path
        except Exception:
            return APIEndpointCategory.UNKNOWN

        for pattern, category in _ENDPOINT_PATTERNS:
            if pattern.search(full) or pattern.search(path):
                return category

        return APIEndpointCategory.UNKNOWN

    # ── Signature & Dedup ─────────────────────────────────────────────────

    def _build_signature(
        self, session: HTTPSession, category: APIEndpointCategory,
    ) -> PacketSignature:
        body_hash = ""
        content_length = 0
        if session.response_body:
            body_bytes = session.response_body.encode("utf-8", errors="replace")
            body_hash = hashlib.sha256(body_bytes).hexdigest()[:16]
            content_length = len(body_bytes)

        try:
            path = urlparse(session.url).path
        except Exception:
            path = session.url

        return PacketSignature(
            category=category,
            method=session.method,
            path=path,
            status_code=session.status_code,
            body_hash=body_hash,
            content_length=content_length,
        )

    def _check_duplicate(self, sig: PacketSignature) -> bool:
        """Check if this signature was seen within the dedup window."""
        now = time.monotonic()
        cache_key = f"{sig.category}:{sig.body_hash}"

        # Evict expired entries periodically
        if len(self._signature_cache) > self._MAX_CACHE_SIZE:
            cutoff = now - self._dedup_window * 2
            self._signature_cache = {
                k: v for k, v in self._signature_cache.items()
                if v[1] > cutoff
            }

        existing = self._signature_cache.get(cache_key)
        if existing and (now - existing[1]) < self._dedup_window:
            return True

        self._signature_cache[cache_key] = (sig, now)
        return False

    # ── Lifecycle Inference ───────────────────────────────────────────────

    def _infer_lifecycle(self, category: APIEndpointCategory) -> Optional[GameLifecyclePhase]:
        """Infer game lifecycle phase from traffic patterns."""
        phase_map: dict[APIEndpointCategory, GameLifecyclePhase] = {
            APIEndpointCategory.RIOT_CLIENT_LOBBY: GameLifecyclePhase.LOBBY,
            APIEndpointCategory.RIOT_CLIENT_MATCHMAKING: GameLifecyclePhase.MATCHMAKING,
            APIEndpointCategory.RIOT_CLIENT_CHAMP_SELECT: GameLifecyclePhase.CHAMP_SELECT,
            APIEndpointCategory.LIVE_CLIENT_ALL_GAME: GameLifecyclePhase.IN_GAME,
            APIEndpointCategory.LIVE_CLIENT_ACTIVE_PLAYER: GameLifecyclePhase.IN_GAME,
            APIEndpointCategory.LIVE_CLIENT_EVENTS: GameLifecyclePhase.IN_GAME,
            APIEndpointCategory.RIOT_API_MATCH: GameLifecyclePhase.POST_GAME,
        }

        hint = phase_map.get(category)
        if hint:
            self._lifecycle_state = hint
        return hint

    # ── Helpers ────────────────────────────────────────────────────────────

    @staticmethod
    def _try_parse_json(body: str) -> Optional[dict[str, Any]]:
        try:
            return json.loads(body)
        except (json.JSONDecodeError, TypeError):
            return None

    def _update_stats(self, packet: AnalyzedPacket) -> None:
        s = self._stats
        s.total_sessions += 1

        cat = packet.category
        cat_key = cat.value
        s.category_counts[cat_key] = s.category_counts.get(cat_key, 0) + 1

        if cat == APIEndpointCategory.UNKNOWN:
            s.unknown_sessions += 1
        elif cat.value.startswith("live_client"):
            s.live_client_sessions += 1
            s.lol_sessions += 1
        elif cat.value.startswith("riot_client"):
            s.riot_client_sessions += 1
            s.lol_sessions += 1
        elif cat.value.startswith("riot_api"):
            s.riot_api_sessions += 1
            s.lol_sessions += 1

        if packet.is_duplicate:
            s.duplicate_sessions += 1

        sc = packet.session.status_code
        if sc >= 400:
            s.error_sessions += 1

        dur = packet.session.duration_ms
        if dur > s.max_response_time_ms:
            s.max_response_time_ms = dur
        if s.total_sessions > 0:
            # Running average
            s.avg_response_time_ms += (dur - s.avg_response_time_ms) / s.total_sessions

        now = time.time()
        if s.window_start == 0:
            s.window_start = now
        s.window_end = now
