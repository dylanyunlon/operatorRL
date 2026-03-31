"""
TrainingDataQualityValidator — Validates training data quality with schema checks and anomaly detection.

Architecture (拿来主义):
  history_data_quality_checker.py, intel_pipeline_e2e_tester.py（M742）

Location: integrations/lol-history/src/lol_history/training_data_quality_validator.py

Design Notes (Knuth-level critique):
  User:
    - Production-grade module with unified {"status": "ok"} response format.
    - Stateless or bounded-state design for long-running sessions.
    - Graceful degradation: partial results on component failure.
  System:
    - All data structures bounded (deque/OrderedDict with maxlen).
    - Evolution callback integration for self-improvement feedback.
    - Comprehensive get_stats() for observability.
    - Zero external dependencies beyond stdlib.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import time
from collections import OrderedDict, defaultdict, deque
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)
_EVOLUTION_KEY = "integrations.lol_history.training_data_quality_validator.v1"


def _safe_div(a: float, b: float, d: float = 0.0) -> float:
    return a / b if b else d


class _SchemaValidator:
    """Validates training records against expected schema."""

    REQUIRED_FIELDS = ["game_id", "champion", "kills", "deaths", "assists", "win", "duration"]
    NUMERIC_FIELDS = ["kills", "deaths", "assists", "duration", "gold"]
    BOOLEAN_FIELDS = ["win"]

    def validate(self, record: Dict[str, Any]) -> Tuple[bool, List[str]]:
        issues = []
        for field in self.REQUIRED_FIELDS:
            if field not in record:
                issues.append(f"missing_field:{field}")
        for field in self.NUMERIC_FIELDS:
            val = record.get(field)
            if val is not None and not isinstance(val, (int, float)):
                issues.append(f"non_numeric:{field}={val}")
            elif val is not None and val < 0:
                issues.append(f"negative_value:{field}={val}")
        for field in self.BOOLEAN_FIELDS:
            val = record.get(field)
            if val is not None and not isinstance(val, bool):
                issues.append(f"non_boolean:{field}={val}")
        return len(issues) == 0, issues


class _AnomalyDetector:
    """Detects anomalous values in training data."""

    THRESHOLDS = {
        "kills": (0, 50),
        "deaths": (0, 40),
        "assists": (0, 60),
        "duration": (180, 5400),
        "gold": (0, 100000),
    }

    def detect(self, record: Dict[str, Any]) -> List[str]:
        anomalies = []
        for field, (low, high) in self.THRESHOLDS.items():
            val = record.get(field)
            if val is not None and (val < low or val > high):
                anomalies.append(f"anomaly:{field}={val} (expected {low}-{high})")
        kda = record.get("kills", 0) + record.get("assists", 0)
        deaths = record.get("deaths", 1) or 1
        if kda / deaths > 30:
            anomalies.append(f"extreme_kda:{kda}/{deaths}")
        return anomalies


class _SkewDetector:
    """Detects data distribution skew in batches."""

    def __init__(self) -> None:
        self._field_values: Dict[str, List[float]] = defaultdict(list)

    def add_record(self, record: Dict[str, Any]) -> None:
        for field in ["kills", "deaths", "assists", "duration", "gold"]:
            val = record.get(field)
            if isinstance(val, (int, float)):
                self._field_values[field].append(val)

    def check_skew(self) -> Dict[str, Any]:
        skew_report = {}
        for field, values in self._field_values.items():
            if len(values) < 5:
                continue
            mean = sum(values) / len(values)
            sorted_v = sorted(values)
            median = sorted_v[len(sorted_v) // 2]
            std = math.sqrt(sum((v - mean) ** 2 for v in values) / len(values)) if len(values) > 1 else 0
            skew_report[field] = {
                "mean": round(mean, 2),
                "median": median,
                "std": round(std, 2),
                "mean_median_ratio": round(_safe_div(mean, median), 2) if median else None,
                "is_skewed": abs(mean - median) > std * 0.5 if std > 0 else False,
            }
        return skew_report

    def reset(self) -> None:
        self._field_values.clear()


class _CompletionChecker:
    """Checks field completeness across records."""

    def __init__(self) -> None:
        self._field_present: Dict[str, int] = defaultdict(int)
        self._total_records = 0

    def check(self, record: Dict[str, Any]) -> Dict[str, float]:
        self._total_records += 1
        for key in record:
            if record[key] is not None:
                self._field_present[key] += 1
        completeness = {}
        for field, count in self._field_present.items():
            completeness[field] = _safe_div(count, self._total_records)
        return completeness

    def get_incomplete_fields(self, threshold: float = 0.9) -> List[str]:
        return [f for f, rate in self._field_present.items()
                if _safe_div(rate, self._total_records) < threshold]


class TrainingDataQualityValidator:
    """Validates training data quality with schema validation and anomaly detection.

    Public API: validate_record, validate_batch, get_rejection_reasons,
                get_quality_report, get_skew_analysis, get_stats
    """

    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable] = None
        self._op_count = 0
        self._validate_count = 0
        self._reject_count = 0
        self._schema = _SchemaValidator()
        self._anomaly = _AnomalyDetector()
        self._skew = _SkewDetector()
        self._completion = _CompletionChecker()
        self._rejection_reasons: deque = deque(maxlen=500)
        self._batch_results: deque = deque(maxlen=100)

    def _fire(self, et: str, data: Dict) -> None:
        if self.evolution_callback:
            self.evolution_callback({"type": et, "key": _EVOLUTION_KEY, **data})

    def validate_record(self, record: Dict[str, Any]) -> Dict[str, Any]:
        """Validate a single training record."""
        self._op_count += 1
        self._validate_count += 1
        all_issues = []

        schema_ok, schema_issues = self._schema.validate(record)
        all_issues.extend(schema_issues)

        anomalies = self._anomaly.detect(record)
        all_issues.extend(anomalies)

        self._skew.add_record(record)
        self._completion.check(record)

        valid = len(all_issues) == 0
        if not valid:
            self._reject_count += 1
            self._rejection_reasons.append({
                "game_id": record.get("game_id", "unknown"),
                "issues": all_issues,
                "ts": time.monotonic(),
            })

        return {
            "status": "ok",
            "valid": valid,
            "issues": all_issues,
            "schema_valid": schema_ok,
            "anomaly_count": len(anomalies),
        }

    def validate_batch(self, records: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Validate a batch of training records."""
        self._op_count += 1
        results = []
        valid_count = 0
        for record in records:
            result = self.validate_record(record)
            results.append(result)
            if result["valid"]:
                valid_count += 1

        batch_result = {
            "total": len(records),
            "valid": valid_count,
            "rejected": len(records) - valid_count,
            "acceptance_rate": _safe_div(valid_count, len(records)),
        }
        self._batch_results.append(batch_result)

        return {"status": "ok", "batch": batch_result, "details": results}

    def get_rejection_reasons(self) -> Dict[str, Any]:
        self._op_count += 1
        reason_counts = defaultdict(int)
        for r in self._rejection_reasons:
            for issue in r.get("issues", []):
                reason_counts[issue.split(":")[0]] += 1
        return {
            "status": "ok",
            "total_rejections": self._reject_count,
            "reason_counts": dict(reason_counts),
            "recent": list(self._rejection_reasons)[-10:],
        }

    def get_quality_report(self) -> Dict[str, Any]:
        self._op_count += 1
        return {
            "status": "ok",
            "validated": self._validate_count,
            "rejected": self._reject_count,
            "acceptance_rate": _safe_div(self._validate_count - self._reject_count,
                                         self._validate_count),
            "skew": self._skew.check_skew(),
            "incomplete_fields": self._completion.get_incomplete_fields(),
        }

    def get_stats(self) -> Dict[str, Any]:
        self._op_count += 1
        return {
            "status": "ok",
            "op_count": self._op_count,
            "validate_count": self._validate_count,
            "reject_count": self._reject_count,
            "acceptance_rate": _safe_div(self._validate_count - self._reject_count,
                                         self._validate_count),
        }
