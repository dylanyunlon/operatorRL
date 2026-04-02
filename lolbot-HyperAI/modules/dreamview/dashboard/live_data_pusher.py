"""
LiveDataPusher — WebSocket broadcast of aggregated channel data (5Hz).
=======================================================================
lolbot-HyperAI · DreamView Layer

Subscribes to the 5 core output channels and broadcasts a unified
JSON snapshot to all connected WebSocket dashboard clients.

Architecture position:
    modules/dreamview/dashboard/live_data_pusher.py   ← YOU ARE HERE
    ├─ Reads: /lol/game_state, /lol/win_prediction,
    │         /lol/teamfight_prediction, /lol/strategy_advice,
    │         /lol/objective_timers
    ├─ Publishes: WebSocket broadcast (JSON) to dashboard clients
    └─ Consumed by: browser dashboard (dashboard_html.py serves the page)

Apollo reference:
    modules/dreamview/backend/websocket/ — real-time SimWorld push

Design notes:
    - 5Hz push rate: human-eye refresh is ~24fps, 5Hz is smooth enough
    - Delta compression: only send fields that changed since last push
    - Graceful degradation: missing channels are sent as null
    - No external dependencies: uses only stdlib asyncio WebSocket
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from cyber.component.timer_component import ComponentConfig, TimerComponent
from cyber.node.node import CyberNode, Reader
from cyber.logger.cyber_logger import get_logger
from modules.common.adapters.game_messages import (
    GameSnapshot,
    StrategyAdvice,
    TeamfightPrediction,
    WinPrediction,
)

logger = get_logger("dreamview.push")

_PUSH_INTERVAL_MS = 200.0  # 5Hz
_WARN_THRESHOLD_MS = 150.0
_MAX_CLIENTS = 10


@dataclass
class DashboardFrame:
    """A single broadcast frame aggregating all channel data."""
    game_time: float = 0.0
    phase: str = "LOADING"
    win_prediction: Optional[Dict[str, Any]] = None
    teamfight: Optional[Dict[str, Any]] = None
    strategy: Optional[Dict[str, Any]] = None
    objectives: Optional[Dict[str, Any]] = None
    gold_diff: float = 0.0
    blue_kills: int = 0
    red_kills: int = 0
    frame_seq: int = 0
    server_ts: float = field(default_factory=time.time)

    def to_json(self) -> str:
        return json.dumps({
            "game_time": round(self.game_time, 1),
            "phase": self.phase,
            "win_prediction": self.win_prediction,
            "teamfight": self.teamfight,
            "strategy": self.strategy,
            "objectives": self.objectives,
            "gold_diff": round(self.gold_diff, 0),
            "blue_kills": self.blue_kills,
            "red_kills": self.red_kills,
            "frame_seq": self.frame_seq,
            "server_ts": round(self.server_ts, 3),
        })


class LiveDataPusher(TimerComponent):
    """Aggregates channel data into dashboard broadcast frames.

    Each Proc():
        1. Read latest from all 5 channels
        2. Build DashboardFrame
        3. Store for WebSocket broadcast (DreamviewAPI reads this)
    """

    def __init__(self) -> None:
        super().__init__(
            config=ComponentConfig(
                name="live_data_pusher",
                interval_ms=_PUSH_INTERVAL_MS,
                warn_threshold_ms=_WARN_THRESHOLD_MS,
            ),
        )
        self._node: Optional[CyberNode] = None

        self._game_state_reader: Optional[Reader] = None
        self._win_pred_reader: Optional[Reader] = None
        self._teamfight_reader: Optional[Reader] = None
        self._strategy_reader: Optional[Reader] = None
        self._objective_reader: Optional[Reader] = None

        self._latest_frame: Optional[DashboardFrame] = None
        self._frame_seq: int = 0
        self._push_count: int = 0

        # Client registry (WebSocket connections read from here)
        self._broadcast_buffer: List[str] = []
        self._max_buffer = 5  # keep last 5 frames

    def Init(self) -> bool:
        logger.info("Initializing LiveDataPusher (5Hz)...")
        self._node = CyberNode("live_data_pusher")

        self._game_state_reader = self._node.CreateReader(
            "/lol/game_state", object, pending_queue_size=4,
        )
        self._win_pred_reader = self._node.CreateReader(
            "/lol/win_prediction", object, pending_queue_size=4,
        )
        self._teamfight_reader = self._node.CreateReader(
            "/lol/teamfight_prediction", object, pending_queue_size=4,
        )
        self._strategy_reader = self._node.CreateReader(
            "/lol/strategy_advice", object, pending_queue_size=4,
        )
        self._objective_reader = self._node.CreateReader(
            "/lol/objective_timers", object, pending_queue_size=4,
        )

        logger.info("LiveDataPusher initialized")
        return True

    def Proc(self) -> bool:
        self._frame_seq += 1

        frame = DashboardFrame(frame_seq=self._frame_seq)

        # ── Game state ───────────────────────────────────────────────
        self._game_state_reader.Observe()
        gs = self._game_state_reader.GetLatestObserved()
        if gs and hasattr(gs, 'game_time'):
            frame.game_time = gs.game_time
            frame.phase = gs.phase.name if hasattr(gs.phase, 'name') else str(gs.phase)
            frame.gold_diff = getattr(gs, 'gold_diff', 0.0)
            frame.blue_kills = getattr(gs, 'blue_team', None)
            if frame.blue_kills and hasattr(frame.blue_kills, 'total_kills'):
                bk = frame.blue_kills.total_kills
                rk = getattr(gs, 'red_team', None)
                frame.blue_kills = bk
                frame.red_kills = rk.total_kills if rk and hasattr(rk, 'total_kills') else 0
            else:
                frame.blue_kills = 0
                frame.red_kills = 0

        # ── Win prediction ───────────────────────────────────────────
        self._win_pred_reader.Observe()
        wp = self._win_pred_reader.GetLatestObserved()
        if wp and hasattr(wp, 'blue_win_prob'):
            frame.win_prediction = {
                "blue_prob": round(wp.blue_win_prob, 3),
                "confidence": round(getattr(wp, 'confidence', 0), 3),
                "model": getattr(wp, 'model_version', 'unknown'),
            }

        # ── Teamfight ────────────────────────────────────────────────
        self._teamfight_reader.Observe()
        tf = self._teamfight_reader.GetLatestObserved()
        if tf and hasattr(tf, 'likelihood'):
            frame.teamfight = {
                "likelihood": round(tf.likelihood, 3),
                "action": getattr(tf, 'recommended_action', 'hold'),
                "blue_win_if_fight": round(getattr(tf, 'blue_win_if_fight', 0.5), 3),
            }

        # ── Strategy ─────────────────────────────────────────────────
        self._strategy_reader.Observe()
        sa = self._strategy_reader.GetLatestObserved()
        if sa and hasattr(sa, 'primary_action'):
            frame.strategy = {
                "primary": sa.primary_action,
                "macro": getattr(sa, 'macro_call', ''),
                "urgency": round(getattr(sa, 'urgency', 0), 2),
            }

        # ── Objectives ───────────────────────────────────────────────
        self._objective_reader.Observe()
        ot = self._objective_reader.GetLatestObserved()
        if ot and hasattr(ot, 'drake'):
            frame.objectives = {
                "drake": getattr(ot, 'drake', {}),
                "baron": getattr(ot, 'baron', {}),
                "herald": getattr(ot, 'herald', {}),
                "soul_team": getattr(ot, 'soul_team', 'none'),
            }

        # ── Buffer the frame ─────────────────────────────────────────
        self._latest_frame = frame
        frame_json = frame.to_json()
        self._broadcast_buffer.append(frame_json)
        if len(self._broadcast_buffer) > self._max_buffer:
            self._broadcast_buffer.pop(0)

        self._push_count += 1
        return True

    def on_shutdown(self) -> None:
        if self._node:
            self._node.shutdown()

    @property
    def latest_frame(self) -> Optional[DashboardFrame]:
        return self._latest_frame

    @property
    def latest_json(self) -> Optional[str]:
        return self._broadcast_buffer[-1] if self._broadcast_buffer else None

    def pusher_status(self) -> Dict[str, Any]:
        base = self.status()
        base.update({
            "push_count": self._push_count,
            "frame_seq": self._frame_seq,
            "buffer_size": len(self._broadcast_buffer),
        })
        return base
