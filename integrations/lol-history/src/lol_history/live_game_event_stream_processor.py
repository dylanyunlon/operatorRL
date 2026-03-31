"""
LiveGameEventStreamProcessor — Processes real-time game events from Live Client Data API.

Architecture (拿来主义):
  seraphine_event_stream_processor.py（M524）— event stream processing patterns
  lcu_websocket_event_translator.py（M751）— event translation and normalization

Location: integrations/lol-history/src/lol_history/live_game_event_stream_processor.py

Design Notes (Knuth-level critique):
  User:
    - Events arrive pre-sorted by EventTime from LCD API; processor handles late arrivals.
    - Deduplication by EventID prevents double-counting on rapid poll cycles.
    - Time-window aggregation provides "last N seconds" view for decision context.
  System:
    - O(1) dedup via set membership; O(n) window queries bounded by maxlen.
    - Event type dispatch avoids if-elif chain: uses registry pattern.
    - Downstream distribution via callback list; failures isolated per subscriber.
    - Memory bounded: deque(maxlen) for history; set trimmed periodically.
"""
from __future__ import annotations

import logging
import math
import time
from collections import OrderedDict, defaultdict, deque
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)
_EVOLUTION_KEY = "integrations.lol_history.live_game_event_stream_processor.v1"


def _safe_div(a: float, b: float, d: float = 0.0) -> float:
    return a / b if b else d


# ─── Event Classification ─────────────────────────────────────────────────────

class EventCategory:
    """Categorization of LoL game events for priority routing."""
    COMBAT = "combat"
    OBJECTIVE = "objective"
    STRUCTURE = "structure"
    ITEM = "item"
    ABILITY = "ability"
    SYSTEM = "system"
    UNKNOWN = "unknown"

    EVENT_MAP = {
        "ChampionKill": COMBAT,
        "Multikill": COMBAT,
        "Ace": COMBAT,
        "FirstBlood": COMBAT,
        "DragonKill": OBJECTIVE,
        "BaronKill": OBJECTIVE,
        "HeraldKill": OBJECTIVE,
        "ElderDragonKill": OBJECTIVE,
        "AtakhanKill": OBJECTIVE,
        "VoidGrubKill": OBJECTIVE,
        "TurretKilled": STRUCTURE,
        "InhibKilled": STRUCTURE,
        "InhibRespawningSoon": STRUCTURE,
        "InhibRespawned": STRUCTURE,
        "ItemPurchased": ITEM,
        "ItemSold": ITEM,
        "ItemUndo": ITEM,
        "GameStart": SYSTEM,
        "GameEnd": SYSTEM,
        "MinionsSpawning": SYSTEM,
    }

    @classmethod
    def classify(cls, event_name: str) -> str:
        return cls.EVENT_MAP.get(event_name, cls.UNKNOWN)


# ─── Event Priority ───────────────────────────────────────────────────────────

class EventPriority:
    """Priority levels for event processing order."""
    CRITICAL = 0
    HIGH = 1
    MEDIUM = 2
    LOW = 3
    INFO = 4

    PRIORITY_MAP = {
        "BaronKill": CRITICAL,
        "ElderDragonKill": CRITICAL,
        "Ace": CRITICAL,
        "GameEnd": CRITICAL,
        "DragonKill": HIGH,
        "HeraldKill": HIGH,
        "ChampionKill": HIGH,
        "FirstBlood": HIGH,
        "TurretKilled": MEDIUM,
        "InhibKilled": MEDIUM,
        "Multikill": MEDIUM,
        "VoidGrubKill": MEDIUM,
        "AtakhanKill": MEDIUM,
        "InhibRespawningSoon": LOW,
        "InhibRespawned": LOW,
        "ItemPurchased": INFO,
        "GameStart": INFO,
        "MinionsSpawning": INFO,
    }

    @classmethod
    def get_priority(cls, event_name: str) -> int:
        return cls.PRIORITY_MAP.get(event_name, cls.INFO)


# ─── Deduplication Engine ─────────────────────────────────────────────────────

class _DeduplicationEngine:
    """Deduplicates events by EventID with periodic cleanup."""

    def __init__(self, max_ids: int = 10000) -> None:
        self._seen_ids: OrderedDict = OrderedDict()
        self._max_ids = max_ids
        self._dedup_count = 0
        self._total_checked = 0

    def is_duplicate(self, event_id: int) -> bool:
        self._total_checked += 1
        if event_id in self._seen_ids:
            self._dedup_count += 1
            return True
        self._seen_ids[event_id] = time.monotonic()
        if len(self._seen_ids) > self._max_ids:
            oldest = len(self._seen_ids) - self._max_ids
            for _ in range(oldest):
                self._seen_ids.popitem(last=False)
        return False

    def get_stats(self) -> Dict[str, Any]:
        return {
            "tracked_ids": len(self._seen_ids),
            "dedup_count": self._dedup_count,
            "total_checked": self._total_checked,
            "dedup_rate": _safe_div(self._dedup_count, self._total_checked),
        }


# ─── Time Window Aggregator ──────────────────────────────────────────────────

class _TimeWindowAggregator:
    """Aggregates events within configurable time windows."""

    def __init__(self, window_sizes: List[float] = None) -> None:
        self._window_sizes = window_sizes or [30.0, 60.0, 120.0, 300.0]
        self._events: deque = deque(maxlen=5000)
        self._type_counts: Dict[str, int] = defaultdict(int)
        self._category_counts: Dict[str, int] = defaultdict(int)

    def add_event(self, event: Dict[str, Any]) -> None:
        game_time = event.get("EventTime", 0.0)
        event_name = event.get("EventName", "Unknown")
        category = EventCategory.classify(event_name)
        self._events.append({
            "event": event,
            "game_time": game_time,
            "category": category,
            "insert_time": time.monotonic(),
        })
        self._type_counts[event_name] += 1
        self._category_counts[category] += 1

    def get_window(self, current_game_time: float,
                   window_seconds: float) -> List[Dict[str, Any]]:
        cutoff = current_game_time - window_seconds
        return [e["event"] for e in self._events if e["game_time"] >= cutoff]

    def get_aggregation(self, current_game_time: float) -> Dict[str, Any]:
        result = {}
        for ws in self._window_sizes:
            window_events = self.get_window(current_game_time, ws)
            type_counts = defaultdict(int)
            for e in window_events:
                type_counts[e.get("EventName", "Unknown")] += 1
            result[f"last_{int(ws)}s"] = {
                "event_count": len(window_events),
                "type_breakdown": dict(type_counts),
            }
        return result

    def get_stats(self) -> Dict[str, Any]:
        return {
            "total_events": len(self._events),
            "type_counts": dict(self._type_counts),
            "category_counts": dict(self._category_counts),
            "window_sizes": self._window_sizes,
        }


# ─── Event Enrichment ─────────────────────────────────────────────────────────

class _EventEnricher:
    """Enriches raw events with derived fields (category, priority, context)."""

    def __init__(self) -> None:
        self._enrichment_count = 0
        self._kill_streak_tracker: Dict[str, int] = defaultdict(int)
        self._death_tracker: Dict[str, int] = defaultdict(int)
        self._objective_count = {"dragon": 0, "baron": 0, "herald": 0, "grub": 0}

    def enrich(self, event: Dict[str, Any]) -> Dict[str, Any]:
        self._enrichment_count += 1
        event_name = event.get("EventName", "Unknown")
        enriched = {
            **event,
            "_category": EventCategory.classify(event_name),
            "_priority": EventPriority.get_priority(event_name),
            "_enriched_at": time.monotonic(),
        }

        if event_name == "ChampionKill":
            killer = event.get("KillerName", "")
            victim = event.get("VictimName", "")
            if killer:
                self._kill_streak_tracker[killer] += 1
                enriched["_killer_streak"] = self._kill_streak_tracker[killer]
            if victim:
                self._kill_streak_tracker[victim] = 0
                self._death_tracker[victim] += 1
                enriched["_victim_deaths"] = self._death_tracker[victim]

        elif event_name == "DragonKill":
            self._objective_count["dragon"] += 1
            enriched["_dragon_count"] = self._objective_count["dragon"]
            enriched["_soul_possible"] = self._objective_count["dragon"] >= 4

        elif event_name == "BaronKill":
            self._objective_count["baron"] += 1
            enriched["_baron_count"] = self._objective_count["baron"]

        elif event_name == "HeraldKill":
            self._objective_count["herald"] += 1
            enriched["_herald_count"] = self._objective_count["herald"]

        return enriched

    def get_stats(self) -> Dict[str, Any]:
        return {
            "enrichment_count": self._enrichment_count,
            "kill_streaks": dict(self._kill_streak_tracker),
            "death_counts": dict(self._death_tracker),
            "objective_counts": dict(self._objective_count),
        }


# ─── Downstream Distributor ──────────────────────────────────────────────────

class _DownstreamDistributor:
    """Distributes processed events to registered subscribers."""

    def __init__(self) -> None:
        self._subscribers: Dict[str, List[Callable]] = {}
        self._global_subscribers: List[Callable] = []
        self._dispatch_count = 0
        self._error_count = 0

    def subscribe_type(self, event_type: str, callback: Callable) -> int:
        if event_type not in self._subscribers:
            self._subscribers[event_type] = []
        self._subscribers[event_type].append(callback)
        return len(self._subscribers[event_type])

    def subscribe_all(self, callback: Callable) -> int:
        self._global_subscribers.append(callback)
        return len(self._global_subscribers)

    def distribute(self, event: Dict[str, Any]) -> int:
        dispatched = 0
        event_name = event.get("EventName", "")

        for cb in self._global_subscribers:
            try:
                cb(event)
                dispatched += 1
            except Exception as e:
                self._error_count += 1
                logger.warning("Global subscriber error: %s", e)

        for cb in self._subscribers.get(event_name, []):
            try:
                cb(event)
                dispatched += 1
            except Exception as e:
                self._error_count += 1
                logger.warning("Type subscriber error for %s: %s", event_name, e)

        category = event.get("_category", "")
        for cb in self._subscribers.get(f"category:{category}", []):
            try:
                cb(event)
                dispatched += 1
            except Exception as e:
                self._error_count += 1

        self._dispatch_count += dispatched
        return dispatched

    def get_stats(self) -> Dict[str, Any]:
        return {
            "type_subscribers": {k: len(v) for k, v in self._subscribers.items()},
            "global_subscribers": len(self._global_subscribers),
            "dispatch_count": self._dispatch_count,
            "error_count": self._error_count,
        }


class LiveGameEventStreamProcessor:
    """Processes real-time game events with dedup, enrichment, windowing, and distribution.

    Public API: process_events, process_single_event, get_event_summary,
                get_events_by_type, get_recent_events, subscribe_event_type,
                subscribe_all_events, get_window_aggregation, get_stats
    """

    def __init__(self, max_event_history: int = 2000) -> None:
        self.evolution_callback: Optional[Callable] = None
        self._op_count = 0
        self._process_count = 0
        self._dedup = _DeduplicationEngine()
        self._aggregator = _TimeWindowAggregator()
        self._enricher = _EventEnricher()
        self._distributor = _DownstreamDistributor()
        self._processed_events: deque = deque(maxlen=max_event_history)
        self._event_type_index: Dict[str, List[int]] = defaultdict(list)
        self._latest_game_time: float = 0.0
        self._batch_count = 0

    def _fire(self, et: str, data: Dict) -> None:
        if self.evolution_callback:
            self.evolution_callback({"type": et, "key": _EVOLUTION_KEY, **data})

    def process_events(self, events: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Process a batch of events from LCD API poll."""
        self._op_count += 1
        self._batch_count += 1
        new_events = []
        duplicates = 0

        for event in events:
            event_id = event.get("EventID", -1)
            if event_id >= 0 and self._dedup.is_duplicate(event_id):
                duplicates += 1
                continue

            enriched = self._enricher.enrich(event)
            self._aggregator.add_event(enriched)
            idx = len(self._processed_events)
            self._processed_events.append(enriched)
            event_name = enriched.get("EventName", "Unknown")
            self._event_type_index[event_name].append(idx)
            self._process_count += 1
            new_events.append(enriched)

            game_time = enriched.get("EventTime", 0.0)
            if game_time > self._latest_game_time:
                self._latest_game_time = game_time

            self._distributor.distribute(enriched)

        self._fire("events_processed", {
            "batch": self._batch_count,
            "new": len(new_events),
            "duplicates": duplicates,
        })

        return {
            "status": "ok",
            "new_events": len(new_events),
            "duplicates": duplicates,
            "total_processed": self._process_count,
            "latest_game_time": self._latest_game_time,
        }

    def process_single_event(self, event: Dict[str, Any]) -> Dict[str, Any]:
        """Process a single event."""
        self._op_count += 1
        return self.process_events([event])

    def get_event_summary(self) -> Dict[str, Any]:
        """Get summary of all processed events."""
        self._op_count += 1
        type_counts = {}
        for event_name, indices in self._event_type_index.items():
            type_counts[event_name] = len(indices)

        category_counts = defaultdict(int)
        for event in self._processed_events:
            cat = event.get("_category", "unknown")
            category_counts[cat] += 1

        return {
            "status": "ok",
            "total_events": len(self._processed_events),
            "latest_game_time": self._latest_game_time,
            "type_counts": type_counts,
            "category_counts": dict(category_counts),
            "dedup_stats": self._dedup.get_stats(),
        }

    def get_events_by_type(self, event_type: str,
                           limit: int = 50) -> Dict[str, Any]:
        """Get events filtered by type."""
        self._op_count += 1
        indices = self._event_type_index.get(event_type, [])
        events_list = list(self._processed_events)
        matched = []
        for idx in indices[-limit:]:
            if idx < len(events_list):
                matched.append(events_list[idx])
        return {
            "status": "ok",
            "event_type": event_type,
            "count": len(matched),
            "events": matched,
        }

    def get_recent_events(self, window_seconds: float = 60.0) -> Dict[str, Any]:
        """Get events from the last N seconds of game time."""
        self._op_count += 1
        window = self._aggregator.get_window(self._latest_game_time, window_seconds)
        return {
            "status": "ok",
            "window_seconds": window_seconds,
            "current_game_time": self._latest_game_time,
            "event_count": len(window),
            "events": window,
        }

    def subscribe_event_type(self, event_type: str,
                              callback: Callable) -> Dict[str, Any]:
        """Subscribe to specific event types."""
        self._op_count += 1
        count = self._distributor.subscribe_type(event_type, callback)
        return {"status": "ok", "event_type": event_type, "subscriber_count": count}

    def subscribe_all_events(self, callback: Callable) -> Dict[str, Any]:
        """Subscribe to all events."""
        self._op_count += 1
        count = self._distributor.subscribe_all(callback)
        return {"status": "ok", "global_subscriber_count": count}

    def get_window_aggregation(self) -> Dict[str, Any]:
        """Get time-window aggregated stats."""
        self._op_count += 1
        return {
            "status": "ok",
            "current_game_time": self._latest_game_time,
            "aggregation": self._aggregator.get_aggregation(self._latest_game_time),
        }

    def get_kill_feed(self, limit: int = 20) -> Dict[str, Any]:
        """Get recent kills in chronological order."""
        self._op_count += 1
        kills = [e for e in self._processed_events
                 if e.get("EventName") == "ChampionKill"]
        return {
            "status": "ok",
            "kills": list(kills)[-limit:],
            "total_kills": len(kills),
        }

    def get_objective_timeline(self) -> Dict[str, Any]:
        """Get ordered timeline of objective events."""
        self._op_count += 1
        objectives = [e for e in self._processed_events
                      if e.get("_category") == EventCategory.OBJECTIVE]
        return {
            "status": "ok",
            "objectives": list(objectives),
            "enricher_stats": self._enricher.get_stats(),
        }

    def get_stats(self) -> Dict[str, Any]:
        """Full diagnostic stats."""
        self._op_count += 1
        return {
            "status": "ok",
            "op_count": self._op_count,
            "process_count": self._process_count,
            "batch_count": self._batch_count,
            "total_events": len(self._processed_events),
            "latest_game_time": self._latest_game_time,
            "event_types_seen": list(self._event_type_index.keys()),
            "dedup": self._dedup.get_stats(),
            "aggregator": self._aggregator.get_stats(),
            "enricher": self._enricher.get_stats(),
            "distributor": self._distributor.get_stats(),
        }
