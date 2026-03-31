"""
HistoryBatchProcessor — Batch processing pipeline for history data.

Architecture (拿来主义):
  batch_inference_scheduler.py（M551）+ history_to_training_exporter.py（M606）

Location: integrations/lol-history/src/lol_history/history_batch_processor.py

Design Notes (Knuth-level critique):
  User:
    - submit() returns a batch_id for tracking — never blocks.
    - process() handles partial failures — successful items are preserved.
    - get_batch_status returns progress even for in-flight batches.
  System:
    - evolution_callback fires on every operation for system-level tracking.
    - Batch size is configurable to balance throughput vs memory.
    - op_count provides basic throughput monitoring.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "integrations.lol_history.history_batch_processor.v1"
_DEFAULT_BATCH_SIZE: int = 100


def _safe_div(a: float, b: float, default: float = 0.0) -> float:
    return a / b if b != 0 else default


class HistoryBatchProcessor:
    """Batch processing pipeline for history data.

    Public API
    ----------
    submit              — submit a batch of records for processing
    process             — process a submitted batch
    get_batch_status    — check status of a batch
    list_batches        — list all batches
    get_stats           — internal statistics

    Evolution
    ---------
    Set evolution_callback to receive structured events.
    """

    def __init__(self, *, batch_size: int = _DEFAULT_BATCH_SIZE,
                 processor: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None) -> None:
        self.evolution_callback: Optional[Callable[[Dict[str, Any]], None]] = None
        self._op_count: int = 0
        self._batch_size: int = batch_size
        self._processor: Optional[Callable] = processor
        self._batches: Dict[str, Dict[str, Any]] = {}
        self._processed_total: int = 0
        self._error_total: int = 0

    def _fire(self, event_type: str, data: Dict[str, Any]) -> None:
        if self.evolution_callback is not None:
            self.evolution_callback({
                "source": _EVOLUTION_KEY, "type": event_type,
                "timestamp": time.time(), "payload": data,
            })

    def _default_process(self, record: Dict[str, Any]) -> Dict[str, Any]:
        """Default pass-through processor."""
        return {**record, "_processed": True, "_processed_at": time.time()}

    # ------------------------------------------------------------------ #

    def submit(self, records: List[Dict[str, Any]] = None,
               metadata: Dict[str, Any] = None) -> Dict[str, Any]:
        """Submit a batch of records for processing.

        Parameters
        ----------
        records : list of dict
        metadata : dict  optional metadata for the batch

        Returns
        -------
        dict  with status, batch_id, record_count, chunk_count
        """
        self._op_count += 1
        _start = time.time()
        if records is None:
            records = []

        batch_id = str(uuid.uuid4())[:8]

        # Split into chunks
        chunks: List[List[Dict[str, Any]]] = []
        for i in range(0, len(records), self._batch_size):
            chunks.append(records[i:i + self._batch_size])

        self._batches[batch_id] = {
            "batch_id": batch_id,
            "status": "submitted",
            "chunks": chunks,
            "total_records": len(records),
            "processed_records": 0,
            "errors": 0,
            "results": [],
            "metadata": metadata or {},
            "submitted_at": time.time(),
            "completed_at": None,
        }

        elapsed = time.time() - _start
        self._fire("submit_completed", {"elapsed": elapsed, "batch_id": batch_id})
        return {"status": "ok", "op": "submit",
                "batch_id": batch_id, "record_count": len(records),
                "chunk_count": len(chunks)}

    # ------------------------------------------------------------------ #

    def process(self, batch_id: str) -> Dict[str, Any]:
        """Process a submitted batch.

        Parameters
        ----------
        batch_id : str

        Returns
        -------
        dict  with status, processed, errors, results
        """
        self._op_count += 1
        _start = time.time()

        batch = self._batches.get(batch_id)
        if batch is None:
            return {"status": "error", "reason": "unknown batch_id"}

        batch["status"] = "processing"
        proc_fn = self._processor or self._default_process

        results: List[Dict[str, Any]] = []
        errors = 0
        processed = 0

        for chunk in batch["chunks"]:
            for record in chunk:
                try:
                    result = proc_fn(record)
                    results.append(result)
                    processed += 1
                except Exception as exc:
                    errors += 1
                    results.append({"_error": str(exc), "_original": record})

        batch["results"] = results
        batch["processed_records"] = processed
        batch["errors"] = errors
        batch["status"] = "completed"
        batch["completed_at"] = time.time()

        self._processed_total += processed
        self._error_total += errors

        elapsed = time.time() - _start
        self._fire("process_completed", {
            "elapsed": elapsed, "batch_id": batch_id,
            "processed": processed, "errors": errors,
        })
        return {"status": "ok", "op": "process",
                "batch_id": batch_id, "processed": processed,
                "errors": errors, "results": results}

    # ------------------------------------------------------------------ #

    def get_batch_status(self, batch_id: str) -> Dict[str, Any]:
        """Check status of a batch.

        Returns
        -------
        dict  with batch metadata and progress
        """
        self._op_count += 1
        batch = self._batches.get(batch_id)
        if batch is None:
            return {"status": "error", "reason": "unknown batch_id"}

        return {"status": "ok", "op": "get_batch_status",
                "batch_id": batch_id,
                "batch_status": batch["status"],
                "total_records": batch["total_records"],
                "processed_records": batch["processed_records"],
                "errors": batch["errors"],
                "submitted_at": batch["submitted_at"],
                "completed_at": batch["completed_at"],
                "progress": round(_safe_div(
                    batch["processed_records"], batch["total_records"]), 4)}

    # ------------------------------------------------------------------ #

    def list_batches(self) -> Dict[str, Any]:
        """List all batches."""
        self._op_count += 1
        summaries = []
        for bid, b in self._batches.items():
            summaries.append({
                "batch_id": bid,
                "status": b["status"],
                "total_records": b["total_records"],
                "processed_records": b["processed_records"],
                "errors": b["errors"],
            })
        return {"status": "ok", "op": "list_batches", "batches": summaries}

    # ------------------------------------------------------------------ #

    def get_stats(self) -> Dict[str, Any]:
        return {
            "op_count": self._op_count,
            "total_batches": len(self._batches),
            "processed_total": self._processed_total,
            "error_total": self._error_total,
            "batch_size": self._batch_size,
        }
