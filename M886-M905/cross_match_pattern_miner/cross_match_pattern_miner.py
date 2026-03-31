#!/usr/bin/env python3
"""
M902 — CrossMatchPatternMiner
===============================
Mines opponent behavioral patterns across multiple game sessions.

Reference: M866-M885 cross_game_intel_fusion pattern
"""
from __future__ import annotations
import asyncio, collections, json, logging, math, os, sqlite3, time, hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
from enum import Enum, auto
from pathlib import Path

logger = logging.getLogger("M902.CrossMatchPatternMiner")


@dataclass
class OpponentPattern:
    puuid: str
    pattern_type: str
    confidence: float
    evidence_count: int
    description: str
    first_seen: Optional[datetime] = None
    last_seen: Optional[datetime] = None

    def to_dict(self) -> Dict[str, Any]:
        return {"puuid": self.puuid[:12], "type": self.pattern_type,
                "confidence": round(self.confidence, 2), "evidence": self.evidence_count,
                "desc": self.description}


class CrossMatchPatternMiner:
    """
    Mines behavioral patterns across multiple game sessions.
    Builds opponent profiles that improve over time as more data accumulates.
    """

    def __init__(self, history_analyzer=None, persistence=None):
        self._analyzer = history_analyzer
        self._persistence = persistence
        self._patterns: Dict[str, List[OpponentPattern]] = collections.defaultdict(list)
        self._stats = {"patterns_found": 0, "opponents_profiled": 0}
        logger.info("CrossMatchPatternMiner initialized")

    async def mine_patterns(self, puuid: str) -> List[OpponentPattern]:
        """Mine patterns for a specific opponent."""
        patterns = []

        if self._analyzer:
            profile = self._analyzer.get_opponent_profile(puuid)
            if profile:
                # Champion preference pattern
                if profile.champion_pool:
                    top = profile.champion_pool[0]
                    if top.games_played >= 5 and top.winrate >= 55:
                        patterns.append(OpponentPattern(
                            puuid=puuid, pattern_type="champion_specialist",
                            confidence=min(0.95, top.games_played / 20),
                            evidence_count=top.games_played,
                            description=f"Specialist on champion {top.champion_id} ({top.winrate:.0f}% WR)",
                        ))

                # Playstyle patterns
                for tag in profile.playstyle_tags:
                    patterns.append(OpponentPattern(
                        puuid=puuid, pattern_type=f"playstyle_{tag}",
                        confidence=0.7, evidence_count=profile.total_games_analyzed,
                        description=f"Playstyle: {tag}",
                    ))

                # Role preference
                if profile.preferred_roles:
                    patterns.append(OpponentPattern(
                        puuid=puuid, pattern_type="role_preference",
                        confidence=0.8, evidence_count=profile.total_games_analyzed,
                        description=f"Prefers: {', '.join(profile.preferred_roles[:2])}",
                    ))

        self._patterns[puuid] = patterns
        self._stats["patterns_found"] += len(patterns)
        if patterns:
            self._stats["opponents_profiled"] += 1
        return patterns

    async def mine_team(self, puuids: List[str]) -> Dict[str, List[Dict]]:
        result = {}
        for puuid in puuids:
            pats = await self.mine_patterns(puuid)
            result[puuid[:12]] = [p.to_dict() for p in pats]
        return result

    def get_patterns(self, puuid: str) -> List[Dict[str, Any]]:
        return [p.to_dict() for p in self._patterns.get(puuid, [])]

    def export_stats(self) -> Dict[str, Any]:
        return {"miner_stats": self._stats, "opponents_tracked": len(self._patterns)}



# ---------------------------------------------------------------------------
# Extended CrossMatchPatternMiner utilities
# ---------------------------------------------------------------------------

class TemporalPatternAnalyzer:
    """Analyzes patterns over time to detect behavioral changes."""

    def __init__(self):
        self._observations: Dict[str, List[Dict[str, Any]]] = collections.defaultdict(list)

    def record(self, puuid: str, game_time: float, observation: Dict[str, Any]):
        observation["game_time"] = game_time
        observation["recorded_at"] = datetime.now(timezone.utc).isoformat()
        self._observations[puuid].append(observation)

    def detect_trend(self, puuid: str, metric: str) -> Optional[str]:
        """Detect if a metric is trending up, down, or stable."""
        obs = self._observations.get(puuid, [])
        values = [o.get(metric, 0) for o in obs if metric in o]
        if len(values) < 3:
            return None

        # Simple linear regression
        n = len(values)
        x_mean = (n - 1) / 2
        y_mean = sum(values) / n
        numerator = sum((i - x_mean) * (v - y_mean) for i, v in enumerate(values))
        denominator = sum((i - x_mean) ** 2 for i in range(n))

        if denominator == 0:
            return "stable"

        slope = numerator / denominator
        if slope > 0.1:
            return "improving"
        elif slope < -0.1:
            return "declining"
        return "stable"

    def detect_anomalies(self, puuid: str, metric: str, threshold: float = 2.0) -> List[Dict]:
        """Detect anomalous values using z-score method."""
        obs = self._observations.get(puuid, [])
        values = [o.get(metric, 0) for o in obs if metric in o]
        if len(values) < 5:
            return []

        mean = sum(values) / len(values)
        std = (sum((v - mean) ** 2 for v in values) / len(values)) ** 0.5
        if std == 0:
            return []

        anomalies = []
        for i, v in enumerate(values):
            z = abs(v - mean) / std
            if z > threshold:
                anomalies.append({"index": i, "value": v, "z_score": round(z, 2)})
        return anomalies


class ChampionTransitionAnalyzer:
    """Analyzes champion picks across games to detect adaptation patterns."""

    def __init__(self):
        self._pick_sequences: Dict[str, List[int]] = collections.defaultdict(list)

    def record_pick(self, puuid: str, champion_id: int):
        self._pick_sequences[puuid].append(champion_id)

    def detect_adaptation(self, puuid: str) -> Optional[Dict[str, Any]]:
        """Detect if player is adapting champion picks based on losses."""
        picks = self._pick_sequences.get(puuid, [])
        if len(picks) < 3:
            return None

        unique = len(set(picks))
        total = len(picks)
        diversity = unique / total

        # Detect one-trick behavior
        most_common = collections.Counter(picks).most_common(1)
        if most_common:
            top_champ, top_count = most_common[0]
            if top_count / total >= 0.7:
                return {
                    "type": "one_trick",
                    "champion_id": top_champ,
                    "frequency": round(top_count / total * 100, 1),
                    "diversity": round(diversity, 2),
                }

        # Detect frequent switching
        switches = sum(1 for i in range(1, len(picks)) if picks[i] != picks[i-1])
        switch_rate = switches / max(len(picks) - 1, 1)
        if switch_rate >= 0.8:
            return {
                "type": "frequent_switcher",
                "switch_rate": round(switch_rate * 100, 1),
                "unique_champions": unique,
            }

        return None


class TiltDetector:
    """Detects if an opponent is likely tilted based on game patterns."""

    @staticmethod
    def analyze(recent_results: List[bool], recent_kdas: List[float]) -> Dict[str, Any]:
        if len(recent_results) < 3:
            return {"tilted": False, "confidence": 0}

        # Loss streak
        loss_streak = 0
        for r in reversed(recent_results):
            if not r:
                loss_streak += 1
            else:
                break

        # Declining KDA
        kda_declining = False
        if len(recent_kdas) >= 3:
            kda_declining = all(recent_kdas[i] > recent_kdas[i+1]
                               for i in range(len(recent_kdas) - 2, min(len(recent_kdas) - 1, len(recent_kdas))))

        tilt_score = 0
        if loss_streak >= 3:
            tilt_score += 3
        elif loss_streak >= 2:
            tilt_score += 1
        if kda_declining:
            tilt_score += 2
        if recent_kdas and recent_kdas[-1] < 1.0:
            tilt_score += 1

        return {
            "tilted": tilt_score >= 3,
            "tilt_score": tilt_score,
            "loss_streak": loss_streak,
            "kda_declining": kda_declining,
            "confidence": min(0.9, tilt_score / 6),
        }


class PatternExporter:
    """Exports mined patterns to various formats for integration."""

    @staticmethod
    def to_summary(patterns: Dict[str, List[OpponentPattern]]) -> str:
        lines = ["=== Opponent Pattern Summary ==="]
        for puuid, pats in patterns.items():
            lines.append(f"\nPlayer {puuid[:12]}:")
            for p in pats:
                lines.append(f"  [{p.pattern_type}] {p.description} (conf={p.confidence:.0%})")
        return "\n".join(lines)

    @staticmethod
    def to_json(patterns: Dict[str, List[OpponentPattern]]) -> str:
        data = {}
        for puuid, pats in patterns.items():
            data[puuid] = [p.to_dict() for p in pats]
        return json.dumps(data, indent=2)

    @staticmethod
    def get_actionable_insights(patterns: Dict[str, List[OpponentPattern]]) -> List[Dict[str, Any]]:
        insights = []
        for puuid, pats in patterns.items():
            for p in pats:
                if p.confidence >= 0.7:
                    insights.append({
                        "target": puuid[:12],
                        "insight": p.description,
                        "confidence": p.confidence,
                        "action_type": p.pattern_type,
                    })
        insights.sort(key=lambda x: x["confidence"], reverse=True)
        return insights



# ---------------------------------------------------------------------------
# Extended CrossMatchPatternMiner utilities — metrics, serialization, diagnostics
# ---------------------------------------------------------------------------

class CrossMatchPatternMinerMetrics:
    """Collects performance metrics for CrossMatchPatternMiner."""

    def __init__(self):
        self._operation_times: List[float] = []
        self._error_counts: Dict[str, int] = collections.defaultdict(int)
        self._invocations = 0

    def record_operation(self, duration_ms: float):
        self._invocations += 1
        self._operation_times.append(duration_ms)
        if len(self._operation_times) > 1000:
            self._operation_times = self._operation_times[-1000:]

    def record_error(self, error_type: str):
        self._error_counts[error_type] += 1

    def get_summary(self) -> Dict[str, Any]:
        if not self._operation_times:
            return {"invocations": self._invocations, "errors": dict(self._error_counts)}
        sorted_times = sorted(self._operation_times)
        n = len(sorted_times)
        return {
            "invocations": self._invocations,
            "avg_ms": round(sum(sorted_times) / n, 2),
            "p50_ms": round(sorted_times[n // 2], 2),
            "p95_ms": round(sorted_times[int(n * 0.95)], 2),
            "p99_ms": round(sorted_times[int(n * 0.99)], 2),
            "max_ms": round(sorted_times[-1], 2),
            "errors": dict(self._error_counts),
        }


class CrossMatchPatternMinerSerializer:
    """Serialization utilities for CrossMatchPatternMiner state."""

    @staticmethod
    def serialize_state(state: Dict[str, Any]) -> str:
        return json.dumps(state, indent=2, ensure_ascii=False, default=str)

    @staticmethod
    def deserialize_state(data: str) -> Dict[str, Any]:
        try:
            return json.loads(data)
        except json.JSONDecodeError as exc:
            logger.error("Deserialize error: %s", exc)
            return {}

    @staticmethod
    def compute_state_hash(state: Dict[str, Any]) -> str:
        serialized = json.dumps(state, sort_keys=True, default=str)
        return hashlib.sha256(serialized.encode()).hexdigest()[:16]


class CrossMatchPatternMinerDiagnostics:
    """Diagnostic tools for CrossMatchPatternMiner troubleshooting."""

    def __init__(self, instance):
        self._instance = instance
        self._diagnostic_log: List[Dict[str, Any]] = []

    def run_self_test(self) -> Dict[str, Any]:
        """Run basic self-diagnostics."""
        results = {
            "module": "CrossMatchPatternMiner",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "checks": [],
        }

        # Check 1: Instance exists
        results["checks"].append({
            "name": "instance_valid",
            "passed": self._instance is not None,
        })

        # Check 2: Has export_stats method
        has_stats = hasattr(self._instance, "export_stats")
        results["checks"].append({
            "name": "has_export_stats",
            "passed": has_stats,
        })

        # Check 3: export_stats returns valid data
        if has_stats:
            try:
                stats = self._instance.export_stats()
                results["checks"].append({
                    "name": "stats_callable",
                    "passed": isinstance(stats, dict),
                    "detail": f"{len(stats)} keys returned",
                })
            except Exception as exc:
                results["checks"].append({
                    "name": "stats_callable",
                    "passed": False,
                    "detail": str(exc),
                })

        # Check 4: Memory footprint estimate
        import sys
        size = sys.getsizeof(self._instance)
        results["checks"].append({
            "name": "memory_footprint",
            "passed": size < 10_000_000,  # 10MB threshold
            "detail": f"{size} bytes",
        })

        self._diagnostic_log.append(results)
        return results

    def get_diagnostic_history(self) -> List[Dict[str, Any]]:
        return list(self._diagnostic_log)


class CrossMatchPatternMinerEventLogger:
    """Structured event logger for CrossMatchPatternMiner with rotation."""

    def __init__(self, max_events: int = 500):
        self._events: List[Dict[str, Any]] = []
        self._max = max_events

    def log(self, event_type: str, data: Optional[Dict] = None, level: str = "info"):
        self._events.append({
            "type": event_type,
            "level": level,
            "data": data or {},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        if len(self._events) > self._max:
            self._events = self._events[-self._max:]

    def get_events(self, event_type: Optional[str] = None,
                   level: Optional[str] = None,
                   limit: int = 50) -> List[Dict[str, Any]]:
        filtered = self._events
        if event_type:
            filtered = [e for e in filtered if e["type"] == event_type]
        if level:
            filtered = [e for e in filtered if e["level"] == level]
        return filtered[-limit:]

    def count_by_type(self) -> Dict[str, int]:
        return dict(collections.Counter(e["type"] for e in self._events))

    def count_by_level(self) -> Dict[str, int]:
        return dict(collections.Counter(e["level"] for e in self._events))

    @property
    def total(self) -> int:
        return len(self._events)



class CrossMatchPatternMinerConfigStore:
    """Configuration store for runtime settings."""
    def __init__(self):
        self._config: Dict[str, Any] = {}
        self._defaults: Dict[str, Any] = {}
        self._change_log: List[Dict[str, Any]] = []

    def set_default(self, key: str, value: Any):
        self._defaults[key] = value
        if key not in self._config:
            self._config[key] = value

    def get(self, key: str, fallback: Any = None) -> Any:
        return self._config.get(key, self._defaults.get(key, fallback))

    def set(self, key: str, value: Any):
        old = self._config.get(key)
        self._config[key] = value
        self._change_log.append({
            "key": key, "old": old, "new": value,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    def reset_to_defaults(self):
        self._config = dict(self._defaults)

    def get_all(self) -> Dict[str, Any]:
        merged = dict(self._defaults)
        merged.update(self._config)
        return merged

    def get_changes(self) -> List[Dict[str, Any]]:
        return list(self._change_log)


class CrossMatchPatternMinerHealthCheck:
    """Periodic health check for the module."""
    def __init__(self, instance):
        self._instance = instance
        self._check_results: List[Dict[str, Any]] = []
        self._consecutive_failures = 0

    def check(self) -> Dict[str, Any]:
        result = {
            "module": "CrossMatchPatternMiner",
            "healthy": True,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "checks": [],
        }
        # Verify instance is responsive
        try:
            if hasattr(self._instance, "export_stats"):
                stats = self._instance.export_stats()
                result["checks"].append({"name": "export_stats", "ok": True})
            self._consecutive_failures = 0
        except Exception as exc:
            result["healthy"] = False
            result["checks"].append({"name": "export_stats", "ok": False, "error": str(exc)})
            self._consecutive_failures += 1

        result["consecutive_failures"] = self._consecutive_failures
        self._check_results.append(result)
        if len(self._check_results) > 100:
            self._check_results = self._check_results[-100:]
        return result

    def get_history(self) -> List[Dict[str, Any]]:
        return list(self._check_results)

    @property
    def is_healthy(self) -> bool:
        if not self._check_results:
            return True
        return self._check_results[-1].get("healthy", False)


class CrossMatchPatternMinerDataValidator:
    """Validates input and output data for the module."""

    @staticmethod
    def validate_dict(data: Dict[str, Any], required_keys: List[str]) -> Tuple[bool, List[str]]:
        errors = []
        for key in required_keys:
            if key not in data:
                errors.append(f"Missing required key: {key}")
        return len(errors) == 0, errors

    @staticmethod
    def validate_numeric_range(value: float, min_val: float, max_val: float,
                                field_name: str = "value") -> Tuple[bool, str]:
        if value < min_val or value > max_val:
            return False, f"{field_name} {value} outside range [{min_val}, {max_val}]"
        return True, ""

    @staticmethod
    def sanitize_string(s: str, max_length: int = 256) -> str:
        return s[:max_length].strip()
