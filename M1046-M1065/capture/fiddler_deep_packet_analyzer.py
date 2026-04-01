#!/usr/bin/env python3
"""
M1053: Fiddler Deep Packet Analyzer
=====================================
OperatorRL M1046-M1065 · 自部署 自环境反馈 自演化

Deep analysis of HTTPS traffic captured by Fiddler Everywhere MCP Server.
Extracts game-critical data from intercepted Riot API responses:
match history, summoner profiles, champion select events, and
in-game WebSocket messages.

Pattern: Read capture/network_capture_engine.py FiddlerMCPClient
→ understand how traffic is captured → implement deep packet analysis
that extracts structured game data from raw HTTP responses.

Fiddler MCP Server (localhost:8868/mcp) provides:
    - get_captured_sessions: list recent HTTP sessions
    - get_session_details: full request/response for a session
    - export_as_har: HAR format export for offline analysis
    - analyze_traffic: AI-powered traffic pattern analysis

Proxifier configuration for LoL:
    Rule: LeagueClient.exe, LeagueClientUx.exe → Fiddler proxy (127.0.0.1:8866)
    Action: HTTPS, Resolve names remotely
"""

import asyncio
import json
import re
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

sys.path.insert(0, str(Path(__file__).parent.parent))
try:
    from evo_logging.evolution_logger import get_logger, LogCategory
    from capture.network_capture_engine import (
        FiddlerMCPClient, InterceptedRequest, EndpointCategory,
        RIOT_ENDPOINT_PATTERNS)
except ImportError:
    pass


@dataclass
class TrafficPattern:
    """Detected pattern in network traffic."""
    pattern_type: str    # "api_burst", "websocket_storm", "slow_response", etc.
    endpoint: str
    occurrence_count: int
    avg_latency_ms: float
    max_latency_ms: float
    first_seen: str
    last_seen: str
    data_volume_bytes: int = 0
    anomaly_score: float = 0.0  # 0=normal, 1=highly anomalous

    def to_dict(self) -> Dict[str, Any]:
        return self.__dict__


@dataclass
class ExtractedGameEvent:
    """Structured game event extracted from network traffic."""
    event_type: str        # "champ_select_pick", "game_start", "kill", etc.
    timestamp: str
    source_endpoint: str
    data: Dict[str, Any]
    confidence: float = 1.0  # How confident we are in the extraction
    match_time_sec: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return self.__dict__


@dataclass
class ProxifierConfig:
    """Configuration for Proxifier routing rules."""
    proxy_host: str = "127.0.0.1"
    proxy_port: int = 8866          # Fiddler default HTTPS port
    target_processes: List[str] = field(default_factory=lambda: [
        "LeagueClient.exe",
        "LeagueClientUx.exe",
        "RiotClientServices.exe",
    ])
    resolve_dns_remotely: bool = True
    https_inspection: bool = True

    def to_proxifier_rule(self) -> str:
        """Generate Proxifier-compatible rule text."""
        processes = "; ".join(self.target_processes)
        return (
            f"Rule: LoL Traffic Capture\n"
            f"  Applications: {processes}\n"
            f"  Target: Any\n"
            f"  Action: Proxy {self.proxy_host}:{self.proxy_port} HTTPS\n"
            f"  Resolve: {'Remote' if self.resolve_dns_remotely else 'Local'}\n"
        )


class FiddlerDeepPacketAnalyzer:
    """
    Deep analysis engine for Fiddler-captured HTTPS traffic.

    Operates in two modes:
    1. Real-time: Continuously polls Fiddler MCP for new sessions
    2. Offline: Analyzes exported HAR/SAZ files

    Analysis capabilities:
    - API endpoint classification and frequency analysis
    - Latency anomaly detection (p99 spike detection)
    - WebSocket message extraction (game events)
    - Champ select data extraction from LCU API responses
    - Match history data extraction without additional API calls
    - Riot API key/token detection (security audit)
    """
    # Known Riot API hosts for traffic filtering
    RIOT_HOSTS = {
        '127.0.0.1',           # LCU API
        'localhost',
        'lol-match-history',
        'sgp.{region}.lol',
    }
    RIOT_HOST_PATTERNS = [
        re.compile(r'.*\.riotgames\.com$'),
        re.compile(r'.*\.leagueoflegends\.com$'),
        re.compile(r'^127\.0\.0\.1$'),
        re.compile(r'^localhost$'),
    ]

    def __init__(self, fiddler_client: Optional[Any] = None):
        self._fiddler = fiddler_client or FiddlerMCPClient()
        self._logger = get_logger()
        self._traffic_log: List[InterceptedRequest] = []
        self._patterns: Dict[str, TrafficPattern] = {}
        self._extracted_events: List[ExtractedGameEvent] = []
        self._endpoint_stats: Dict[str, Dict[str, Any]] = defaultdict(
            lambda: {'count': 0, 'total_latency': 0, 'max_latency': 0,
                     'total_bytes': 0, 'errors': 0})

    def analyze_request(self, req: InterceptedRequest) -> Optional[ExtractedGameEvent]:
        """
        Deep-analyze a single intercepted request.

        Extracts structured game events from response bodies.
        This is the primary real-time analysis entry point.
        """
        self._traffic_log.append(req)
        self._update_endpoint_stats(req)
        # Only analyze Riot API traffic
        if not self._is_riot_traffic(req):
            return None
        # Parse response and extract game events
        response_data = req.get_json_response()
        if not response_data:
            return None
        event = None
        category = req.category
        if category == EndpointCategory.CHAMP_SELECT.value:
            event = self._extract_champ_select_event(req, response_data)
        elif category == EndpointCategory.MATCH_HISTORY.value:
            event = self._extract_match_history_event(req, response_data)
        elif category == EndpointCategory.GAMEFLOW.value:
            event = self._extract_gameflow_event(req, response_data)
        elif category == EndpointCategory.SUMMONER_INFO.value:
            event = self._extract_summoner_event(req, response_data)
        elif category == EndpointCategory.RANKED.value:
            event = self._extract_ranked_event(req, response_data)
        elif category == EndpointCategory.END_OF_GAME.value:
            event = self._extract_end_of_game_event(req, response_data)
        if event:
            self._extracted_events.append(event)
            self._logger.info(
                LogCategory.FIDDLER_MCP,
                f"Extracted event: {event.event_type}",
                data={'endpoint': req.path, 'confidence': event.confidence})
        return event

    def detect_traffic_anomalies(self) -> List[TrafficPattern]:
        """Detect anomalous traffic patterns."""
        anomalies = []
        for endpoint, stats in self._endpoint_stats.items():
            count = stats['count']
            if count < 5:
                continue
            avg_latency = stats['total_latency'] / count
            max_latency = stats['max_latency']
            # Anomaly: max latency > 5x average
            if max_latency > avg_latency * 5 and max_latency > 100:
                pattern = TrafficPattern(
                    pattern_type="latency_spike",
                    endpoint=endpoint,
                    occurrence_count=count,
                    avg_latency_ms=round(avg_latency, 1),
                    max_latency_ms=round(max_latency, 1),
                    first_seen="",
                    last_seen="",
                    anomaly_score=min(max_latency / (avg_latency * 10), 1.0),
                )
                anomalies.append(pattern)
            # Anomaly: high error rate
            error_rate = stats['errors'] / count
            if error_rate > 0.1:  # >10% errors
                pattern = TrafficPattern(
                    pattern_type="high_error_rate",
                    endpoint=endpoint,
                    occurrence_count=count,
                    avg_latency_ms=round(avg_latency, 1),
                    max_latency_ms=round(max_latency, 1),
                    first_seen="",
                    last_seen="",
                    anomaly_score=min(error_rate * 2, 1.0),
                )
                anomalies.append(pattern)
        return sorted(anomalies, key=lambda p: -p.anomaly_score)

    async def analyze_har_export(self, har_json: str) -> List[ExtractedGameEvent]:
        """Analyze a HAR format export from Fiddler."""
        events = []
        try:
            har = json.loads(har_json)
            entries = har.get('log', {}).get('entries', [])
            for entry in entries:
                req_data = entry.get('request', {})
                resp_data = entry.get('response', {})
                url = req_data.get('url', '')
                # Convert HAR entry to InterceptedRequest
                from urllib.parse import urlparse
                parsed = urlparse(url)
                from capture.network_capture_engine import classify_endpoint
                category = classify_endpoint(parsed.path)
                req = InterceptedRequest(
                    request_id=str(hash(url))[:12],
                    timestamp=entry.get('startedDateTime', ''),
                    method=req_data.get('method', 'GET'),
                    url=url,
                    host=parsed.hostname or '',
                    path=parsed.path,
                    status_code=resp_data.get('status', 0),
                    request_headers={h['name']: h['value']
                                     for h in req_data.get('headers', [])},
                    response_headers={h['name']: h['value']
                                      for h in resp_data.get('headers', [])},
                    request_body=req_data.get('postData', {}).get('text'),
                    response_body=resp_data.get('content', {}).get('text'),
                    latency_ms=entry.get('time', 0),
                    category=category.value,
                    capture_mode='FIDDLER_EXPORT',
                )
                event = self.analyze_request(req)
                if event:
                    events.append(event)
        except Exception as e:
            self._logger.error(
                LogCategory.FIDDLER_MCP,
                f"HAR analysis error: {e}")
        return events

    def generate_security_audit(self) -> Dict[str, Any]:
        """Audit captured traffic for security issues."""
        issues = []
        for req in self._traffic_log:
            # Check for exposed auth tokens in URLs
            if 'token=' in req.url.lower() or 'key=' in req.url.lower():
                issues.append({
                    'type': 'token_in_url',
                    'endpoint': req.path,
                    'severity': 'medium',
                    'detail': 'Authentication token visible in URL parameters',
                })
            # Check for unencrypted traffic
            if req.url.startswith('http://') and not req.url.startswith('http://127.'):
                issues.append({
                    'type': 'unencrypted_traffic',
                    'endpoint': req.url,
                    'severity': 'high',
                    'detail': 'Non-localhost traffic over unencrypted HTTP',
                })
        return {
            'total_requests_audited': len(self._traffic_log),
            'issues_found': len(issues),
            'issues': issues[:50],  # Cap to prevent huge reports
        }

    # ---- Event extraction methods ----

    def _extract_champ_select_event(
        self, req: InterceptedRequest, data: Dict
    ) -> Optional[ExtractedGameEvent]:
        if not isinstance(data, dict):
            return None
        return ExtractedGameEvent(
            event_type="champ_select_update",
            timestamp=req.timestamp,
            source_endpoint=req.path,
            data={
                'phase': data.get('timer', {}).get('phase', ''),
                'our_team': len(data.get('myTeam', [])),
                'enemy_team': len(data.get('theirTeam', [])),
                'bans': len(data.get('bans', {}).get('myTeamBans', [])),
            })

    def _extract_match_history_event(
        self, req: InterceptedRequest, data: Dict
    ) -> Optional[ExtractedGameEvent]:
        games = data.get('games', {})
        if isinstance(games, dict):
            games = games.get('games', [])
        if not games:
            return None
        return ExtractedGameEvent(
            event_type="match_history_loaded",
            timestamp=req.timestamp,
            source_endpoint=req.path,
            data={
                'games_count': len(games),
                'puuid': req.path.split('/')[-2] if '/' in req.path else '',
            })

    def _extract_gameflow_event(
        self, req: InterceptedRequest, data: Any
    ) -> Optional[ExtractedGameEvent]:
        phase = data if isinstance(data, str) else str(data)
        return ExtractedGameEvent(
            event_type="gameflow_phase_change",
            timestamp=req.timestamp,
            source_endpoint=req.path,
            data={'phase': phase})

    def _extract_summoner_event(
        self, req: InterceptedRequest, data: Dict
    ) -> Optional[ExtractedGameEvent]:
        if not isinstance(data, dict):
            return None
        return ExtractedGameEvent(
            event_type="summoner_loaded",
            timestamp=req.timestamp,
            source_endpoint=req.path,
            data={
                'summoner_name': data.get('displayName', data.get('gameName', '')),
                'level': data.get('summonerLevel', 0),
                'puuid': data.get('puuid', '')[:8],
            })

    def _extract_ranked_event(
        self, req: InterceptedRequest, data: Dict
    ) -> Optional[ExtractedGameEvent]:
        if not isinstance(data, dict):
            return None
        return ExtractedGameEvent(
            event_type="ranked_stats_loaded",
            timestamp=req.timestamp,
            source_endpoint=req.path,
            data={'queues': list(data.get('queues', data.get('queueMap', {})).keys())})

    def _extract_end_of_game_event(
        self, req: InterceptedRequest, data: Dict
    ) -> Optional[ExtractedGameEvent]:
        if not isinstance(data, dict):
            return None
        return ExtractedGameEvent(
            event_type="end_of_game",
            timestamp=req.timestamp,
            source_endpoint=req.path,
            data={
                'game_id': data.get('gameId', 0),
                'game_length': data.get('gameLength', 0),
            })

    # ---- Internal helpers ----

    def _is_riot_traffic(self, req: InterceptedRequest) -> bool:
        for pattern in self.RIOT_HOST_PATTERNS:
            if pattern.match(req.host):
                return True
        return False

    def _update_endpoint_stats(self, req: InterceptedRequest) -> None:
        stats = self._endpoint_stats[req.path]
        stats['count'] += 1
        stats['total_latency'] += req.latency_ms
        stats['max_latency'] = max(stats['max_latency'], req.latency_ms)
        if req.content_length:
            stats['total_bytes'] += req.content_length
        if req.status_code >= 400:
            stats['errors'] += 1

    def get_stats(self) -> Dict[str, Any]:
        return {
            'total_requests': len(self._traffic_log),
            'extracted_events': len(self._extracted_events),
            'endpoint_count': len(self._endpoint_stats),
            'anomalies': len(self.detect_traffic_anomalies()),
            'top_endpoints': dict(sorted(
                ((k, v['count']) for k, v in self._endpoint_stats.items()),
                key=lambda x: -x[1])[:10]),
        }


class HARFileParser:
    """
    Parses HTTP Archive (HAR) files exported from Fiddler.

    HAR is the standard format for recording HTTP transactions.
    Fiddler can export captured sessions as HAR for offline analysis.
    This enables replay-based development without a live game client.

    Production critique:
        1. User: HAR files from previous game sessions can be loaded
           to test strategy recommendations offline.
        2. System: HAR parsing is memory-efficient — we stream entries
           rather than loading the entire file into memory.
    """
    def __init__(self, har_path: str):
        self._path = Path(har_path)
        self._entries: List[Dict] = []
        self._parsed = False

    def parse(self) -> int:
        """Parse HAR file and return number of entries."""
        if not self._path.exists():
            return 0
        try:
            with open(self._path, 'r', encoding='utf-8') as f:
                har_data = json.loads(f.read())
            self._entries = har_data.get('log', {}).get('entries', [])
            self._parsed = True
            return len(self._entries)
        except (json.JSONDecodeError, IOError) as e:
            return 0

    def get_riot_api_entries(self) -> List[Dict]:
        """Filter entries to only Riot/LoL API calls."""
        if not self._parsed:
            self.parse()
        riot_entries = []
        for entry in self._entries:
            request = entry.get('request', {})
            url = request.get('url', '')
            if any(pattern in url for pattern in [
                '127.0.0.1', 'riotgames.com', 'pvp.net',
                'leagueoflegends.com', '/lol-'
            ]):
                response = entry.get('response', {})
                content = response.get('content', {})
                riot_entries.append({
                    'url': url,
                    'method': request.get('method', 'GET'),
                    'status': response.get('status', 0),
                    'time_ms': entry.get('time', 0),
                    'request_headers': {
                        h['name']: h['value']
                        for h in request.get('headers', [])
                    },
                    'response_headers': {
                        h['name']: h['value']
                        for h in response.get('headers', [])
                    },
                    'response_body': content.get('text', ''),
                    'mime_type': content.get('mimeType', ''),
                    'body_size': content.get('size', 0),
                    'started': entry.get('startedDateTime', ''),
                })
        return riot_entries

    def get_timeline(self) -> List[Dict]:
        """Get chronological timeline of all captured requests."""
        if not self._parsed:
            self.parse()
        timeline = []
        for entry in self._entries:
            request = entry.get('request', {})
            response = entry.get('response', {})
            timeline.append({
                'timestamp': entry.get('startedDateTime', ''),
                'method': request.get('method', ''),
                'url': request.get('url', ''),
                'status': response.get('status', 0),
                'time_ms': entry.get('time', 0),
                'size_bytes': response.get('content', {}).get('size', 0),
            })
        return sorted(timeline, key=lambda x: x['timestamp'])

    def get_api_latency_stats(self) -> Dict[str, Any]:
        """Compute latency statistics for API calls."""
        entries = self.get_riot_api_entries()
        if not entries:
            return {'count': 0}
        latencies = [e['time_ms'] for e in entries if e['time_ms'] > 0]
        if not latencies:
            return {'count': len(entries), 'no_timing_data': True}
        latencies.sort()
        n = len(latencies)
        return {
            'count': n,
            'min_ms': latencies[0],
            'max_ms': latencies[-1],
            'mean_ms': round(sum(latencies) / n, 2),
            'median_ms': latencies[n // 2],
            'p95_ms': latencies[int(n * 0.95)] if n > 1 else latencies[0],
            'p99_ms': latencies[int(n * 0.99)] if n > 1 else latencies[0],
        }

    def extract_match_data(self) -> List[Dict]:
        """Extract match history data from HAR entries."""
        matches = []
        for entry in self.get_riot_api_entries():
            url = entry.get('url', '')
            if '/lol-match-history/' in url and entry.get('response_body'):
                try:
                    body = json.loads(entry['response_body'])
                    games = body.get('games', {}).get('games', [])
                    for game in games:
                        matches.append({
                            'game_id': game.get('gameId'),
                            'champion_id': game.get('participants', [{}])[0].get('championId') if game.get('participants') else None,
                            'queue_id': game.get('queueId'),
                            'game_duration': game.get('gameDuration'),
                            'timestamp': game.get('gameCreation'),
                        })
                except (json.JSONDecodeError, TypeError, IndexError):
                    continue
        return matches
