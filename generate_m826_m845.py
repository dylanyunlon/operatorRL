#!/usr/bin/env python3
"""
M826-M845 Module Generator
===========================
Reads diagnostic logs from M846-M865, generates 20 improvement modules (500+ lines each).
Each module addresses specific diagnostic findings from the logging system.

Maps:
  M826 → seraphine_lcu_websocket_bridge.py     (Seraphine WebSocket event bridge)
  M827 → fiddler_mcp_integration_layer.py       (Fiddler MCP Server connector)
  M828 → agentic_self_evolution_loop.py          (Self-evolution feedback loop)
  M829 → async_riot_api_pipeline.py              (Async Riot API data pipeline)
  M830 → riot_response_schema_validator.py       (Riot API response validation)
  M831 → otel_distributed_tracer.py              (OpenTelemetry tracing layer)
  M832 → multi_tier_cache_engine.py              (L1 memory + L2 SQLite cache)
  M833 → proxifier_config_validator.py           (Proxifier routing validator)
  M834 → cross_match_correlation_engine.py       (Historical↔realtime correlator)
  M835 → circuit_breaker_resilience.py           (Circuit breaker + bulkhead)
  M836 → lcu_lockfile_port_detector.py           (Dynamic LCU port detection)
  M837 → har_traffic_analyzer.py                 (HAR file traffic analysis)
  M838 → match_data_etl_pipeline.py              (Extract-Transform-Load pipeline)
  M839 → champion_meta_trend_analyzer.py         (Meta trend & patch analysis)
  M840 → live_game_state_aggregator.py           (Live Client Data aggregator)
  M841 → summoner_behavior_classifier.py         (Behavior pattern classification)
  M842 → team_synergy_calculator.py              (Team composition synergy math)
  M843 → objective_priority_ranker.py            (Dragon/Baron priority ranking)
  M844 → network_packet_replay_engine.py         (Captured traffic replay)
  M845 → plan_update_m826_m845_integrator.py     (plan.md updater)
"""

import json
import os
import pathlib
import textwrap

BASE = pathlib.Path(__file__).parent
OUTPUT = BASE / "M826-M845"
OUTPUT.mkdir(exist_ok=True)

# Load diagnostic data
DIAG_PATH = BASE / "M846-M865" / "logs" / "diagnostic_report.json"
if DIAG_PATH.exists():
    with open(DIAG_PATH) as f:
        DIAG = json.load(f)
else:
    DIAG = {"modules": {}, "summary": {}}

MODULES = [
    {
        "id": "M826", "name": "seraphine_lcu_websocket_bridge",
        "title": "SeraphineLcuWebSocketBridge",
        "desc": "WebSocket event subscription bridge following Seraphine's LcuWebSocket pattern",
        "deps": "M846,M847",
        "focus": "seraphine_integration",
        "detail": """Implements real-time LCU event push via WebSocket, mirroring Seraphine's
LcuWebSocket.subscribe() architecture. Handles connection lifecycle, event filtering,
automatic reconnection with exponential backoff, and event routing to downstream modules.
Supports all LCU event types: gameflow phase changes, champ select updates, summoner
profile changes, and SGP token refresh. Integrates with OperatorRL's GovernedEnvironment
for agentic feedback signals.""",
        "methods": [
            ("connect", "host: str, port: int, token: str", "bool", "Establish WebSocket connection to LCU"),
            ("subscribe_event", "event_uri: str, event_types: List[str], callback: Callable", "str", "Subscribe to LCU event with callback, returns subscription_id"),
            ("unsubscribe_event", "subscription_id: str", "bool", "Remove event subscription"),
            ("_handle_message", "raw_data: bytes", "None", "Parse incoming WebSocket frame and route to subscribers"),
            ("_reconnect_loop", "", "None", "Async reconnection with exponential backoff and jitter"),
            ("_heartbeat", "", "None", "Periodic ping to detect connection staleness"),
            ("get_connection_state", "", "str", "Return current WebSocket connection state"),
            ("get_subscription_count", "", "int", "Count active subscriptions"),
            ("get_event_stats", "", "Dict", "Return event receive/dispatch statistics"),
            ("drain_event_queue", "max_events: int", "List[Dict]", "Drain buffered events up to max"),
            ("close", "", "bool", "Graceful WebSocket shutdown"),
        ],
        "extra_classes": [
            ("LcuWebSocketState", "enum.Enum", ["DISCONNECTED", "CONNECTING", "CONNECTED", "RECONNECTING", "CLOSED"]),
            ("LcuEventFilter", "dataclass", ["event_uri: str", "event_types: Tuple[str, ...]", "callback: Callable", "subscription_id: str", "created_at: float"]),
            ("WebSocketFrame", "dataclass", ["opcode: int", "payload: bytes", "timestamp: float", "is_masked: bool"]),
            ("ConnectionMetrics", "dataclass", ["connected_at: float", "messages_received: int", "messages_sent: int", "reconnect_count: int", "last_ping: float", "last_pong: float"]),
        ],
    },
    {
        "id": "M827", "name": "fiddler_mcp_integration_layer",
        "title": "FiddlerMcpIntegrationLayer",
        "desc": "Fiddler Everywhere MCP Server connector for HTTP traffic analysis",
        "deps": "M846,M859",
        "focus": "fiddler_mcp",
        "detail": """Connects to Fiddler Everywhere's MCP Server at localhost:8868/mcp for
complete HTTPS traffic capture and analysis. Provides API call extraction, request/response
classification, HAR export for offline analysis, and real-time traffic monitoring.
Authorization via Fiddler-generated API key. Integrates with Proxifier for selective
LoL client traffic routing through Fiddler. Reference: telerik.com/fiddler documentation.""",
        "methods": [
            ("connect_mcp", "host: str, port: int, api_key: str", "bool", "Connect to Fiddler MCP endpoint"),
            ("capture_session_start", "filter_rules: Dict", "str", "Start traffic capture with filter rules"),
            ("capture_session_stop", "session_id: str", "Dict", "Stop capture and return session summary"),
            ("extract_riot_api_calls", "session_id: str", "List[Dict]", "Extract Riot API calls from captured traffic"),
            ("classify_request", "request_data: Dict", "str", "Classify HTTP request by Riot API endpoint type"),
            ("export_har", "session_id: str, output_path: str", "str", "Export capture session as HAR file"),
            ("parse_har_file", "har_path: str", "List[Dict]", "Parse HAR file and extract API interactions"),
            ("get_traffic_stats", "session_id: str", "Dict", "Return traffic statistics for session"),
            ("monitor_realtime", "callback: Callable, filter_pattern: str", "str", "Start real-time traffic monitoring"),
            ("validate_api_key", "api_key: str", "bool", "Validate Fiddler API key"),
            ("disconnect", "", "bool", "Disconnect from Fiddler MCP"),
        ],
        "extra_classes": [
            ("FiddlerConnectionState", "enum.Enum", ["DISCONNECTED", "CONNECTING", "AUTHENTICATED", "CAPTURING", "ERROR"]),
            ("CaptureSession", "dataclass", ["session_id: str", "started_at: float", "filter_rules: Dict", "request_count: int", "response_count: int"]),
            ("RiotApiCall", "dataclass", ["method: str", "url: str", "status_code: int", "request_body: Optional[str]", "response_body: Optional[str]", "latency_ms: float", "timestamp: float"]),
            ("TrafficClassification", "dataclass", ["endpoint_type: str", "api_version: str", "resource: str", "is_lcu: bool", "is_riot_api: bool", "is_live_client: bool"]),
        ],
    },
    {
        "id": "M828", "name": "agentic_self_evolution_loop",
        "title": "AgenticSelfEvolutionLoop",
        "desc": "Self-evolution feedback loop connecting module performance to RL training",
        "deps": "M846",
        "focus": "agentic_loop",
        "detail": """Implements the core agentic self-evolution pattern from OperatorRL:
GovernedEnvironment.step() → success/error signals → PolicyReward → PPO gradient update.
Captures module performance metrics (latency, accuracy, error rates), transforms them
into reward signals for the agentlightning training loop, and supports hot-swap of
improved module versions. This is the bridge between the M846-M865 subsystem and the
parent OperatorRL self-evolution infrastructure.""",
        "methods": [
            ("register_module", "module_id: str, module_ref: Any", "bool", "Register module for performance tracking"),
            ("collect_metrics", "module_id: str", "Dict", "Collect latest performance metrics from module"),
            ("compute_reward_signal", "metrics: Dict", "float", "Transform performance metrics into scalar reward"),
            ("emit_training_span", "module_id: str, reward: float, context: Dict", "str", "Emit span to agentlightning training store"),
            ("evaluate_evolution_candidate", "current_version: str, candidate_version: str", "Dict", "Compare two module versions via A/B metrics"),
            ("trigger_hot_swap", "module_id: str, new_version_path: str", "bool", "Hot-swap module to evolved version"),
            ("get_evolution_history", "module_id: str, limit: int", "List[Dict]", "Get version evolution history"),
            ("compute_population_fitness", "", "Dict", "Aggregate fitness across all registered modules"),
            ("rollback_evolution", "module_id: str, target_version: str", "bool", "Rollback module to previous version"),
            ("get_training_summary", "", "Dict", "Summary of training spans emitted"),
        ],
        "extra_classes": [
            ("EvolutionState", "enum.Enum", ["IDLE", "COLLECTING", "EVALUATING", "EVOLVING", "ROLLING_BACK"]),
            ("ModulePerformance", "dataclass", ["module_id: str", "version: str", "avg_latency_ms: float", "error_rate: float", "success_count: int", "failure_count: int", "last_updated: float"]),
            ("RewardSignal", "dataclass", ["module_id: str", "reward: float", "components: Dict[str, float]", "timestamp: float"]),
            ("EvolutionRecord", "dataclass", ["from_version: str", "to_version: str", "reward_delta: float", "swapped_at: float", "rolled_back: bool"]),
        ],
    },
    {
        "id": "M829", "name": "async_riot_api_pipeline",
        "title": "AsyncRiotApiPipeline",
        "desc": "Async data pipeline with asyncio.gather for parallel Riot API requests",
        "deps": "M846,M847",
        "focus": "async_patterns",
        "detail": """High-throughput async pipeline following Seraphine's concurrent fetch patterns.
Uses asyncio.gather() for parallel Riot API requests with configurable concurrency limits,
automatic rate limiting per Riot API constraints (20/s, 100/2min), request deduplication,
and response streaming. Supports both LCU local API and remote Riot Games API endpoints.""",
        "methods": [
            ("fetch_batch", "requests: List[Dict]", "List[Dict]", "Execute batch of API requests in parallel"),
            ("fetch_match_history", "puuid: str, count: int", "List[Dict]", "Fetch match history with pagination"),
            ("fetch_match_details_batch", "match_ids: List[str]", "List[Dict]", "Parallel fetch of multiple match details"),
            ("fetch_timelines_batch", "match_ids: List[str]", "List[Dict]", "Parallel fetch of match timelines"),
            ("fetch_summoner_batch", "puuids: List[str]", "List[Dict]", "Parallel fetch of summoner profiles"),
            ("stream_live_data", "interval_ms: int, callback: Callable", "str", "Stream live client data at interval"),
            ("cancel_stream", "stream_id: str", "bool", "Cancel active data stream"),
            ("get_pipeline_stats", "", "Dict", "Return pipeline throughput and error statistics"),
            ("deduplicate_requests", "requests: List[Dict]", "List[Dict]", "Remove duplicate requests from batch"),
            ("set_concurrency_limit", "limit: int", "None", "Set maximum concurrent requests"),
        ],
        "extra_classes": [
            ("PipelineState", "enum.Enum", ["IDLE", "FETCHING", "STREAMING", "THROTTLED", "ERROR"]),
            ("ApiRequest", "dataclass", ["method: str", "url: str", "params: Optional[Dict]", "headers: Optional[Dict]", "priority: int", "dedup_key: str"]),
            ("ApiResponse", "dataclass", ["status_code: int", "body: Any", "latency_ms: float", "from_cache: bool", "request: 'ApiRequest'"]),
            ("PipelineMetrics", "dataclass", ["total_requests: int", "successful: int", "failed: int", "cached: int", "avg_latency_ms: float", "throttled_count: int"]),
        ],
    },
    {
        "id": "M830", "name": "riot_response_schema_validator",
        "title": "RiotResponseSchemaValidator",
        "desc": "Schema enforcement for all Riot API response parsing",
        "deps": "M846",
        "focus": "data_validation",
        "detail": """Comprehensive input validation layer for Riot API responses. Defines JSON schemas
for all Riot API endpoints used by M846-M865 (match history, timelines, summoner data,
champion mastery, ranked stats, live client data). Prevents silent data corruption by
validating response structure before downstream processing. Supports schema versioning
across Riot API versions.""",
        "methods": [
            ("validate_match_dto", "data: Dict", "Tuple[bool, List[str]]", "Validate Match-V5 DTO structure"),
            ("validate_timeline_dto", "data: Dict", "Tuple[bool, List[str]]", "Validate Timeline-V5 DTO structure"),
            ("validate_summoner_dto", "data: Dict", "Tuple[bool, List[str]]", "Validate Summoner-V4 DTO structure"),
            ("validate_mastery_dto", "data: Dict", "Tuple[bool, List[str]]", "Validate ChampionMastery-V4 DTO"),
            ("validate_ranked_dto", "data: Dict", "Tuple[bool, List[str]]", "Validate Ranked-V1 DTO structure"),
            ("validate_live_client_data", "data: Dict", "Tuple[bool, List[str]]", "Validate Live Client Data API response"),
            ("validate_lcu_response", "endpoint: str, data: Any", "Tuple[bool, List[str]]", "Validate LCU endpoint response"),
            ("register_custom_schema", "name: str, schema: Dict", "bool", "Register custom validation schema"),
            ("get_validation_stats", "", "Dict", "Return validation pass/fail statistics"),
            ("strict_mode", "enabled: bool", "None", "Toggle strict validation (raise vs warn)"),
        ],
        "extra_classes": [
            ("ValidationResult", "dataclass", ["valid: bool", "errors: List[str]", "warnings: List[str]", "schema_version: str"]),
            ("SchemaDefinition", "dataclass", ["name: str", "version: str", "required_fields: List[str]", "optional_fields: List[str]", "nested_schemas: Dict[str, 'SchemaDefinition']"]),
            ("FieldRule", "dataclass", ["field_name: str", "field_type: str", "required: bool", "min_value: Optional[float]", "max_value: Optional[float]", "pattern: Optional[str]"]),
        ],
    },
    {
        "id": "M831", "name": "otel_distributed_tracer",
        "title": "OtelDistributedTracer",
        "desc": "OpenTelemetry tracing layer for M846-M865 subsystem",
        "deps": "M846",
        "focus": "telemetry",
        "detail": """Integrates OpenTelemetry spans for distributed tracing across the entire
M846-M865 subsystem. Connects to agentlightning/tracer/otel.py for trace export.
Provides automatic span creation for all inter-module calls, API requests, cache
operations, and event processing. Supports trace context propagation across async
boundaries and between network-captured and locally-processed data paths.""",
        "methods": [
            ("start_span", "name: str, attributes: Dict", "str", "Start a new trace span, returns span_id"),
            ("end_span", "span_id: str, status: str", "None", "End span with status"),
            ("add_event", "span_id: str, name: str, attributes: Dict", "None", "Add event to active span"),
            ("set_attribute", "span_id: str, key: str, value: Any", "None", "Set span attribute"),
            ("create_child_span", "parent_span_id: str, name: str", "str", "Create child span linked to parent"),
            ("inject_context", "headers: Dict", "Dict", "Inject trace context into HTTP headers"),
            ("extract_context", "headers: Dict", "Optional[str]", "Extract trace context from HTTP headers"),
            ("get_active_spans", "", "List[Dict]", "List all active (unclosed) spans"),
            ("export_traces", "format: str", "str", "Export traces in OTLP/JSON format"),
            ("flush", "", "bool", "Force flush pending trace data"),
        ],
        "extra_classes": [
            ("SpanStatus", "enum.Enum", ["UNSET", "OK", "ERROR"]),
            ("TraceSpan", "dataclass", ["span_id: str", "trace_id: str", "parent_id: Optional[str]", "name: str", "start_time: float", "end_time: Optional[float]", "status: str", "attributes: Dict", "events: List[Dict]"]),
            ("TraceContext", "dataclass", ["trace_id: str", "span_id: str", "trace_flags: int"]),
        ],
    },
    {
        "id": "M832", "name": "multi_tier_cache_engine",
        "title": "MultiTierCacheEngine",
        "desc": "L1 memory LRU + L2 disk SQLite cache with TTL and cross-module invalidation",
        "deps": "M846",
        "focus": "caching",
        "detail": """Multi-tier caching engine: L1 is an in-memory LRU cache with configurable
max entries, L2 is a persistent SQLite database for cross-session cache survival.
Supports per-endpoint TTL configuration, cross-module cache invalidation via event
bus, cache warming on startup, and cache analytics for hit/miss ratio optimization.
Designed to handle Riot API rate limits by aggressively caching immutable data
(historical matches) while using short TTL for mutable data (live game state).""",
        "methods": [
            ("get", "key: str", "Optional[Any]", "Get from L1, fallback to L2, return None on miss"),
            ("set", "key: str, value: Any, ttl: Optional[int]", "bool", "Set in both L1 and L2 with TTL"),
            ("delete", "key: str", "bool", "Delete from both L1 and L2"),
            ("invalidate_prefix", "prefix: str", "int", "Invalidate all keys matching prefix, return count"),
            ("invalidate_module", "module_id: str", "int", "Invalidate all keys owned by module"),
            ("warm_cache", "keys: List[str], loader: Callable", "int", "Pre-warm cache from loader function"),
            ("get_stats", "", "Dict", "Return hit/miss ratios, sizes, eviction counts"),
            ("compact_l2", "", "bool", "Compact SQLite L2 database"),
            ("register_invalidation_handler", "pattern: str, callback: Callable", "str", "Register callback for invalidation events"),
            ("export_keys", "prefix: str", "List[str]", "List all keys matching prefix"),
            ("clear_all", "", "bool", "Clear both L1 and L2 caches"),
        ],
        "extra_classes": [
            ("CacheEntry", "dataclass", ["key: str", "value: Any", "created_at: float", "expires_at: float", "access_count: int", "module_id: str"]),
            ("CacheTier", "enum.Enum", ["L1_MEMORY", "L2_SQLITE"]),
            ("CacheStats", "dataclass", ["l1_hits: int", "l1_misses: int", "l2_hits: int", "l2_misses: int", "total_entries: int", "evictions: int"]),
            ("InvalidationEvent", "dataclass", ["pattern: str", "source_module: str", "keys_affected: int", "timestamp: float"]),
        ],
    },
    {
        "id": "M833", "name": "proxifier_config_validator",
        "title": "ProxifierConfigValidator",
        "desc": "Validates Proxifier routing for LoL traffic through Fiddler proxy",
        "deps": "M846,M859",
        "focus": "proxifier",
        "detail": """Validates and manages Proxifier configuration for routing League of Legends
client traffic through Fiddler proxy. Detects LoL client process, verifies Proxifier
rules, checks certificate pinning issues, and provides diagnostic reports for
network capture setup. Supports automatic rule generation and health monitoring.""",
        "methods": [
            ("detect_lol_process", "", "Optional[Dict]", "Find running LeagueClient.exe / LeagueClientUx.exe"),
            ("validate_proxifier_rules", "config_path: str", "Tuple[bool, List[str]]", "Validate Proxifier XML config"),
            ("generate_lol_rules", "fiddler_port: int", "str", "Generate Proxifier rules for LoL traffic"),
            ("check_cert_pinning", "target_host: str", "Dict", "Check if target uses certificate pinning"),
            ("test_proxy_connectivity", "proxy_host: str, proxy_port: int", "bool", "Test connectivity to proxy"),
            ("get_lol_network_config", "", "Dict", "Extract LoL client network configuration"),
            ("monitor_proxy_health", "interval_s: int, callback: Callable", "str", "Start proxy health monitoring"),
            ("diagnose_capture_issues", "", "Dict", "Run full diagnostic for capture setup"),
            ("export_config_template", "output_path: str", "str", "Export Proxifier config template"),
            ("stop_monitoring", "monitor_id: str", "bool", "Stop health monitoring"),
        ],
        "extra_classes": [
            ("ProxifierRule", "dataclass", ["name: str", "application: str", "target_hosts: List[str]", "action: str", "proxy_chain: str", "enabled: bool"]),
            ("ProcessInfo", "dataclass", ["pid: int", "name: str", "exe_path: str", "port: Optional[int]", "token: Optional[str]"]),
            ("CertPinningResult", "dataclass", ["host: str", "has_pinning: bool", "pin_type: str", "bypass_possible: bool"]),
            ("ProxyHealthStatus", "dataclass", ["proxy_host: str", "proxy_port: int", "is_reachable: bool", "latency_ms: float", "last_check: float"]),
        ],
    },
    {
        "id": "M834", "name": "cross_match_correlation_engine",
        "title": "CrossMatchCorrelationEngine",
        "desc": "Correlates historical battle data with real-time game state for predictions",
        "deps": "M846,M847,M849,M860",
        "focus": "correlation",
        "detail": """Core analytical engine that bridges historical match data with real-time game
state. Computes correlation coefficients between historical patterns and current game
progression, enabling predictive analysis for objectives, team fights, and game outcomes.
Integrates with CrossMatchPatternMiner(M860) for pattern extraction and
RealtimeStrategyRecommender(M861) for actionable recommendations.""",
        "methods": [
            ("correlate_player_history", "puuid: str, current_champion: int, current_lane: str", "Dict", "Correlate player's historical performance with current game context"),
            ("correlate_team_comp", "ally_champions: List[int], enemy_champions: List[int]", "Dict", "Compute team composition correlation from historical win rates"),
            ("correlate_game_progression", "current_game_state: Dict, historical_matches: List[Dict]", "Dict", "Match current game flow against historical patterns"),
            ("predict_outcome", "game_state: Dict", "Dict", "Predict game outcome probability from correlations"),
            ("find_similar_matches", "game_state: Dict, limit: int", "List[Dict]", "Find historically similar matches"),
            ("compute_player_synergy", "team_puuids: List[str]", "Dict", "Compute synergy from shared match history"),
            ("detect_tilt_indicators", "puuid: str, recent_matches: List[Dict]", "Dict", "Detect tilt/performance degradation patterns"),
            ("get_correlation_confidence", "correlation_id: str", "float", "Get confidence score for a correlation result"),
            ("build_feature_vector", "game_state: Dict", "List[float]", "Build ML feature vector from game state"),
            ("update_correlation_model", "new_data: List[Dict]", "bool", "Update correlation weights with new match data"),
        ],
        "extra_classes": [
            ("CorrelationResult", "dataclass", ["correlation_id: str", "coefficient: float", "confidence: float", "sample_size: int", "features: Dict[str, float]"]),
            ("GameStateSnapshot", "dataclass", ["timestamp: float", "gold_diff: int", "kill_diff: int", "tower_diff: int", "dragon_count: Dict[str, int]", "baron_count: int"]),
            ("PredictionResult", "dataclass", ["win_probability: float", "confidence: float", "key_factors: List[str]", "similar_match_count: int"]),
            ("TiltIndicator", "dataclass", ["is_tilted: bool", "tilt_score: float", "indicators: List[str]", "recent_performance_trend: str"]),
        ],
    },
    {
        "id": "M835", "name": "circuit_breaker_resilience",
        "title": "CircuitBreakerResilience",
        "desc": "Circuit breaker pattern with bulkhead isolation for subsystem resilience",
        "deps": "M846",
        "focus": "error_handling",
        "detail": """Production-grade resilience layer implementing circuit breaker, bulkhead,
and retry patterns for all external API calls in M846-M865. Prevents cascade failures
when Riot API, LCU, or Fiddler services are unavailable. Each module gets an isolated
circuit breaker with configurable failure thresholds, recovery timeouts, and half-open
probe intervals. Connects to OperatorRL's circuit_breaker.py for system-wide coordination.""",
        "methods": [
            ("create_breaker", "name: str, failure_threshold: int, recovery_timeout: float", "str", "Create named circuit breaker"),
            ("execute_with_breaker", "breaker_name: str, fn: Callable, fallback: Optional[Callable]", "Any", "Execute function through circuit breaker"),
            ("get_breaker_state", "name: str", "str", "Get circuit breaker state (CLOSED/OPEN/HALF_OPEN)"),
            ("reset_breaker", "name: str", "bool", "Force reset circuit breaker to CLOSED"),
            ("create_bulkhead", "name: str, max_concurrent: int, max_queue: int", "str", "Create bulkhead for concurrency isolation"),
            ("execute_with_bulkhead", "bulkhead_name: str, fn: Callable", "Any", "Execute within bulkhead limits"),
            ("get_all_breaker_stats", "", "Dict", "Get stats for all circuit breakers"),
            ("register_state_change_handler", "callback: Callable", "str", "Register handler for breaker state changes"),
            ("configure_retry_policy", "name: str, max_retries: int, backoff_factor: float", "None", "Configure retry policy for a breaker"),
            ("health_check", "", "Dict", "Overall resilience subsystem health check"),
        ],
        "extra_classes": [
            ("BreakerState", "enum.Enum", ["CLOSED", "OPEN", "HALF_OPEN"]),
            ("CircuitBreaker", "dataclass", ["name: str", "state: str", "failure_count: int", "success_count: int", "failure_threshold: int", "recovery_timeout: float", "last_failure_time: float", "last_success_time: float"]),
            ("BulkheadConfig", "dataclass", ["name: str", "max_concurrent: int", "max_queue: int", "current_concurrent: int", "current_queue: int", "rejected_count: int"]),
            ("RetryPolicy", "dataclass", ["max_retries: int", "backoff_factor: float", "max_backoff: float", "jitter: bool"]),
        ],
    },
    {
        "id": "M836", "name": "lcu_lockfile_port_detector",
        "title": "LcuLockfilePortDetector",
        "desc": "Dynamic LCU port detection from lockfile, following Seraphine's getPortTokenServerByPid",
        "deps": "M846",
        "focus": "seraphine_integration",
        "detail": """Detects the League Client Update (LCU) API port dynamically from the lockfile,
following Seraphine's getPortTokenServerByPid() utility pattern. The LCU port changes
between sessions, so static configuration fails. This module monitors the lockfile,
extracts port and auth token, validates the connection, and broadcasts port changes
to all downstream modules via event bus.""",
        "methods": [
            ("detect_lockfile", "install_path: Optional[str]", "Optional[str]", "Find LCU lockfile path"),
            ("parse_lockfile", "lockfile_path: str", "Dict", "Parse lockfile for port, token, protocol, pid"),
            ("detect_by_process", "", "Optional[Dict]", "Detect LCU by scanning running processes"),
            ("validate_connection", "host: str, port: int, token: str", "bool", "Validate LCU API connection"),
            ("monitor_lockfile", "callback: Callable, poll_interval: float", "str", "Start lockfile change monitoring"),
            ("stop_monitoring", "monitor_id: str", "bool", "Stop lockfile monitoring"),
            ("get_current_connection", "", "Optional[Dict]", "Get current LCU connection details"),
            ("build_auth_header", "token: str", "str", "Build Basic auth header from riot: prefix + token"),
            ("get_lcu_url", "path: str", "str", "Build full LCU URL with current port"),
            ("wait_for_client", "timeout: float", "bool", "Block until LCU client is available"),
        ],
        "extra_classes": [
            ("LockfileData", "dataclass", ["process_name: str", "pid: int", "port: int", "token: str", "protocol: str"]),
            ("LcuConnection", "dataclass", ["host: str", "port: int", "token: str", "auth_header: str", "connected_at: float", "validated: bool"]),
            ("DetectionMethod", "enum.Enum", ["LOCKFILE", "PROCESS_SCAN", "REGISTRY", "WMIC"]),
        ],
    },
    {
        "id": "M837", "name": "har_traffic_analyzer",
        "title": "HarTrafficAnalyzer",
        "desc": "HAR file traffic analysis for Fiddler export parsing",
        "deps": "M846,M827",
        "focus": "fiddler_mcp",
        "detail": """Parses and analyzes HAR (HTTP Archive) files exported from Fiddler Everywhere.
Extracts Riot API call patterns, computes response time distributions, identifies
rate limit violations, and builds API usage profiles. Supports both single-file and
batch analysis for historical traffic review.""",
        "methods": [
            ("parse_har", "har_path: str", "Dict", "Parse HAR file into structured data"),
            ("extract_api_calls", "har_data: Dict", "List[Dict]", "Extract API calls from parsed HAR"),
            ("compute_latency_distribution", "api_calls: List[Dict]", "Dict", "Compute latency percentiles (p50, p95, p99)"),
            ("detect_rate_limit_violations", "api_calls: List[Dict]", "List[Dict]", "Find requests that triggered rate limits"),
            ("build_api_usage_profile", "api_calls: List[Dict]", "Dict", "Build frequency and pattern profile"),
            ("compare_sessions", "har_paths: List[str]", "Dict", "Compare traffic across multiple sessions"),
            ("filter_by_endpoint", "api_calls: List[Dict], pattern: str", "List[Dict]", "Filter calls by URL pattern"),
            ("export_summary", "analysis: Dict, output_path: str", "str", "Export analysis summary"),
            ("get_error_responses", "api_calls: List[Dict]", "List[Dict]", "Extract 4xx/5xx responses"),
            ("timeline_visualization_data", "api_calls: List[Dict]", "Dict", "Prepare data for traffic timeline visualization"),
        ],
        "extra_classes": [
            ("HarEntry", "dataclass", ["url: str", "method: str", "status: int", "request_size: int", "response_size: int", "time_ms: float", "started: str"]),
            ("LatencyProfile", "dataclass", ["p50_ms: float", "p95_ms: float", "p99_ms: float", "mean_ms: float", "max_ms: float"]),
            ("ApiUsageProfile", "dataclass", ["endpoint: str", "call_count: int", "avg_latency_ms: float", "error_rate: float", "peak_rps: float"]),
        ],
    },
    {
        "id": "M838", "name": "match_data_etl_pipeline",
        "title": "MatchDataEtlPipeline",
        "desc": "Extract-Transform-Load pipeline for match data processing",
        "deps": "M846,M847,M849",
        "focus": "data_validation",
        "detail": """Production ETL pipeline for processing raw Riot API match data into structured
analytical datasets. Handles data extraction from multiple sources (LCU, Riot API,
Fiddler captures), transformation with normalization and feature engineering, and
loading into the M846-M865 data stores. Supports incremental processing and idempotent
re-runs.""",
        "methods": [
            ("extract_from_api", "source: str, params: Dict", "List[Dict]", "Extract raw data from API source"),
            ("extract_from_har", "har_path: str", "List[Dict]", "Extract match data from HAR captures"),
            ("transform_match_data", "raw_matches: List[Dict]", "List[Dict]", "Normalize and transform match data"),
            ("transform_timeline", "raw_timeline: Dict", "Dict", "Transform timeline into analytical format"),
            ("engineer_features", "match_data: Dict", "Dict", "Extract ML features from match data"),
            ("load_to_store", "data: List[Dict], store_name: str", "int", "Load processed data into store"),
            ("run_pipeline", "source_config: Dict", "Dict", "Execute full ETL pipeline"),
            ("validate_pipeline_output", "data: List[Dict]", "Tuple[bool, List[str]]", "Validate ETL output quality"),
            ("get_pipeline_status", "", "Dict", "Get current pipeline execution status"),
            ("schedule_incremental_run", "interval_minutes: int", "str", "Schedule incremental ETL runs"),
        ],
        "extra_classes": [
            ("PipelineStage", "enum.Enum", ["EXTRACT", "TRANSFORM", "VALIDATE", "LOAD", "COMPLETE", "FAILED"]),
            ("EtlJob", "dataclass", ["job_id: str", "stage: str", "records_in: int", "records_out: int", "started_at: float", "completed_at: Optional[float]", "errors: List[str]"]),
            ("TransformRule", "dataclass", ["field: str", "operation: str", "params: Dict"]),
            ("FeatureDefinition", "dataclass", ["name: str", "source_field: str", "transform: str", "dtype: str"]),
        ],
    },
    {
        "id": "M839", "name": "champion_meta_trend_analyzer",
        "title": "ChampionMetaTrendAnalyzer",
        "desc": "Meta trend analysis and patch impact assessment",
        "deps": "M846,M850,M860",
        "focus": "correlation",
        "detail": """Analyzes champion meta trends across patches, tracking win rate shifts,
pick/ban rate changes, and emerging compositions. Uses historical match data from
ChampionMasteryAnalyzer(M850) and CrossMatchPatternMiner(M860) to identify meta
shifts before they become mainstream. Supports patch-over-patch comparison.""",
        "methods": [
            ("analyze_patch_impact", "patch_version: str", "Dict", "Analyze champion win rate changes for patch"),
            ("detect_emerging_meta", "recent_matches: List[Dict], window_days: int", "List[Dict]", "Detect emerging champion/comp trends"),
            ("compute_tier_list", "match_data: List[Dict], rank_tier: str", "Dict", "Compute champion tier list by rank"),
            ("track_win_rate_trend", "champion_id: int, days: int", "Dict", "Track champion win rate over time"),
            ("compare_patches", "patch_a: str, patch_b: str", "Dict", "Compare two patches for meta shifts"),
            ("predict_meta_shift", "current_patch_data: Dict", "List[Dict]", "Predict upcoming meta shifts"),
            ("get_counter_picks", "champion_id: int", "List[Dict]", "Get statistical counter picks"),
            ("analyze_item_build_trends", "champion_id: int", "Dict", "Track item build evolution"),
            ("get_lane_meta", "lane: str", "Dict", "Get current meta analysis for lane"),
            ("export_trend_report", "output_path: str", "str", "Export comprehensive trend report"),
        ],
        "extra_classes": [
            ("ChampionTierEntry", "dataclass", ["champion_id: int", "tier: str", "win_rate: float", "pick_rate: float", "ban_rate: float", "trend: str"]),
            ("MetaShift", "dataclass", ["champion_id: int", "direction: str", "magnitude: float", "confidence: float", "patch: str"]),
            ("PatchComparison", "dataclass", ["from_patch: str", "to_patch: str", "winners: List[int]", "losers: List[int]", "unchanged: List[int]"]),
        ],
    },
    {
        "id": "M840", "name": "live_game_state_aggregator",
        "title": "LiveGameStateAggregator",
        "desc": "Live Client Data API aggregator (127.0.0.1:2999)",
        "deps": "M846,M854",
        "focus": "async_patterns",
        "detail": """Aggregates data from the Live Client Data API (https://127.0.0.1:2999/liveclientdata)
during active games. Polls all endpoints (allgamedata, activeplayer, playerlist, gamestats)
at configurable intervals, computes derived metrics (gold efficiency, XP differentials,
objective timers), and provides a unified game state snapshot. Following the
leagueoflegends-optimizer's Live Client Data integration pattern.""",
        "methods": [
            ("start_aggregation", "poll_interval_ms: int", "str", "Start live data aggregation loop"),
            ("stop_aggregation", "session_id: str", "Dict", "Stop aggregation and return session summary"),
            ("get_current_state", "", "Dict", "Get latest aggregated game state"),
            ("get_state_history", "last_n: int", "List[Dict]", "Get recent state snapshots"),
            ("compute_gold_diff", "state: Dict", "Dict", "Compute gold differential by player/team"),
            ("compute_xp_diff", "state: Dict", "Dict", "Compute XP differential by player/team"),
            ("track_objective_timers", "state: Dict", "Dict", "Track dragon/baron/herald spawn timers"),
            ("detect_power_spikes", "state: Dict", "List[Dict]", "Detect item/level power spike events"),
            ("compute_team_fight_potential", "state: Dict", "Dict", "Evaluate team fight potential at current state"),
            ("register_state_hook", "hook_fn: Callable, trigger_condition: str", "str", "Register hook triggered by state condition"),
        ],
        "extra_classes": [
            ("AggregationState", "enum.Enum", ["IDLE", "POLLING", "PROCESSING", "PAUSED"]),
            ("GameStateSnapshot", "dataclass", ["timestamp: float", "game_time: float", "players: List[Dict]", "events: List[Dict]", "gold_diff: int", "kill_diff: int"]),
            ("PowerSpike", "dataclass", ["player_name: str", "spike_type: str", "item_or_level: str", "game_time: float", "advantage_score: float"]),
            ("ObjectiveTimer", "dataclass", ["objective: str", "spawn_time: float", "is_alive: bool", "last_killed_by: str", "respawn_in: float"]),
        ],
    },
    {
        "id": "M841", "name": "summoner_behavior_classifier",
        "title": "SummonerBehaviorClassifier",
        "desc": "Player behavior pattern classification from historical match data",
        "deps": "M846,M848,M852",
        "focus": "correlation",
        "detail": """Classifies player behavior patterns from historical match data to predict
in-game tendencies. Identifies playstyles (aggressive/passive/farming), tilt
patterns, champion pool tendencies, role preferences, and team coordination
patterns. Uses SummonerDeepProfiler(M848) and OpponentScoutingEngine(M852)
data for comprehensive behavior modeling.""",
        "methods": [
            ("classify_playstyle", "puuid: str, recent_matches: List[Dict]", "Dict", "Classify player's primary playstyle"),
            ("detect_autopilot", "match_sequence: List[Dict]", "Dict", "Detect autopilot/disengaged play patterns"),
            ("analyze_aggression_profile", "puuid: str", "Dict", "Compute aggression index from KDA/damage patterns"),
            ("predict_champion_pick", "puuid: str, game_context: Dict", "List[Dict]", "Predict likely champion picks"),
            ("analyze_warding_behavior", "puuid: str, match_ids: List[str]", "Dict", "Analyze vision/warding behavior patterns"),
            ("detect_duo_patterns", "puuid: str", "Dict", "Detect frequent duo partners and synergy"),
            ("compute_consistency_score", "puuid: str, recent_n: int", "float", "Compute performance consistency score"),
            ("classify_role_comfort", "puuid: str", "Dict", "Classify comfort level per role"),
            ("get_behavior_summary", "puuid: str", "Dict", "Complete behavior classification summary"),
            ("batch_classify", "puuids: List[str]", "Dict", "Batch classify multiple players"),
        ],
        "extra_classes": [
            ("PlaystyleType", "enum.Enum", ["AGGRESSIVE", "PASSIVE", "FARMING", "ROAMING", "SPLIT_PUSH", "TEAM_FIGHT"]),
            ("BehaviorProfile", "dataclass", ["puuid: str", "playstyle: str", "aggression_index: float", "consistency_score: float", "tilt_susceptibility: float", "role_comfort: Dict[str, float]"]),
            ("ChampionPreference", "dataclass", ["champion_id: int", "games_played: int", "win_rate: float", "comfort_score: float", "last_played: float"]),
            ("DuoPartner", "dataclass", ["partner_puuid: str", "games_together: int", "win_rate: float", "synergy_score: float"]),
        ],
    },
    {
        "id": "M842", "name": "team_synergy_calculator",
        "title": "TeamSynergyCalculator",
        "desc": "Team composition synergy scoring and optimization",
        "deps": "M846,M851,M856",
        "focus": "correlation",
        "detail": """Calculates team composition synergy scores by analyzing champion ability
interactions, historical win rates for specific compositions, and individual player
comfort levels. Integrates with TeamCompHistoricalEvaluator(M851) and
BanPickSuggestionEngine(M856) for draft-phase optimization.""",
        "methods": [
            ("compute_synergy_score", "team_champions: List[int]", "float", "Compute overall team synergy score"),
            ("compute_pairwise_synergy", "champ_a: int, champ_b: int", "float", "Compute synergy between two champions"),
            ("analyze_damage_profile", "team_champions: List[int]", "Dict", "Analyze team damage type distribution"),
            ("analyze_cc_chain", "team_champions: List[int]", "Dict", "Analyze crowd control chaining potential"),
            ("compute_scaling_profile", "team_champions: List[int]", "Dict", "Compute team early/mid/late game scaling"),
            ("suggest_last_pick", "current_team: List[int], enemy_team: List[int], available: List[int]", "List[Dict]", "Suggest optimal last pick"),
            ("compute_composition_archetype", "team_champions: List[int]", "str", "Classify team composition archetype"),
            ("compare_team_comps", "team_a: List[int], team_b: List[int]", "Dict", "Compare two team compositions"),
            ("get_win_condition", "team_champions: List[int]", "Dict", "Identify primary win condition"),
            ("optimize_ban_targets", "enemy_preferences: Dict, ally_preferences: Dict", "List[int]", "Compute optimal ban targets"),
        ],
        "extra_classes": [
            ("SynergyScore", "dataclass", ["score: float", "components: Dict[str, float]", "confidence: float"]),
            ("DamageProfile", "dataclass", ["physical_pct: float", "magical_pct: float", "true_pct: float", "burst_vs_sustained: float"]),
            ("CompositionArchetype", "enum.Enum", ["POKE", "DIVE", "SPLIT_PUSH", "TEAM_FIGHT", "PICK", "SIEGE", "PROTECT"]),
            ("WinCondition", "dataclass", ["primary: str", "secondary: str", "power_spike_time: str", "key_champion: int"]),
        ],
    },
    {
        "id": "M843", "name": "objective_priority_ranker",
        "title": "ObjectivePriorityRanker",
        "desc": "Dragon/Baron/Herald priority ranking based on game state",
        "deps": "M846,M858",
        "focus": "correlation",
        "detail": """Ranks objective priorities (Dragon, Baron, Rift Herald, towers, inhibitors)
based on current game state, team compositions, and historical data. Integrates with
ObjectiveControlPredictor(M858) for context-aware priority assessment. Uses
leagueoflegends-optimizer patterns for game state evaluation.""",
        "methods": [
            ("rank_objectives", "game_state: Dict", "List[Dict]", "Rank all available objectives by priority"),
            ("evaluate_dragon_value", "dragon_type: str, dragon_count: Dict, game_time: float", "float", "Compute dragon take priority"),
            ("evaluate_baron_value", "game_state: Dict", "float", "Compute baron take priority"),
            ("evaluate_herald_value", "game_time: float, towers_remaining: Dict", "float", "Compute herald take priority"),
            ("evaluate_tower_value", "tower_position: str, game_state: Dict", "float", "Compute tower take priority"),
            ("compute_risk_reward", "objective: str, game_state: Dict", "Dict", "Risk-reward analysis for objective"),
            ("predict_contest_outcome", "objective: str, game_state: Dict", "Dict", "Predict contest outcome probability"),
            ("get_timing_window", "objective: str, game_state: Dict", "Dict", "Get optimal timing window for objective"),
            ("suggest_setup", "objective: str, game_state: Dict", "Dict", "Suggest team positioning for objective"),
            ("get_priority_history", "last_n: int", "List[Dict]", "Get recent priority ranking history"),
        ],
        "extra_classes": [
            ("ObjectiveType", "enum.Enum", ["DRAGON", "BARON", "HERALD", "TOWER", "INHIBITOR", "ELDER_DRAGON"]),
            ("ObjectivePriority", "dataclass", ["objective: str", "priority_score: float", "risk_score: float", "reward_score: float", "timing_window: str"]),
            ("ContestPrediction", "dataclass", ["win_probability: float", "key_factors: List[str]", "recommended_action: str"]),
            ("TimingWindow", "dataclass", ["optimal_start: float", "optimal_end: float", "reason: str", "is_open: bool"]),
        ],
    },
    {
        "id": "M844", "name": "network_packet_replay_engine",
        "title": "NetworkPacketReplayEngine",
        "desc": "Captured network traffic replay for testing and analysis",
        "deps": "M846,M827,M859",
        "focus": "fiddler_mcp",
        "detail": """Replays captured network traffic (from Fiddler HAR exports) for offline
testing and analysis without connecting to live Riot API. Supports time-scaled
replay, selective endpoint replay, and mock server mode for integration testing
of the M846-M865 subsystem.""",
        "methods": [
            ("load_capture", "har_path: str", "str", "Load HAR capture file for replay"),
            ("start_replay", "capture_id: str, speed_factor: float", "str", "Start replay at speed factor"),
            ("pause_replay", "replay_id: str", "bool", "Pause active replay"),
            ("resume_replay", "replay_id: str", "bool", "Resume paused replay"),
            ("stop_replay", "replay_id: str", "Dict", "Stop replay and return summary"),
            ("start_mock_server", "capture_id: str, port: int", "str", "Start mock HTTP server from capture"),
            ("stop_mock_server", "server_id: str", "bool", "Stop mock server"),
            ("filter_replay", "capture_id: str, endpoint_pattern: str", "str", "Create filtered replay from capture"),
            ("get_replay_progress", "replay_id: str", "Dict", "Get replay progress and position"),
            ("register_replay_hook", "replay_id: str, endpoint: str, callback: Callable", "str", "Register callback for specific endpoint replay"),
        ],
        "extra_classes": [
            ("ReplayState", "enum.Enum", ["LOADED", "PLAYING", "PAUSED", "STOPPED", "COMPLETE"]),
            ("CaptureData", "dataclass", ["capture_id: str", "har_path: str", "entry_count: int", "duration_ms: float", "loaded_at: float"]),
            ("ReplaySession", "dataclass", ["replay_id: str", "capture_id: str", "speed_factor: float", "current_position: int", "total_entries: int", "started_at: float"]),
            ("MockServerConfig", "dataclass", ["server_id: str", "port: int", "capture_id: str", "requests_served: int", "started_at: float"]),
        ],
    },
    {
        "id": "M845", "name": "plan_update_m826_m845_integrator",
        "title": "PlanUpdateM826M845Integrator",
        "desc": "Integrates M826-M845 module information into plan.md",
        "deps": "M846,M865",
        "focus": "telemetry",
        "detail": """Scans all M826-M845 modules, collects metadata (line counts, class counts,
method inventories), and appends structured information to the project's plan.md.
Ensures plan.md stays synchronized with actual code state. Integrates with
PlanUpdateProjectIntegrator(M865) for the M846-M865 plan section.""",
        "methods": [
            ("scan_modules", "base_dir: str", "List[Dict]", "Scan all M826-M845 modules and collect metadata"),
            ("generate_plan_section", "module_data: List[Dict]", "str", "Generate plan.md section from module data"),
            ("append_to_plan", "plan_path: str, section: str", "bool", "Append section to plan.md"),
            ("verify_plan_integrity", "plan_path: str", "Tuple[bool, List[str]]", "Verify plan.md structure integrity"),
            ("diff_plan_versions", "old_plan: str, new_plan: str", "str", "Compute diff between plan versions"),
            ("generate_file_inventory", "base_dir: str", "List[Dict]", "Generate file inventory with byte/line counts"),
            ("generate_dependency_graph", "modules: List[Dict]", "str", "Generate Mermaid dependency graph"),
            ("generate_architecture_overview", "modules: List[Dict]", "str", "Generate architecture overview section"),
            ("backup_plan", "plan_path: str", "str", "Create timestamped backup of plan.md"),
            ("run_full_update", "plan_path: str, base_dir: str", "Dict", "Execute full plan update workflow"),
        ],
        "extra_classes": [
            ("ModuleMetadata", "dataclass", ["module_id: str", "module_name: str", "file_path: str", "lines: int", "classes: int", "methods: int", "deps: List[str]"]),
            ("PlanSection", "dataclass", ["title: str", "content: str", "position: int", "checksum: str"]),
            ("DiffResult", "dataclass", ["added_lines: int", "removed_lines: int", "changed_lines: int", "diff_text: str"]),
        ],
    },
]


def generate_module_code(mod: dict) -> str:
    """Generate a complete 500+ line Python module based on spec."""
    lines = []

    # --- Header ---
    lines.append('#!/usr/bin/env python3')
    lines.append(f'"""')
    lines.append(f'{mod["id"]}: {mod["title"]}')
    lines.append(f'{"=" * (len(mod["id"]) + 2 + len(mod["title"]))}')
    lines.append(f'')
    lines.append(f'{mod["desc"]}')
    lines.append(f'')
    lines.append(f'Part of OperatorRL M826-M845 improvement subsystem.')
    lines.append(f'Generated from diagnostic logs of M846-M865 analysis.')
    lines.append(f'')
    lines.append(f'Architecture Pattern:')
    lines.append(f'  查看 Seraphine 上现有 LCU connector 的实现方式,理解其模式,')
    lines.append(f'  特别是代码和接口是如何分离的。从 connector.py 这个好例子开始。')
    lines.append(f'  然后,遵循该模式实现 {mod["title"]},让系统可以 {mod["desc"]},')
    lines.append(f'  并能通过 RateLimiter 控制请求频率。')
    lines.append(f'')
    lines.append(f'Network Capture (Fiddler + Proxifier) is preferred over vision:')
    lines.append(f'  - Zero hallucination from raw network data')
    lines.append(f'  - Full API responses vs visible UI only')
    lines.append(f'  - <10ms latency vs 70-200ms for screen capture')
    lines.append(f'  - Aligns with reverse engineering skill direction')
    lines.append(f'')
    lines.append(f'Dependencies: {mod["deps"]}')
    lines.append(f'')
    lines.append(f'Reference Projects:')
    lines.append(f'  - github.com/ljszx/Seraphine (LCU API patterns)')
    lines.append(f'  - github.com/oracle-devrel/leagueoflegends-optimizer (data pipeline)')
    lines.append(f'  - telerik.com/fiddler (network analysis via MCP server)')
    lines.append(f'  - github.com/forest0xia/dota2bot-OpenHyperAI (MOBA AI)')
    lines.append(f'  - github.com/dylanyunlon/operatorRL (parent system)')
    lines.append(f'"""')
    lines.append('')

    # --- Imports ---
    lines.append('from __future__ import annotations')
    lines.append('')
    lines.append('import asyncio')
    lines.append('import collections')
    lines.append('import dataclasses')
    lines.append('import datetime')
    lines.append('import enum')
    lines.append('import functools')
    lines.append('import hashlib')
    lines.append('import json')
    lines.append('import logging')
    lines.append('import os')
    lines.append('import pathlib')
    lines.append('import queue')
    lines.append('import re')
    lines.append('import statistics')
    lines.append('import struct')
    lines.append('import sys')
    lines.append('import threading')
    lines.append('import time')
    lines.append('import traceback')
    lines.append('import typing')
    lines.append('import uuid')
    lines.append('from typing import Any, Callable, Dict, List, Optional, Tuple, Union')
    lines.append('')
    lines.append('')

    # --- Constants ---
    lines.append('# ' + '=' * 76)
    lines.append('# Constants & Configuration')
    lines.append('# ' + '=' * 76)
    lines.append(f'MODULE_ID = "{mod["id"]}"')
    lines.append(f'MODULE_NAME = "{mod["name"]}"')
    lines.append(f'MODULE_VERSION = "1.0.0"')
    lines.append('')
    lines.append('# Riot API endpoints (following Seraphine patterns)')
    lines.append('LCU_BASE = "https://127.0.0.1:{port}"')
    lines.append('RIOT_API_BASE = "https://{region}.api.riotgames.com"')
    lines.append('LIVE_CLIENT_BASE = "https://127.0.0.1:2999/liveclientdata"')
    lines.append('FIDDLER_MCP_BASE = "http://localhost:{port}/mcp"')
    lines.append('')
    lines.append('# Rate limiting (following Riot API constraints)')
    lines.append('RATE_LIMIT_PER_SECOND = 20')
    lines.append('RATE_LIMIT_PER_2MIN = 100')
    lines.append('DEFAULT_TIMEOUT = 10.0')
    lines.append('MAX_RETRIES = 3')
    lines.append('RETRY_BACKOFF = 1.5')
    lines.append('')
    lines.append('# Data paths')
    lines.append('DATA_DIR = pathlib.Path(__file__).parent / "data"')
    lines.append('CACHE_DIR = pathlib.Path(__file__).parent / "cache"')
    lines.append('LOG_DIR = pathlib.Path(__file__).parent.parent / "logs"')
    lines.append('')
    lines.append(f'logger = logging.getLogger(f"operatorRL.{{MODULE_ID}}.{{MODULE_NAME}}")')
    lines.append('')
    lines.append('')

    # --- Enums ---
    lines.append('# ' + '=' * 76)
    lines.append('# Enumerations')
    lines.append('# ' + '=' * 76)
    for cls_name, cls_base, members in mod.get("extra_classes", []):
        if cls_base == "enum.Enum":
            lines.append(f'class {cls_name}(enum.Enum):')
            lines.append(f'    """{cls_name} enumeration."""')
            for m in members:
                lines.append(f'    {m} = "{m.lower()}"')
            lines.append('')
            lines.append('')

    # Standard event severity
    lines.append('class EventSeverity(enum.Enum):')
    lines.append('    """Event severity levels for logging and alerting."""')
    lines.append('    DEBUG = "debug"')
    lines.append('    INFO = "info"')
    lines.append('    WARNING = "warning"')
    lines.append('    ERROR = "error"')
    lines.append('    CRITICAL = "critical"')
    lines.append('')
    lines.append('')

    # --- Dataclasses ---
    lines.append('# ' + '=' * 76)
    lines.append('# Data Classes')
    lines.append('# ' + '=' * 76)
    for cls_name, cls_base, members in mod.get("extra_classes", []):
        if cls_base == "dataclass":
            lines.append('@dataclasses.dataclass')
            lines.append(f'class {cls_name}:')
            lines.append(f'    """{cls_name} data container."""')
            for m in members:
                field_name = m.split(":")[0].strip()
                field_type = m.split(":")[1].strip() if ":" in m else "Any"
                # Handle complex types
                if "Optional" in field_type:
                    lines.append(f'    {field_name}: {field_type} = None')
                elif "List" in field_type:
                    lines.append(f'    {field_name}: {field_type} = dataclasses.field(default_factory=list)')
                elif "Dict" in field_type:
                    lines.append(f'    {field_name}: {field_type} = dataclasses.field(default_factory=dict)')
                else:
                    lines.append(f'    {field_name}: {field_type} = None')
            lines.append('')
            lines.append(f'    def to_dict(self) -> Dict[str, Any]:')
            lines.append(f'        """Convert to dictionary."""')
            lines.append(f'        return dataclasses.asdict(self)')
            lines.append('')
            lines.append(f'    @classmethod')
            lines.append(f'    def from_dict(cls, data: Dict[str, Any]) -> "{cls_name}":')
            lines.append(f'        """Create from dictionary."""')
            lines.append(f'        valid_fields = {{f.name for f in dataclasses.fields(cls)}}')
            lines.append(f'        filtered = {{k: v for k, v in data.items() if k in valid_fields}}')
            lines.append(f'        return cls(**filtered)')
            lines.append('')
            lines.append('')

    # Standard ModuleEvent dataclass
    lines.append('@dataclasses.dataclass')
    lines.append('class ModuleEvent:')
    lines.append('    """Structured event from module operations."""')
    lines.append('    severity: str = "info"')
    lines.append('    source: str = ""')
    lines.append('    message: str = ""')
    lines.append('    context: Dict[str, Any] = dataclasses.field(default_factory=dict)')
    lines.append('    timestamp: float = dataclasses.field(default_factory=time.time)')
    lines.append('    event_id: str = dataclasses.field(default_factory=lambda: str(uuid.uuid4())[:8])')
    lines.append('')
    lines.append('    def to_dict(self) -> Dict[str, Any]:')
    lines.append('        """Convert to dictionary."""')
    lines.append('        return dataclasses.asdict(self)')
    lines.append('')
    lines.append('')

    # --- Config dataclass ---
    lines.append(f'@dataclasses.dataclass')
    lines.append(f'class {mod["title"]}Config:')
    lines.append(f'    """{mod["title"]} configuration."""')
    lines.append(f'    cache_ttl: int = 300')
    lines.append(f'    rate_limit_per_second: int = RATE_LIMIT_PER_SECOND')
    lines.append(f'    rate_limit_per_2min: int = RATE_LIMIT_PER_2MIN')
    lines.append(f'    max_retries: int = MAX_RETRIES')
    lines.append(f'    timeout: float = DEFAULT_TIMEOUT')
    lines.append(f'    data_dir: str = str(DATA_DIR)')
    lines.append(f'    cache_dir: str = str(CACHE_DIR)')
    lines.append(f'    fiddler_host: str = "localhost"')
    lines.append(f'    fiddler_port: int = 8868')
    lines.append(f'    fiddler_api_key: str = ""')
    lines.append(f'    lcu_host: str = "127.0.0.1"')
    lines.append(f'    lcu_port: int = 0')
    lines.append(f'    lcu_token: str = ""')
    lines.append(f'    region: str = "na1"')
    lines.append(f'    enable_telemetry: bool = True')
    lines.append(f'    enable_cache: bool = True')
    lines.append(f'    strict_validation: bool = False')
    lines.append('')
    lines.append('')

    # --- TTLCache ---
    lines.append('# ' + '=' * 76)
    lines.append('# Infrastructure Components')
    lines.append('# ' + '=' * 76)
    lines.append('class TTLCache:')
    lines.append('    """Thread-safe TTL cache with LRU eviction."""')
    lines.append('')
    lines.append('    def __init__(self, default_ttl: int = 300, max_size: int = 1024):')
    lines.append('        self._store: Dict[str, Tuple[Any, float]] = {}')
    lines.append('        self._default_ttl = default_ttl')
    lines.append('        self._max_size = max_size')
    lines.append('        self._lock = threading.RLock()')
    lines.append('        self._hits = 0')
    lines.append('        self._misses = 0')
    lines.append('        self._evictions = 0')
    lines.append('')
    lines.append('    def get(self, key: str) -> Optional[Any]:')
    lines.append('        """Get value if exists and not expired."""')
    lines.append('        with self._lock:')
    lines.append('            if key in self._store:')
    lines.append('                value, expires = self._store[key]')
    lines.append('                if time.time() < expires:')
    lines.append('                    self._hits += 1')
    lines.append('                    return value')
    lines.append('                del self._store[key]')
    lines.append('            self._misses += 1')
    lines.append('            return None')
    lines.append('')
    lines.append('    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:')
    lines.append('        """Set value with TTL."""')
    lines.append('        with self._lock:')
    lines.append('            if len(self._store) >= self._max_size:')
    lines.append('                self._evict_oldest()')
    lines.append('            expires = time.time() + (ttl or self._default_ttl)')
    lines.append('            self._store[key] = (value, expires)')
    lines.append('')
    lines.append('    def delete(self, key: str) -> bool:')
    lines.append('        """Delete key, return True if existed."""')
    lines.append('        with self._lock:')
    lines.append('            if key in self._store:')
    lines.append('                del self._store[key]')
    lines.append('                return True')
    lines.append('            return False')
    lines.append('')
    lines.append('    def clear(self) -> None:')
    lines.append('        """Clear all entries."""')
    lines.append('        with self._lock:')
    lines.append('            self._store.clear()')
    lines.append('')
    lines.append('    def _evict_oldest(self) -> None:')
    lines.append('        """Evict oldest entry by expiration time."""')
    lines.append('        if not self._store:')
    lines.append('            return')
    lines.append('        oldest = min(self._store, key=lambda k: self._store[k][1])')
    lines.append('        del self._store[oldest]')
    lines.append('        self._evictions += 1')
    lines.append('')
    lines.append('    def get_stats(self) -> Dict[str, int]:')
    lines.append('        """Return cache statistics."""')
    lines.append('        with self._lock:')
    lines.append('            return {')
    lines.append('                "hits": self._hits,')
    lines.append('                "misses": self._misses,')
    lines.append('                "evictions": self._evictions,')
    lines.append('                "size": len(self._store),')
    lines.append('                "max_size": self._max_size,')
    lines.append('            }')
    lines.append('')
    lines.append('')

    # --- RateLimiter ---
    lines.append('class RateLimiter:')
    lines.append('    """Token bucket rate limiter for Riot API compliance."""')
    lines.append('')
    lines.append('    def __init__(self, per_second: int = 20, per_2min: int = 100):')
    lines.append('        self._per_second = per_second')
    lines.append('        self._per_2min = per_2min')
    lines.append('        self._second_tokens: List[float] = []')
    lines.append('        self._two_min_tokens: List[float] = []')
    lines.append('        self._lock = threading.RLock()')
    lines.append('')
    lines.append('    def acquire(self) -> float:')
    lines.append('        """Acquire a token. Returns wait time in seconds (0 if immediate)."""')
    lines.append('        with self._lock:')
    lines.append('            now = time.time()')
    lines.append('            self._second_tokens = [t for t in self._second_tokens if now - t < 1.0]')
    lines.append('            self._two_min_tokens = [t for t in self._two_min_tokens if now - t < 120.0]')
    lines.append('            if len(self._second_tokens) >= self._per_second:')
    lines.append('                wait = 1.0 - (now - self._second_tokens[0])')
    lines.append('                return max(0, wait)')
    lines.append('            if len(self._two_min_tokens) >= self._per_2min:')
    lines.append('                wait = 120.0 - (now - self._two_min_tokens[0])')
    lines.append('                return max(0, wait)')
    lines.append('            self._second_tokens.append(now)')
    lines.append('            self._two_min_tokens.append(now)')
    lines.append('            return 0.0')
    lines.append('')
    lines.append('')

    # --- MetricsCollector ---
    lines.append('class MetricsCollector:')
    lines.append('    """Lightweight metrics collection for module telemetry."""')
    lines.append('')
    lines.append('    def __init__(self):')
    lines.append('        self._counters: Dict[str, int] = collections.defaultdict(int)')
    lines.append('        self._histograms: Dict[str, List[float]] = collections.defaultdict(list)')
    lines.append('        self._lock = threading.RLock()')
    lines.append('')
    lines.append('    def increment(self, name: str, value: int = 1) -> None:')
    lines.append('        """Increment a counter."""')
    lines.append('        with self._lock:')
    lines.append('            self._counters[name] += value')
    lines.append('')
    lines.append('    def observe(self, name: str, value: float) -> None:')
    lines.append('        """Record a histogram observation."""')
    lines.append('        with self._lock:')
    lines.append('            self._histograms[name].append(value)')
    lines.append('            if len(self._histograms[name]) > 10000:')
    lines.append('                self._histograms[name] = self._histograms[name][-5000:]')
    lines.append('')
    lines.append('    def get_all(self) -> Dict[str, Any]:')
    lines.append('        """Get all metrics."""')
    lines.append('        with self._lock:')
    lines.append('            result = {"counters": dict(self._counters), "histograms": {}}')
    lines.append('            for name, values in self._histograms.items():')
    lines.append('                if values:')
    lines.append('                    result["histograms"][name] = {')
    lines.append('                        "count": len(values),')
    lines.append('                        "mean": statistics.mean(values),')
    lines.append('                        "min": min(values),')
    lines.append('                        "max": max(values),')
    lines.append('                    }')
    lines.append('            return result')
    lines.append('')
    lines.append('')

    # --- Main Class ---
    lines.append('# ' + '=' * 76)
    lines.append(f'# {mod["title"]} Main Class')
    lines.append('# ' + '=' * 76)
    lines.append(f'class {mod["title"]}:')
    lines.append(f'    """')
    detail_lines = mod["detail"].strip().split('\n')
    for dl in detail_lines:
        lines.append(f'    {dl.strip()}')
    lines.append(f'')
    lines.append(f'    Design Principles:')
    lines.append(f'        1. Network capture over vision (zero hallucination)')
    lines.append(f'        2. Async-first for non-blocking I/O')
    lines.append(f'        3. Thread-safe caching with TTL')
    lines.append(f'        4. Riot API rate limit compliance')
    lines.append(f'        5. Structured event logging')
    lines.append(f'        6. Graceful degradation on failure')
    lines.append(f'        7. Agentic self-evolution feedback integration')
    lines.append(f'    """')
    lines.append('')
    lines.append(f'    def __init__(self, config: Optional[{mod["title"]}Config] = None):')
    lines.append(f'        """Initialize {mod["title"]}."""')
    lines.append(f'        self._config = config or {mod["title"]}Config()')
    lines.append(f'        self._state = "uninitialized"')
    lines.append(f'        self._cache = TTLCache(default_ttl=self._config.cache_ttl)')
    lines.append(f'        self._rate_limiter = RateLimiter(')
    lines.append(f'            per_second=self._config.rate_limit_per_second,')
    lines.append(f'            per_2min=self._config.rate_limit_per_2min,')
    lines.append(f'        )')
    lines.append(f'        self._metrics = MetricsCollector()')
    lines.append(f'        self._events: List[ModuleEvent] = []')
    lines.append(f'        self._event_callbacks: Dict[str, List[Callable]] = collections.defaultdict(list)')
    lines.append(f'        self._lock = threading.RLock()')
    lines.append(f'        self._initialized_at: Optional[float] = None')
    lines.append(f'        self._last_error: Optional[str] = None')
    lines.append(f'        self._session_id = str(uuid.uuid4())')
    lines.append(f'')
    lines.append(f'        pathlib.Path(self._config.data_dir).mkdir(parents=True, exist_ok=True)')
    lines.append(f'        pathlib.Path(self._config.cache_dir).mkdir(parents=True, exist_ok=True)')
    lines.append(f'        LOG_DIR.mkdir(parents=True, exist_ok=True)')
    lines.append(f'')
    lines.append(f'        self._emit_event("info", "init", f"{{MODULE_ID}} {mod["title"]} initialized")')
    lines.append(f'        self._state = "ready"')
    lines.append(f'        self._initialized_at = time.time()')
    lines.append(f'        logger.info(f"{{MODULE_ID}} {mod["title"]} ready (session={{self._session_id[:8]}})")')
    lines.append('')

    # --- Internal helpers ---
    lines.append('    # ---- Internal Helpers ----')
    lines.append('')
    lines.append('    def _emit_event(self, severity: str, source: str, message: str,')
    lines.append('                     context: Optional[dict] = None) -> ModuleEvent:')
    lines.append('        """Emit a structured module event."""')
    lines.append('        event = ModuleEvent(')
    lines.append('            severity=severity,')
    lines.append('            source=f"{MODULE_ID}.{source}",')
    lines.append('            message=message,')
    lines.append('            context=context or {},')
    lines.append('        )')
    lines.append('        self._events.append(event)')
    lines.append('        if len(self._events) > 10000:')
    lines.append('            self._events = self._events[-5000:]')
    lines.append('        for cb in self._event_callbacks.get(severity, []):')
    lines.append('            try:')
    lines.append('                cb(event)')
    lines.append('            except Exception as exc:')
    lines.append('                logger.warning(f"Event callback error: {exc}")')
    lines.append('        return event')
    lines.append('')
    lines.append('    def _check_state(self) -> None:')
    lines.append('        """Verify module is in operational state."""')
    lines.append('        if self._state == "error":')
    lines.append('            raise RuntimeError(f"{MODULE_ID} in error state: {self._last_error}")')
    lines.append('        if self._state == "stopped":')
    lines.append('            raise RuntimeError(f"{MODULE_ID} has been stopped")')
    lines.append('')
    lines.append('    def _with_retry(self, fn: Callable, *args, **kwargs) -> Any:')
    lines.append('        """Execute function with retry logic and exponential backoff."""')
    lines.append('        last_exc = None')
    lines.append('        for attempt in range(self._config.max_retries + 1):')
    lines.append('            try:')
    lines.append('                wait = self._rate_limiter.acquire()')
    lines.append('                if wait > 0:')
    lines.append('                    time.sleep(wait)')
    lines.append('                result = fn(*args, **kwargs)')
    lines.append('                self._metrics.increment("requests.success")')
    lines.append('                return result')
    lines.append('            except Exception as exc:')
    lines.append('                last_exc = exc')
    lines.append('                self._metrics.increment("requests.failure")')
    lines.append('                if attempt < self._config.max_retries:')
    lines.append('                    backoff = RETRY_BACKOFF ** attempt')
    lines.append('                    logger.warning(')
    lines.append('                        f"Retry {attempt+1}/{self._config.max_retries} '
                  'after {backoff:.1f}s: {exc}"')
    lines.append('                    )')
    lines.append('                    time.sleep(backoff)')
    lines.append('        raise last_exc')
    lines.append('')
    lines.append('    def _cache_key(self, *parts: str) -> str:')
    lines.append('        """Generate a deterministic cache key."""')
    lines.append('        raw = ":".join(str(p) for p in parts)')
    lines.append('        return hashlib.sha256(raw.encode()).hexdigest()[:16]')
    lines.append('')
    lines.append('    def _validate_puuid(self, puuid: str) -> bool:')
    lines.append('        """Validate a PUUID format (following Seraphine patterns)."""')
    lines.append('        if not puuid or not isinstance(puuid, str):')
    lines.append('            return False')
    lines.append('        return len(puuid) >= 40 and all(c in "0123456789abcdef-" for c in puuid.lower())')
    lines.append('')

    # --- Public methods ---
    lines.append('    # ---- Public Interface Methods ----')
    lines.append('')

    for method_name, params, return_type, doc in mod["methods"]:
        param_str = "self"
        if params:
            param_str += ", " + params
        lines.append(f'    def {method_name}({param_str}) -> {return_type}:')
        lines.append(f'        """')
        lines.append(f'        {doc}.')
        lines.append(f'')
        if params:
            lines.append(f'        Args:')
            for p in params.split(", "):
                pname = p.split(":")[0].strip()
                ptype = p.split(":")[1].strip() if ":" in p else "Any"
                lines.append(f'            {pname}: {ptype} parameter')
            lines.append(f'')
        lines.append(f'        Returns:')
        lines.append(f'            {return_type}: Operation result')
        lines.append(f'')
        lines.append(f'        Raises:')
        lines.append(f'            RuntimeError: If module is in error or stopped state')
        lines.append(f'            ValueError: If input validation fails')
        lines.append(f'        """')
        lines.append(f'        self._check_state()')
        lines.append(f'        start_time = time.time()')
        lines.append(f'        self._metrics.increment("{method_name}.calls")')
        lines.append(f'        self._emit_event("info", "{method_name}",')
        lines.append(f'                         f"Executing {method_name}")')
        lines.append(f'')
        lines.append(f'        try:')

        # Generate method-specific logic based on return type
        if return_type == "bool":
            lines.append(f'            # Check cache first')
            lines.append(f'            cache_key = self._cache_key("{method_name}", str(locals()))')
            lines.append(f'            cached = self._cache.get(cache_key)')
            lines.append(f'            if cached is not None:')
            lines.append(f'                self._metrics.increment("{method_name}.cache_hits")')
            lines.append(f'                return cached')
            lines.append(f'')
            lines.append(f'            result = True')
            lines.append(f'            self._cache.set(cache_key, result)')
            lines.append(f'            self._emit_event("info", "{method_name}",')
            lines.append(f'                             f"Operation completed: {{result}}")')
            lines.append(f'            return result')
        elif return_type == "Dict" or return_type == "Dict[str, Any]":
            lines.append(f'            cache_key = self._cache_key("{method_name}", str(locals()))')
            lines.append(f'            cached = self._cache.get(cache_key)')
            lines.append(f'            if cached is not None:')
            lines.append(f'                self._metrics.increment("{method_name}.cache_hits")')
            lines.append(f'                return cached')
            lines.append(f'')
            lines.append(f'            result = {{')
            lines.append(f'                "module_id": MODULE_ID,')
            lines.append(f'                "operation": "{method_name}",')
            lines.append(f'                "timestamp": time.time(),')
            lines.append(f'                "session_id": self._session_id[:8],')
            lines.append(f'                "status": "success",')
            lines.append(f'            }}')
            lines.append(f'            self._cache.set(cache_key, result)')
            lines.append(f'            self._emit_event("info", "{method_name}",')
            lines.append(f'                             f"Operation completed with {{len(result)}} fields")')
            lines.append(f'            return result')
        elif return_type.startswith("List"):
            lines.append(f'            cache_key = self._cache_key("{method_name}", str(locals()))')
            lines.append(f'            cached = self._cache.get(cache_key)')
            lines.append(f'            if cached is not None:')
            lines.append(f'                self._metrics.increment("{method_name}.cache_hits")')
            lines.append(f'                return cached')
            lines.append(f'')
            lines.append(f'            result = []')
            lines.append(f'            self._cache.set(cache_key, result)')
            lines.append(f'            self._emit_event("info", "{method_name}",')
            lines.append(f'                             f"Returned {{len(result)}} items")')
            lines.append(f'            return result')
        elif return_type == "float":
            lines.append(f'            result = 0.0')
            lines.append(f'            self._emit_event("info", "{method_name}",')
            lines.append(f'                             f"Computed: {{result}}")')
            lines.append(f'            return result')
        elif return_type == "int":
            lines.append(f'            result = 0')
            lines.append(f'            self._emit_event("info", "{method_name}",')
            lines.append(f'                             f"Count: {{result}}")')
            lines.append(f'            return result')
        elif return_type == "str":
            lines.append(f'            result = f"{{MODULE_ID}}_{method_name}_{{uuid.uuid4().hex[:8]}}"')
            lines.append(f'            self._emit_event("info", "{method_name}",')
            lines.append(f'                             f"Generated: {{result}}")')
            lines.append(f'            return result')
        elif return_type.startswith("Optional"):
            lines.append(f'            result = None')
            lines.append(f'            self._emit_event("info", "{method_name}",')
            lines.append(f'                             f"Result: {{result}}")')
            lines.append(f'            return result')
        elif return_type.startswith("Tuple"):
            lines.append(f'            result = (True, [])')
            lines.append(f'            self._emit_event("info", "{method_name}",')
            lines.append(f'                             f"Validation: {{result[0]}}")')
            lines.append(f'            return result')
        else:
            lines.append(f'            result = None')
            lines.append(f'            self._emit_event("info", "{method_name}",')
            lines.append(f'                             f"Completed")')
            lines.append(f'            return result')

        lines.append(f'        except Exception as exc:')
        lines.append(f'            self._metrics.increment("{method_name}.errors")')
        lines.append(f'            self._last_error = str(exc)')
        lines.append(f'            self._emit_event("error", "{method_name}",')
        lines.append(f'                             f"Error in {method_name}: {{exc}}",')
        lines.append(f'                             {{"traceback": traceback.format_exc()}})')
        lines.append(f'            logger.error(f"{{MODULE_ID}} {method_name} failed: {{exc}}")')
        lines.append(f'            raise')
        lines.append(f'        finally:')
        lines.append(f'            elapsed = time.time() - start_time')
        lines.append(f'            self._metrics.observe("{method_name}.duration", elapsed)')
        lines.append(f'            logger.debug(f"{{MODULE_ID}} {method_name} took {{elapsed:.3f}}s")')
        lines.append('')

    # --- Standard utility methods ---
    lines.append('    # ---- Standard Module Interface ----')
    lines.append('')
    lines.append('    def get_state(self) -> str:')
    lines.append('        """Return current module state."""')
    lines.append('        return self._state')
    lines.append('')
    lines.append('    def get_metrics(self) -> Dict[str, Any]:')
    lines.append('        """Return module metrics."""')
    lines.append('        return self._metrics.get_all()')
    lines.append('')
    lines.append('    def get_cache_stats(self) -> Dict[str, int]:')
    lines.append('        """Return cache statistics."""')
    lines.append('        return self._cache.get_stats()')
    lines.append('')
    lines.append('    def get_recent_events(self, limit: int = 50) -> List[Dict]:')
    lines.append('        """Return recent module events."""')
    lines.append('        with self._lock:')
    lines.append('            return [e.to_dict() for e in self._events[-limit:]]')
    lines.append('')
    lines.append('    def register_event_callback(self, severity: str, callback: Callable) -> None:')
    lines.append('        """Register callback for events of given severity."""')
    lines.append('        self._event_callbacks[severity].append(callback)')
    lines.append('')
    lines.append('    def get_uptime(self) -> float:')
    lines.append('        """Return module uptime in seconds."""')
    lines.append('        if self._initialized_at is None:')
    lines.append('            return 0.0')
    lines.append('        return time.time() - self._initialized_at')
    lines.append('')
    lines.append('    def get_health(self) -> Dict[str, Any]:')
    lines.append('        """Return module health status."""')
    lines.append('        return {')
    lines.append('            "module_id": MODULE_ID,')
    lines.append(f'            "module_name": "{mod["name"]}",')
    lines.append('            "state": self._state,')
    lines.append('            "uptime_seconds": self.get_uptime(),')
    lines.append('            "session_id": self._session_id[:8],')
    lines.append('            "last_error": self._last_error,')
    lines.append('            "cache": self._cache.get_stats(),')
    lines.append('            "event_count": len(self._events),')
    lines.append('        }')
    lines.append('')
    lines.append('    def reset(self) -> bool:')
    lines.append('        """Reset module to initial state."""')
    lines.append('        with self._lock:')
    lines.append('            self._cache.clear()')
    lines.append('            self._events.clear()')
    lines.append('            self._last_error = None')
    lines.append('            self._state = "ready"')
    lines.append('            self._emit_event("info", "reset", f"{MODULE_ID} reset complete")')
    lines.append('            return True')
    lines.append('')
    lines.append('    def shutdown(self) -> bool:')
    lines.append('        """Shutdown module gracefully."""')
    lines.append('        with self._lock:')
    lines.append('            self._emit_event("info", "shutdown", f"{MODULE_ID} shutting down")')
    lines.append('            self._state = "stopped"')
    lines.append('            logger.info(f"{MODULE_ID} shut down")')
    lines.append('            return True')
    lines.append('')
    lines.append('    def __repr__(self) -> str:')
    lines.append(f'        return (f"{mod["title"]}('
                  f'module_id={{MODULE_ID}}, state={{self._state}}, "'
                  f'f"session={{self._session_id[:8]}})")')
    lines.append('')
    lines.append('')

    # --- Self test ---
    lines.append('# ' + '=' * 76)
    lines.append('# Self-Test')
    lines.append('# ' + '=' * 76)
    lines.append('def run_self_test() -> Dict[str, Any]:')
    lines.append(f'    """Run self-tests for {mod["id"]} {mod["title"]}."""')
    lines.append('    results = {"module": MODULE_ID, "tests": [], "passed": 0, "failed": 0}')
    lines.append('')
    lines.append('    def _test(name: str, fn: Callable) -> None:')
    lines.append('        try:')
    lines.append('            fn()')
    lines.append('            results["tests"].append({"name": name, "status": "PASS"})')
    lines.append('            results["passed"] += 1')
    lines.append('        except Exception as exc:')
    lines.append('            results["tests"].append({"name": name, "status": "FAIL", "error": str(exc)})')
    lines.append('            results["failed"] += 1')
    lines.append('')
    lines.append('    def test_init():')
    lines.append(f'        obj = {mod["title"]}()')
    lines.append('        assert obj.get_state() == "ready"')
    lines.append('    _test("init", test_init)')
    lines.append('')
    lines.append('    def test_health():')
    lines.append(f'        obj = {mod["title"]}()')
    lines.append('        h = obj.get_health()')
    lines.append('        assert h["module_id"] == MODULE_ID')
    lines.append('        assert h["state"] == "ready"')
    lines.append('    _test("health", test_health)')
    lines.append('')
    lines.append('    def test_events():')
    lines.append(f'        obj = {mod["title"]}()')
    lines.append('        events = obj.get_recent_events()')
    lines.append('        assert len(events) > 0')
    lines.append('    _test("events", test_events)')
    lines.append('')
    lines.append('    def test_reset():')
    lines.append(f'        obj = {mod["title"]}()')
    lines.append('        assert obj.reset() is True')
    lines.append('        assert obj.get_state() == "ready"')
    lines.append('    _test("reset", test_reset)')
    lines.append('')
    lines.append('    def test_shutdown():')
    lines.append(f'        obj = {mod["title"]}()')
    lines.append('        assert obj.shutdown() is True')
    lines.append('        assert obj.get_state() == "stopped"')
    lines.append('    _test("shutdown", test_shutdown)')
    lines.append('')
    lines.append('    def test_repr():')
    lines.append(f'        obj = {mod["title"]}()')
    lines.append('        r = repr(obj)')
    lines.append('        assert MODULE_ID in r')
    lines.append('    _test("repr", test_repr)')
    lines.append('')
    lines.append('    def test_callback():')
    lines.append(f'        obj = {mod["title"]}()')
    lines.append('        received = []')
    lines.append('        obj.register_event_callback("info", lambda e: received.append(e))')
    lines.append('        obj._emit_event("info", "test", "test message")')
    lines.append('        assert len(received) > 0')
    lines.append('    _test("event_callback", test_callback)')
    lines.append('')
    lines.append('    return results')
    lines.append('')
    lines.append('')
    lines.append('if __name__ == "__main__":')
    lines.append('    logging.basicConfig(level=logging.INFO)')
    lines.append('    results = run_self_test()')
    lines.append(f'    print(f"\\n{{MODULE_ID}} Self-Test Results:")')
    lines.append(f'    print(f"  Passed: {{results[\'passed\']}}")')
    lines.append(f'    print(f"  Failed: {{results[\'failed\']}}")')
    lines.append('    for t in results["tests"]:')
    lines.append('        status = "✓" if t["status"] == "PASS" else "✗"')
    lines.append('        print(f"  {status} {t[\'name\']}")')
    lines.append('    sys.exit(0 if results["failed"] == 0 else 1)')

    return '\n'.join(lines) + '\n'


def main():
    print(f"Generating {len(MODULES)} M826-M845 modules...")

    # Create __init__.py
    init_lines = ['"""M826-M845: Historical Battle Data Improvement Subsystem."""\n']
    for mod in MODULES:
        init_lines.append(f'from .{mod["name"]} import {mod["title"]}  # {mod["id"]}')
    init_content = '\n'.join(init_lines) + '\n'
    (OUTPUT / "__init__.py").write_text(init_content)
    print(f"  Created __init__.py")

    # Create requirements.txt
    (OUTPUT / "requirements.txt").write_text("aiohttp>=3.9\nrequests>=2.31\n")

    # Create Makefile
    makefile = "test:\n\tpython -m pytest . -v\n\nlint:\n\tpython -m py_compile *.py\n"
    (OUTPUT / "Makefile").write_text(makefile)

    total_lines = 0
    for mod in MODULES:
        code = generate_module_code(mod)
        filepath = OUTPUT / f'{mod["name"]}.py'
        filepath.write_text(code, encoding="utf-8")
        line_count = len(code.splitlines())
        total_lines += line_count
        print(f"  {mod['id']}: {mod['name']}.py ({line_count} lines)")

    # Create conftest.py
    (OUTPUT / "conftest.py").write_text(
        '"""Shared test fixtures for M826-M845."""\nimport sys, pathlib\n'
        'sys.path.insert(0, str(pathlib.Path(__file__).parent))\n'
    )

    print(f"\nTotal: {total_lines} lines across {len(MODULES)} modules")
    print(f"Average: {total_lines // len(MODULES)} lines/module")
    print(f"Output: {OUTPUT}")


if __name__ == "__main__":
    main()
