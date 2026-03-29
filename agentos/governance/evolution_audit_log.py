"""
Evolution Audit Log — Immutable evolution history + compliance tracking.

Location: agentos/governance/evolution_audit_log.py
"""

from __future__ import annotations
import logging, time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)
_EVOLUTION_KEY: str = "agentos.governance.evolution_audit_log.v1"

class EvolutionAuditLog:
    """Immutable audit log for evolution events."""

    def __init__(self, max_entries: int = 10000) -> None:
        self._entries: list[dict[str, Any]] = []
        self._max_entries = max_entries
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def append(self, event_type: str, source: str, data: dict[str, Any]) -> int:
        entry = {"id": len(self._entries), "type": event_type, "source": source, "data": data, "timestamp": time.time()}
        self._entries.append(entry)
        if len(self._entries) > self._max_entries:
            self._entries = self._entries[-self._max_entries:]
        self._fire_evolution("audit_appended", {"type": event_type})
        return entry["id"]

    def query(self, event_type: str = None, source: str = None, limit: int = 50) -> list[dict[str, Any]]:
        result = self._entries
        if event_type:
            result = [e for e in result if e["type"] == event_type]
        if source:
            result = [e for e in result if e["source"] == source]
        return result[-limit:]

    def count(self) -> int:
        return len(self._entries)

    def _fire_evolution(self, event_type: str, payload: dict) -> None:
        if self.evolution_callback is not None:
            self.evolution_callback({"source": _EVOLUTION_KEY, "type": event_type, "timestamp": time.time(), "payload": payload})
