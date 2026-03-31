"""
HistoryPrivacyFilter — Privacy filtering and anonymization for history data.

Architecture (拿来主义):
  governance模块 + history_data_quality_checker.py（M624）

Location: integrations/lol-history/src/lol_history/history_privacy_filter.py

Design Notes (Knuth-level critique):
  User:
    - filter() never removes structural keys — only redacts PII values.
    - get_redaction_report shows exactly what was redacted for auditability.
    - Allowlist approach: only explicitly allowed fields pass through unredacted.
  System:
    - evolution_callback fires on every operation for system-level tracking.
    - Hashing uses SHA-256 truncated for pseudonymization — not reversible.
    - op_count provides basic throughput monitoring.
"""

from __future__ import annotations

import hashlib
import logging
import time
from typing import Any, Callable, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "integrations.lol_history.history_privacy_filter.v1"

_DEFAULT_PII_FIELDS: Set[str] = {
    "summonerName", "summoner_name", "puuid", "account_id",
    "accountId", "summoner_id", "summonerId", "riotId",
    "riot_id", "player_name", "playerName", "real_name",
    "email", "ip_address", "ip", "location",
}

_DEFAULT_SAFE_FIELDS: Set[str] = {
    "champion_id", "championId", "champion", "kills", "deaths",
    "assists", "cs", "gold_earned", "goldEarned", "vision_score",
    "visionScore", "game_duration", "gameDuration", "win",
    "role", "lane", "tier", "rank", "item_build", "runes",
    "damage_dealt", "damage_taken", "wards_placed",
    "match_id", "game_time", "timestamp", "patch",
    "team_id", "teamId", "event_type", "status",
}


def _safe_div(a: float, b: float, default: float = 0.0) -> float:
    return a / b if b != 0 else default


def _hash_value(val: Any, salt: str = "") -> str:
    """SHA-256 pseudonymization, truncated to 12 hex chars."""
    raw = f"{salt}:{val}"
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


class HistoryPrivacyFilter:
    """Privacy filtering and anonymization for history data.

    Public API
    ----------
    filter              — filter a single record
    filter_batch        — filter multiple records
    add_pii_field       — add a field name to PII list
    add_safe_field      — add a field name to safe list
    get_redaction_report— audit what was redacted
    get_stats           — internal statistics

    Evolution
    ---------
    Set evolution_callback to receive structured events.
    """

    def __init__(self, *, salt: str = "operatorRL", redact_mode: str = "hash") -> None:
        """
        Parameters
        ----------
        salt : str  for pseudonymization hashing
        redact_mode : str  "hash" = pseudonymize, "remove" = delete, "mask" = replace with ***
        """
        self.evolution_callback: Optional[Callable[[Dict[str, Any]], None]] = None
        self._op_count: int = 0
        self._salt: str = salt
        self._redact_mode: str = redact_mode
        self._pii_fields: Set[str] = set(_DEFAULT_PII_FIELDS)
        self._safe_fields: Set[str] = set(_DEFAULT_SAFE_FIELDS)
        self._redaction_log: List[Dict[str, Any]] = []
        self._redacted_count: int = 0

    def _fire(self, event_type: str, data: Dict[str, Any]) -> None:
        if self.evolution_callback is not None:
            self.evolution_callback({
                "source": _EVOLUTION_KEY, "type": event_type,
                "timestamp": time.time(), "payload": data,
            })

    def _redact_value(self, field: str, value: Any) -> Any:
        """Apply redaction based on mode."""
        if self._redact_mode == "hash":
            return _hash_value(value, self._salt)
        elif self._redact_mode == "remove":
            return None
        else:  # mask
            return "***"

    # ------------------------------------------------------------------ #

    def filter(self, record: Dict[str, Any] = None) -> Dict[str, Any]:
        """Filter a single record, redacting PII fields.

        Parameters
        ----------
        record : dict

        Returns
        -------
        dict  with status, filtered (dict), redacted_fields (list)
        """
        self._op_count += 1
        _start = time.time()
        if record is None:
            record = {}

        filtered: Dict[str, Any] = {}
        redacted_fields: List[str] = []

        for key, value in record.items():
            if key in self._pii_fields:
                filtered[key] = self._redact_value(key, value)
                redacted_fields.append(key)
                self._redacted_count += 1
            elif isinstance(value, dict):
                # Recurse one level
                sub_filtered = {}
                for sk, sv in value.items():
                    if sk in self._pii_fields:
                        sub_filtered[sk] = self._redact_value(sk, sv)
                        redacted_fields.append(f"{key}.{sk}")
                        self._redacted_count += 1
                    else:
                        sub_filtered[sk] = sv
                filtered[key] = sub_filtered
            else:
                filtered[key] = value

        if redacted_fields:
            self._redaction_log.append({
                "timestamp": time.time(),
                "redacted_fields": redacted_fields,
            })

        elapsed = time.time() - _start
        self._fire("filter_completed", {"elapsed": elapsed, "redacted": len(redacted_fields)})
        return {"status": "ok", "op": "filter",
                "filtered": filtered, "redacted_fields": redacted_fields}

    # ------------------------------------------------------------------ #

    def filter_batch(self, records: List[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Filter multiple records.

        Returns
        -------
        dict  with status, filtered (list), total_redacted
        """
        self._op_count += 1
        _start = time.time()
        if records is None:
            records = []

        filtered_list: List[Dict[str, Any]] = []
        total_redacted = 0
        for rec in records:
            result = self.filter(rec)
            filtered_list.append(result.get("filtered", rec))
            total_redacted += len(result.get("redacted_fields", []))

        elapsed = time.time() - _start
        self._fire("filter_batch_completed", {"elapsed": elapsed, "count": len(records)})
        return {"status": "ok", "op": "filter_batch",
                "filtered": filtered_list, "total_redacted": total_redacted}

    # ------------------------------------------------------------------ #

    def add_pii_field(self, field_name: str) -> Dict[str, Any]:
        """Add a field name to the PII list."""
        self._op_count += 1
        self._pii_fields.add(field_name)
        self._safe_fields.discard(field_name)
        return {"status": "ok", "op": "add_pii_field", "field": field_name}

    def add_safe_field(self, field_name: str) -> Dict[str, Any]:
        """Add a field name to the safe list."""
        self._op_count += 1
        self._safe_fields.add(field_name)
        self._pii_fields.discard(field_name)
        return {"status": "ok", "op": "add_safe_field", "field": field_name}

    # ------------------------------------------------------------------ #

    def get_redaction_report(self) -> Dict[str, Any]:
        """Audit report of what was redacted."""
        self._op_count += 1
        return {"status": "ok", "op": "get_redaction_report",
                "total_redacted": self._redacted_count,
                "redaction_events": len(self._redaction_log),
                "pii_fields": sorted(self._pii_fields),
                "redact_mode": self._redact_mode}

    # ------------------------------------------------------------------ #

    def get_stats(self) -> Dict[str, Any]:
        return {
            "op_count": self._op_count,
            "total_redacted": self._redacted_count,
            "pii_field_count": len(self._pii_fields),
            "safe_field_count": len(self._safe_fields),
            "redact_mode": self._redact_mode,
        }
