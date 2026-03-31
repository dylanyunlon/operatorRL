"""
OpponentModelTrainingExporter — Exports opponent modeling data for training.

Architecture (拿来主义):
  historical_training_exporter.py — training data export patterns
  cross_game_training_data_formatter.py（M673）— data formatting

Location: integrations/lol-history/src/lol_history/opponent_model_training_exporter.py
"""
from __future__ import annotations
import logging, time, json
from typing import Any, Callable, Dict, List, Optional
logger = logging.getLogger(__name__)
_EVOLUTION_KEY = "integrations.lol_history.opponent_model_training_exporter.v1"

def _safe_div(a, b, d=0.0): return a / b if b else d

class OpponentModelTrainingExporter:
    """Exports opponent profile + behavior data as training datasets.

    Public API: add_sample, export_batch, get_dataset_stats, set_format, get_stats
    """
    def __init__(self, export_format: str = "jsonl") -> None:
        self.evolution_callback: Optional[Callable] = None
        self._op_count = 0
        self._samples: List[Dict[str, Any]] = []
        self._export_format = export_format
        self._export_count = 0
        self._label_counts: Dict[str, int] = {}

    def _fire(self, et, data):
        if self.evolution_callback:
            self.evolution_callback({"type": et, "key": _EVOLUTION_KEY, **data})

    def add_sample(self, opponent_profile: Dict[str, Any], game_state: Dict[str, Any],
                    label: str, outcome: Dict[str, Any] = None) -> Dict[str, Any]:
        self._op_count += 1
        sample = {"features": {**opponent_profile, **game_state},
                  "label": label, "outcome": outcome or {}, "timestamp": time.time()}
        self._samples.append(sample)
        self._label_counts[label] = self._label_counts.get(label, 0) + 1
        return {"status": "ok", "total_samples": len(self._samples)}

    def export_batch(self, max_samples: int = 0) -> Dict[str, Any]:
        self._op_count += 1
        n = max_samples if max_samples > 0 else len(self._samples)
        batch = self._samples[:n]
        self._samples = self._samples[n:]
        self._export_count += len(batch)
        if self._export_format == "jsonl":
            data = [json.dumps(s, default=str) for s in batch]
        else:
            data = batch
        self._fire("batch_exported", {"count": len(batch)})
        return {"status": "ok", "exported": len(batch), "format": self._export_format,
                "data": data, "remaining": len(self._samples)}

    def set_format(self, fmt: str) -> Dict[str, Any]:
        self._op_count += 1
        self._export_format = fmt
        return {"status": "ok", "format": fmt}

    def get_dataset_stats(self) -> Dict[str, Any]:
        self._op_count += 1
        return {"status": "ok", "total_samples": len(self._samples),
                "exported": self._export_count, "label_distribution": dict(self._label_counts)}

    def get_stats(self) -> Dict[str, Any]:
        return {"samples": len(self._samples), "exported": self._export_count,
                "labels": len(self._label_counts), "total_ops": self._op_count}
