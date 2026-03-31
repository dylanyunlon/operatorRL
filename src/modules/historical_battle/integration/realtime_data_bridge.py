#!/usr/bin/env python3
"""
M818 - Real-time Data Bridge
====================================
OperatorRL Historical Battle System - Historical/Realtime Data Fusion

查看实时数据流与历史数据融合的实现方式,理解其模式,
特别是实时事件流和历史统计是如何在同一管道中对齐的。
从 LCU WebSocket 事件流开始,遵循该模式实现数据桥接层,
使系统可以将当前对局的实时状态与历史战绩数据叠加分析。

Core responsibilities:
- Consume LCU WebSocket events for game state tracking
- Fuse live game data with historical player/champion analysis
- Produce enriched snapshots combining realtime + historical context
- Generate real-time insights and win probability updates
- Support event-driven callbacks for downstream consumers
"""

import os, sys, json, time, math, logging, hashlib, statistics
from pathlib import Path
from enum import Enum, auto
from typing import Dict, List, Any, Optional, Tuple, Set, Union, Callable
from dataclasses import dataclass, field, asdict
from abc import ABC, abstractmethod
from collections import defaultdict
from datetime import datetime, timezone

logger = logging.getLogger("operatorRL.historical_battle.integration.bridge")
logger.setLevel(logging.DEBUG)

# ─── Constants ────────────────────────────────────────────────────────

LCU_WS_RECONNECT_DELAY = 5
BRIDGE_BUFFER_SIZE = 1000
DATA_FUSION_INTERVAL_MS = 500
GAME_STATE_POLL_INTERVAL = 1.0
HISTORICAL_CONTEXT_DEPTH = 20
INSIGHT_CACHE_TTL = 30
MAX_EVENT_BACKLOG = 5000

# ─── Enumerations ─────────────────────────────────────────────────────

class GamePhaseState(Enum):
    NONE = auto()
    LOBBY = auto()
    CHAMPION_SELECT = auto()
    LOADING = auto()
    IN_GAME = auto()
    POST_GAME = auto()
    RECONNECTING = auto()

class DataStreamType(Enum):
    LCU_EVENT = "lcu_event"
    NETWORK_CAPTURE = "network_capture"
    HISTORICAL_LOOKUP = "historical_lookup"
    FUSED_STATE = "fused_state"

class InsightSeverity(Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"
    OPPORTUNITY = "opportunity"

# ─── Data Models ──────────────────────────────────────────────────────

@dataclass
class LiveGameState:
    game_id: Optional[str] = None
    phase: GamePhaseState = GamePhaseState.NONE
    game_time_seconds: float = 0.0
    my_team: List[Dict[str, Any]] = field(default_factory=list)
    enemy_team: List[Dict[str, Any]] = field(default_factory=list)
    events: List[Dict[str, Any]] = field(default_factory=list)
    objectives: Dict[str, Any] = field(default_factory=dict)
    gold_diff: float = 0.0
    kill_diff: int = 0
    tower_diff: int = 0
    dragon_count: Dict[int, int] = field(default_factory=lambda: {100: 0, 200: 0})
    baron_count: Dict[int, int] = field(default_factory=lambda: {100: 0, 200: 0})
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "game_id": self.game_id, "phase": self.phase.name,
            "game_time": round(self.game_time_seconds, 1),
            "gold_diff": round(self.gold_diff, 0),
            "kill_diff": self.kill_diff, "tower_diff": self.tower_diff,
            "event_count": len(self.events),
            "dragons": self.dragon_count, "barons": self.baron_count,
        }

@dataclass
class HistoricalContext:
    player_profiles: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    champion_stats: Dict[int, Dict[str, Any]] = field(default_factory=dict)
    matchup_data: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    scouting_reports: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    team_composition_score: Optional[Dict[str, Any]] = None
    meta_snapshot: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "players_loaded": len(self.player_profiles),
            "champions_loaded": len(self.champion_stats),
            "matchups_loaded": len(self.matchup_data),
            "has_comp_score": self.team_composition_score is not None,
            "has_meta": self.meta_snapshot is not None,
        }

@dataclass
class GameInsight:
    """A single analytical insight derived from fused data."""
    insight_id: str
    severity: InsightSeverity
    message: str
    timestamp: float = field(default_factory=time.time)
    game_time_seconds: float = 0.0
    data: Dict[str, Any] = field(default_factory=dict)
    actionable: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.insight_id, "severity": self.severity.value,
            "message": self.message, "game_time": round(self.game_time_seconds, 1),
            "actionable": self.actionable,
        }

@dataclass
class FusedSnapshot:
    timestamp: float
    live_state: LiveGameState
    historical_context: HistoricalContext
    insights: List[GameInsight] = field(default_factory=list)
    win_probability: float = 0.5
    recommended_actions: List[str] = field(default_factory=list)
    momentum_score: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "live": self.live_state.to_dict(),
            "context": self.historical_context.to_dict(),
            "insights": [i.to_dict() for i in self.insights],
            "win_probability": round(self.win_probability, 4),
            "momentum": round(self.momentum_score, 3),
            "actions": self.recommended_actions,
        }

@dataclass
class BridgeStatistics:
    total_events_processed: int = 0
    total_fusions: int = 0
    avg_fusion_latency_ms: float = 0.0
    uptime_seconds: float = 0.0
    errors: int = 0
    last_error: Optional[str] = None
    insights_generated: int = 0
    phase_transitions: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

# ─── Event Buffer ─────────────────────────────────────────────────────

class EventBuffer:
    """Thread-safe event buffer with capacity limits."""

    def __init__(self, max_size: int = MAX_EVENT_BACKLOG):
        self._buffer: List[Dict[str, Any]] = []
        self._max_size = max_size
        self._dropped = 0

    def push(self, event: Dict[str, Any]) -> bool:
        if len(self._buffer) >= self._max_size:
            self._buffer.pop(0)
            self._dropped += 1
        self._buffer.append(event)
        return True

    def drain(self, count: int = 0) -> List[Dict[str, Any]]:
        if count <= 0:
            items = list(self._buffer)
            self._buffer.clear()
            return items
        items = self._buffer[:count]
        self._buffer = self._buffer[count:]
        return items

    @property
    def size(self) -> int:
        return len(self._buffer)

    @property
    def dropped_count(self) -> int:
        return self._dropped

    def clear(self) -> None:
        self._buffer.clear()

# ─── Insight Generator ────────────────────────────────────────────────

class InsightGenerator:
    """Generates game insights from fused state data."""

    def __init__(self):
        self._insight_counter = 0
        self._generated_types: Set[str] = set()

    def _gen_id(self) -> str:
        self._insight_counter += 1
        return f"ins_{self._insight_counter:05d}"

    def generate(self, live: LiveGameState, historical: HistoricalContext) -> List[GameInsight]:
        insights = []
        if live.phase == GamePhaseState.CHAMPION_SELECT:
            if historical.scouting_reports:
                insights.append(GameInsight(
                    insight_id=self._gen_id(), severity=InsightSeverity.INFO,
                    message=f"Scouting data available for {len(historical.scouting_reports)} opponents",
                    game_time_seconds=0,
                ))
            if historical.team_composition_score:
                comp_score = historical.team_composition_score.get("overall", 0)
                if comp_score > 0.7:
                    insights.append(GameInsight(
                        insight_id=self._gen_id(), severity=InsightSeverity.INFO,
                        message="Strong team composition detected",
                    ))
        if live.phase == GamePhaseState.IN_GAME:
            if live.gold_diff > 5000:
                insights.append(GameInsight(
                    insight_id=self._gen_id(), severity=InsightSeverity.OPPORTUNITY,
                    message=f"Large gold lead ({live.gold_diff:.0f}g) - force Baron or push",
                    game_time_seconds=live.game_time_seconds,
                ))
            elif live.gold_diff > 3000:
                insights.append(GameInsight(
                    insight_id=self._gen_id(), severity=InsightSeverity.INFO,
                    message="Moderate gold lead - maintain pressure",
                    game_time_seconds=live.game_time_seconds,
                ))
            elif live.gold_diff < -5000:
                insights.append(GameInsight(
                    insight_id=self._gen_id(), severity=InsightSeverity.WARNING,
                    message=f"Large gold deficit ({abs(live.gold_diff):.0f}g) - play safe and scale",
                    game_time_seconds=live.game_time_seconds,
                ))
            elif live.gold_diff < -3000:
                insights.append(GameInsight(
                    insight_id=self._gen_id(), severity=InsightSeverity.WARNING,
                    message="Moderate gold deficit - avoid risky plays",
                    game_time_seconds=live.game_time_seconds,
                ))
            gt = live.game_time_seconds
            if 1200 <= gt <= 1260 and "dragon_reminder" not in self._generated_types:
                insights.append(GameInsight(
                    insight_id=self._gen_id(), severity=InsightSeverity.INFO,
                    message="20-minute mark approaching - Dragon soul timer awareness",
                    game_time_seconds=gt,
                ))
                self._generated_types.add("dragon_reminder")
            if gt >= 1200 and live.baron_count.get(100, 0) == 0 and live.baron_count.get(200, 0) == 0:
                if "baron_spawn" not in self._generated_types:
                    insights.append(GameInsight(
                        insight_id=self._gen_id(), severity=InsightSeverity.INFO,
                        message="Baron Nashor has spawned - establish vision control",
                        game_time_seconds=gt,
                    ))
                    self._generated_types.add("baron_spawn")
        return insights

    def reset(self) -> None:
        self._insight_counter = 0
        self._generated_types.clear()

# ─── Main Bridge ──────────────────────────────────────────────────────

class RealtimeDataBridge:
    """Bridges live game data with historical analysis."""

    def __init__(self):
        self._live_state = LiveGameState()
        self._historical = HistoricalContext()
        self._snapshot_history: List[FusedSnapshot] = []
        self._event_handlers: Dict[str, List[Callable]] = defaultdict(list)
        self._stats = BridgeStatistics()
        self._running = False
        self._fusion_callbacks: List[Callable[[FusedSnapshot], None]] = []
        self._start_time = 0.0
        self._event_buffer = EventBuffer()
        self._insight_gen = InsightGenerator()
        self._previous_phase = GamePhaseState.NONE

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def statistics(self) -> BridgeStatistics:
        if self._running:
            self._stats.uptime_seconds = time.time() - self._start_time
        return self._stats

    def start(self) -> None:
        self._running = True
        self._start_time = time.time()
        self._insight_gen.reset()
        logger.info("Realtime Data Bridge started")

    def stop(self) -> None:
        self._running = False
        self._stats.uptime_seconds = time.time() - self._start_time
        logger.info(f"Realtime Data Bridge stopped. Stats: {self._stats.to_dict()}")

    def register_fusion_callback(self, callback: Callable[[FusedSnapshot], None]) -> None:
        self._fusion_callbacks.append(callback)

    def on_event(self, event_type: str, handler: Callable) -> None:
        self._event_handlers[event_type].append(handler)

    def load_historical_context(self, player_profiles: Optional[Dict[str, Dict]] = None,
                                 champion_stats: Optional[Dict[int, Dict]] = None,
                                 matchup_data: Optional[Dict[str, Dict]] = None,
                                 scouting: Optional[Dict[str, Dict]] = None) -> None:
        if player_profiles:
            self._historical.player_profiles.update(player_profiles)
        if champion_stats:
            self._historical.champion_stats.update(champion_stats)
        if matchup_data:
            self._historical.matchup_data.update(matchup_data)
        if scouting:
            self._historical.scouting_reports.update(scouting)
        logger.info(f"Historical context loaded: {self._historical.to_dict()}")

    def process_lcu_event(self, event: Dict[str, Any]) -> Optional[FusedSnapshot]:
        """Process a League Client event and produce a fused snapshot."""
        self._stats.total_events_processed += 1
        self._event_buffer.push(event)
        event_type = event.get("eventType", event.get("type", "unknown"))
        uri = event.get("uri", "")
        data = event.get("data", {})
        if "/lol-gameflow/v1/gameflow-phase" in uri:
            phase_map = {
                "None": GamePhaseState.NONE, "Lobby": GamePhaseState.LOBBY,
                "ChampSelect": GamePhaseState.CHAMPION_SELECT,
                "GameStart": GamePhaseState.LOADING, "InProgress": GamePhaseState.IN_GAME,
                "EndOfGame": GamePhaseState.POST_GAME, "Reconnect": GamePhaseState.RECONNECTING,
            }
            new_phase = phase_map.get(str(data), GamePhaseState.NONE)
            if new_phase != self._previous_phase:
                self._stats.phase_transitions += 1
                self._previous_phase = new_phase
                if new_phase == GamePhaseState.IN_GAME:
                    self._insight_gen.reset()
            self._live_state.phase = new_phase
        elif "/lol-champ-select" in uri:
            self._live_state.phase = GamePhaseState.CHAMPION_SELECT
        for handler in self._event_handlers.get(event_type, []):
            try:
                handler(event)
            except Exception as exc:
                logger.error(f"Event handler error: {exc}")
                self._stats.errors += 1
                self._stats.last_error = str(exc)
        return self._produce_fusion()

    def process_network_packet(self, packet_data: Dict[str, Any]) -> Optional[FusedSnapshot]:
        """Process a captured network packet."""
        self._stats.total_events_processed += 1
        url = packet_data.get("url", "")
        body = packet_data.get("response_body", {})
        if "/lol-match-history" in url and isinstance(body, dict):
            games = body.get("games", {}).get("games", [])
            if games:
                logger.debug(f"Received {len(games)} match history entries from network")
        return self._produce_fusion()

    def _produce_fusion(self) -> FusedSnapshot:
        """Fuse live state with historical context."""
        start = time.time()
        insights = self._insight_gen.generate(self._live_state, self._historical)
        self._stats.insights_generated += len(insights)
        snapshot = FusedSnapshot(
            timestamp=time.time(), live_state=self._live_state,
            historical_context=self._historical, insights=insights,
            win_probability=self._estimate_win_probability(),
            recommended_actions=self._generate_recommendations(),
            momentum_score=self._calculate_momentum(),
        )
        fusion_time = (time.time() - start) * 1000
        self._stats.total_fusions += 1
        n = self._stats.total_fusions
        self._stats.avg_fusion_latency_ms = (self._stats.avg_fusion_latency_ms * (n-1) + fusion_time) / n
        self._snapshot_history.append(snapshot)
        if len(self._snapshot_history) > BRIDGE_BUFFER_SIZE:
            self._snapshot_history = self._snapshot_history[-BRIDGE_BUFFER_SIZE:]
        for cb in self._fusion_callbacks:
            try:
                cb(snapshot)
            except Exception as exc:
                logger.error(f"Fusion callback error: {exc}")
        return snapshot

    def _estimate_win_probability(self) -> float:
        base = 0.5
        if self._live_state.gold_diff != 0:
            base += (self._live_state.gold_diff / 15000) * 0.2
        if self._live_state.kill_diff != 0:
            base += (self._live_state.kill_diff / 20) * 0.1
        if self._live_state.tower_diff != 0:
            base += (self._live_state.tower_diff / 11) * 0.1
        d100 = self._live_state.dragon_count.get(100, 0)
        d200 = self._live_state.dragon_count.get(200, 0)
        if d100 != d200:
            base += ((d100 - d200) / 4) * 0.05
        return max(0.05, min(0.95, base))

    def _calculate_momentum(self) -> float:
        if len(self._snapshot_history) < 3:
            return 0.0
        recent = self._snapshot_history[-5:]
        probs = [s.win_probability for s in recent]
        if len(probs) >= 2:
            return probs[-1] - probs[0]
        return 0.0

    def _generate_recommendations(self) -> List[str]:
        actions = []
        phase = self._live_state.phase
        if phase == GamePhaseState.CHAMPION_SELECT:
            actions.append("Review enemy scouting reports")
            actions.append("Consider counter-picks based on historical matchup data")
        elif phase == GamePhaseState.IN_GAME:
            gt = self._live_state.game_time_seconds
            if gt < 840:
                actions.append("Focus on lane phase fundamentals")
            elif gt < 1500:
                actions.append("Group for objectives and vision control")
            else:
                actions.append("Play around Baron and Elder Dragon timers")
            if self._live_state.gold_diff > 5000:
                actions.append("Force Baron with gold lead advantage")
            elif self._live_state.gold_diff < -5000:
                actions.append("Stall and look for catches to swing momentum")
        return actions

    def get_latest_snapshot(self) -> Optional[FusedSnapshot]:
        return self._snapshot_history[-1] if self._snapshot_history else None

    def get_event_backlog_size(self) -> int:
        return self._event_buffer.size

    def get_snapshot_history(self, count: int = 10) -> List[Dict[str, Any]]:
        """Get recent snapshot history for trend visualization."""
        recent = self._snapshot_history[-count:]
        return [{"time": s.timestamp, "win_prob": round(s.win_probability, 4),
                 "momentum": round(s.momentum_score, 3),
                 "insights": len(s.insights)} for s in recent]


# ─── Module Self-Test ─────────────────────────────────────────────────

def _self_test() -> Dict[str, Any]:
    results = {"module": "M818_realtime_data_bridge", "tests": []}
    try:
        bridge = RealtimeDataBridge()
        bridge.start()
        assert bridge.is_running
        bridge.load_historical_context(
            player_profiles={"player1": {"rank": "GOLD"}},
            scouting={"enemy1": {"threat": 0.7}},
        )
        event = {"uri": "/lol-gameflow/v1/gameflow-phase", "data": "ChampSelect", "eventType": "Update"}
        snapshot = bridge.process_lcu_event(event)
        assert snapshot is not None
        assert snapshot.live_state.phase == GamePhaseState.CHAMPION_SELECT
        bridge.stop()
        results["tests"].append({"name": "bridge_lifecycle", "status": "pass"})
    except Exception as e:
        results["tests"].append({"name": "bridge_lifecycle", "status": "fail", "error": str(e)})
    try:
        bridge = RealtimeDataBridge()
        bridge.start()
        bridge._live_state.gold_diff = 5000
        bridge._live_state.phase = GamePhaseState.IN_GAME
        snapshot = bridge._produce_fusion()
        assert snapshot.win_probability > 0.5
        assert len(snapshot.insights) > 0
        results["tests"].append({"name": "fusion_insights", "status": "pass"})
    except Exception as e:
        results["tests"].append({"name": "fusion_insights", "status": "fail", "error": str(e)})
    try:
        buf = EventBuffer(max_size=3)
        buf.push({"a": 1})
        buf.push({"b": 2})
        buf.push({"c": 3})
        buf.push({"d": 4})
        assert buf.size == 3
        assert buf.dropped_count == 1
        items = buf.drain()
        assert len(items) == 3
        assert buf.size == 0
        results["tests"].append({"name": "event_buffer", "status": "pass"})
    except Exception as e:
        results["tests"].append({"name": "event_buffer", "status": "fail", "error": str(e)})
    try:
        gen = InsightGenerator()
        live = LiveGameState(phase=GamePhaseState.IN_GAME, gold_diff=6000, game_time_seconds=1250)
        hist = HistoricalContext()
        insights = gen.generate(live, hist)
        assert len(insights) >= 1
        assert any(i.severity == InsightSeverity.OPPORTUNITY for i in insights)
        results["tests"].append({"name": "insight_generation", "status": "pass"})
    except Exception as e:
        results["tests"].append({"name": "insight_generation", "status": "fail", "error": str(e)})
    results["passed"] = sum(1 for t in results["tests"] if t["status"] == "pass")
    results["total"] = len(results["tests"])
    return results

if __name__ == "__main__":
    print(json.dumps(_self_test(), indent=2))
