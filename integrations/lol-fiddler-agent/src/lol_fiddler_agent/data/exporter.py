"""
Data Exporter - Exports game session data to various formats.

Supports CSV, JSON Lines, Parquet (via pandas), and direct
DataFrame conversion for ML workflows.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from lol_fiddler_agent.ml.feature_extractor import FEATURE_NAMES, extract_features
from lol_fiddler_agent.models.game_snapshot import GameSnapshot

logger = logging.getLogger(__name__)


@dataclass
class ExportConfig:
    """Configuration for data export."""
    output_dir: str = "./exports"
    format: str = "jsonl"  # jsonl, csv, parquet
    include_features: bool = True
    include_raw_snapshot: bool = False
    max_file_size_mb: int = 100
    compress: bool = False


class DataExporter:
    """Exports game data to disk or memory buffers.

    Example::

        exporter = DataExporter(ExportConfig(output_dir="./data"))
        for snapshot in snapshots:
            exporter.add(snapshot)
        exporter.flush()
    """

    def __init__(self, config: Optional[ExportConfig] = None) -> None:
        self._config = config or ExportConfig()
        self._buffer: list[dict[str, Any]] = []
        self._export_count = 0
        self._total_rows = 0
        Path(self._config.output_dir).mkdir(parents=True, exist_ok=True)

    def add(self, snapshot: GameSnapshot) -> None:
        """Add a snapshot to the export buffer."""
        row: dict[str, Any] = {
            "snapshot_id": snapshot.snapshot_id,
            "game_time": snapshot.game_time,
            "game_phase": snapshot.game_phase,
            "champion": snapshot.my_champion,
            "team": snapshot.my_team,
            "level": snapshot.my_level,
            "gold": snapshot.my_gold,
            "health_pct": snapshot.my_health_pct,
            "gold_diff": snapshot.gold_difference,
            "kill_diff": snapshot.kill_difference,
        }

        if self._config.include_features:
            features = extract_features(snapshot)
            for name in FEATURE_NAMES:
                row[name] = features.get(name, 0.0)

        if self._config.include_raw_snapshot:
            row["raw_json"] = snapshot.to_json()

        self._buffer.append(row)
        self._total_rows += 1

    def flush(self) -> str:
        """Write buffer to disk and return filepath."""
        if not self._buffer:
            return ""

        self._export_count += 1
        timestamp = int(time.time())
        fmt = self._config.format

        if fmt == "jsonl":
            return self._write_jsonl(timestamp)
        elif fmt == "csv":
            return self._write_csv(timestamp)
        elif fmt == "parquet":
            return self._write_parquet(timestamp)
        else:
            logger.warning("Unknown format %s, falling back to jsonl", fmt)
            return self._write_jsonl(timestamp)

    def _write_jsonl(self, timestamp: int) -> str:
        filename = f"game_data_{timestamp}_{self._export_count}.jsonl"
        filepath = os.path.join(self._config.output_dir, filename)
        with open(filepath, "w") as f:
            for row in self._buffer:
                f.write(json.dumps(row, default=str) + "\n")
        count = len(self._buffer)
        self._buffer.clear()
        logger.info("Exported %d rows to %s", count, filepath)
        return filepath

    def _write_csv(self, timestamp: int) -> str:
        filename = f"game_data_{timestamp}_{self._export_count}.csv"
        filepath = os.path.join(self._config.output_dir, filename)
        if not self._buffer:
            return filepath
        fieldnames = list(self._buffer[0].keys())
        with open(filepath, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for row in self._buffer:
                writer.writerow(row)
        count = len(self._buffer)
        self._buffer.clear()
        logger.info("Exported %d rows to %s", count, filepath)
        return filepath

    def _write_parquet(self, timestamp: int) -> str:
        filename = f"game_data_{timestamp}_{self._export_count}.parquet"
        filepath = os.path.join(self._config.output_dir, filename)
        try:
            import pandas as pd
            df = pd.DataFrame(self._buffer)
            df.to_parquet(filepath, index=False)
            count = len(self._buffer)
            self._buffer.clear()
            logger.info("Exported %d rows to %s", count, filepath)
        except ImportError:
            logger.warning("pandas not available, falling back to jsonl")
            return self._write_jsonl(timestamp)
        return filepath

    def to_dataframe(self):
        """Convert buffer to pandas DataFrame without writing to disk."""
        try:
            import pandas as pd
            return pd.DataFrame(self._buffer)
        except ImportError:
            raise ImportError("pandas required for DataFrame conversion")

    @property
    def buffered_count(self) -> int:
        return len(self._buffer)

    @property
    def total_exported(self) -> int:
        return self._total_rows

    def get_stats(self) -> dict[str, Any]:
        return {
            "buffered": len(self._buffer),
            "total_rows": self._total_rows,
            "exports": self._export_count,
            "format": self._config.format,
            "output_dir": self._config.output_dir,
        }


# ── Evolution Integration (M279 — appended, 不增不删原有函数) ─────────────
_EVOLUTION_KEY = 'exporter'


class EvolvableDataExporter(DataExporter):
    """DataExporter with self-evolution callback + AgentLightning format."""

    def __init__(self, config=None) -> None:
        super().__init__(config)
        self._evolution_callback = None

    @property
    def evolution_callback(self):
        return self._evolution_callback

    @evolution_callback.setter
    def evolution_callback(self, cb):
        self._evolution_callback = cb

    def _fire_evolution(self, data: dict) -> None:
        import time as _time
        data.setdefault('module', _EVOLUTION_KEY)
        data.setdefault('timestamp', _time.time())
        if self._evolution_callback:
            try:
                self._evolution_callback(data)
            except Exception:
                pass

    def to_agentlightning_format(self, **kwargs) -> dict:
        """Convert snapshot data to AgentLightning training format."""
        row = {'format': 'agentlightning', 'module': _EVOLUTION_KEY}
        row.update(kwargs)
        return row

    def to_training_annotation(self, **kwargs) -> dict:
        annotation = {'module': _EVOLUTION_KEY}
        annotation.update(kwargs)
        return annotation
