"""
Fiddler Training Pipeline — capture → clean → label → training sample.

Four-stage pipeline that transforms raw Fiddler-captured HTTP packets
into labelled training samples for the RL training loop.

Location: extensions/fiddler_bridge/src/fiddler_training_pipeline.py

Reference (拿来主义):
  - Akagi/akagi/misc.py: tile encoding utilities
  - DI-star/distar/ctools/utils/data_helper.py: data cleaning pipelines
  - integrations/lol/src/lol_agent/training_data_sqlite.py: sample schema
  - integrations/lol/src/lol_agent/reward_shaper.py: label/reward assignment
  - agentos/governance/data_pipeline.py: stage-based pipeline orchestration

Design Notes (Knuth-level critique):
  User:
    - process() always returns a dict with training_ready flag — never throws.
    - game_phase labelling uses configurable time thresholds.
  System:
    - Stage functions are pure — no side effects except stats accumulation.
    - batch_process is a thin map — no hidden state mutation between items.
"""

from __future__ import annotations

import copy
import logging
import math
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "extensions.fiddler_bridge.fiddler_training_pipeline.v1"

# ---------------------------------------------------------------------------
# Game-phase thresholds (seconds) — tunable per game
# ---------------------------------------------------------------------------
_PHASE_THRESHOLDS: Dict[str, Tuple[float, float]] = {
    "early": (0.0, 900.0),        # 0–15 min
    "mid":   (900.0, 1800.0),     # 15–30 min
    "late":  (1800.0, float("inf")),
}

# Required fields for a sample to be considered valid
_REQUIRED_FIELDS: Tuple[str, ...] = ("game_time",)

# Numeric fields that should be float
_NUMERIC_FIELDS: Tuple[str, ...] = ("game_time", "gold", "xp", "cs", "vision_score")

# Known action-triggering events
_ACTION_EVENTS: Dict[str, str] = {
    "DragonKill": "objective_take",
    "BaronKill": "objective_take",
    "HeraldKill": "objective_take",
    "TurretKill": "push",
    "ChampionKill": "combat",
    "InhibKill": "push",
    "GameEnd": "terminal",
    "Multikill": "combat",
    "Ace": "combat",
}


# ===========================================================================
# Stage implementations
# ===========================================================================

class _CleanStage:
    """Remove nulls, normalise timestamps, coerce numeric types.

    Reference: DI-star data_helper.py — normalisation of raw game states.
    """

    @staticmethod
    def execute(raw: Dict[str, Any]) -> Dict[str, Any]:
        out: Dict[str, Any] = {}

        for k, v in raw.items():
            if v is None:
                continue
            out[k] = v

        # --- Timestamp normalisation ---
        if out.get("timestamp_ms"):
            gt = out.get("game_time", 0)
            if isinstance(gt, (int, float)) and gt > 100_000:
                out["game_time"] = gt / 1000.0
            out.pop("timestamp_ms", None)

        # --- Numeric coercion ---
        for field in _NUMERIC_FIELDS:
            if field in out:
                try:
                    out[field] = float(out[field])
                except (ValueError, TypeError):
                    pass  # leave as-is; validator will catch

        return out


class _LabelStage:
    """Add game_phase and action_label annotations.

    Reference: integrations/lol reward_shaper.py — reward by game state.
    """

    @staticmethod
    def execute(cleaned: Dict[str, Any]) -> Dict[str, Any]:
        out = dict(cleaned)

        # --- Game phase ---
        gt = out.get("game_time", 0.0)
        phase = "early"
        if isinstance(gt, (int, float)):
            for p, (lo, hi) in _PHASE_THRESHOLDS.items():
                if lo <= gt < hi:
                    phase = p
                    break
        out["game_phase"] = phase

        # --- Action label ---
        event = out.get("event", "")
        if event in _ACTION_EVENTS:
            out["action_label"] = _ACTION_EVENTS[event]
        else:
            # default heuristic: gold-based
            gold = out.get("gold", 0)
            if isinstance(gold, (int, float)):
                if gold < 2000:
                    out["action_label"] = "farm"
                elif gold < 6000:
                    out["action_label"] = "trade"
                else:
                    out["action_label"] = "teamfight"
            else:
                out["action_label"] = "unknown"

        return out


class _ValidateStage:
    """Check that the labelled sample has all required fields and sane types."""

    @staticmethod
    def execute(labelled: Dict[str, Any]) -> bool:
        for field in _REQUIRED_FIELDS:
            if field not in labelled:
                return False
            val = labelled[field]
            if not isinstance(val, (int, float)):
                return False
        return True


class _OutputStage:
    """Stamp the sample with metadata and a training_ready flag."""

    @staticmethod
    def execute(labelled: Dict[str, Any], valid: bool) -> Dict[str, Any]:
        out = dict(labelled)
        out["training_ready"] = valid
        out["pipeline_ts"] = time.time()
        out["pipeline_version"] = _EVOLUTION_KEY
        return out


# ===========================================================================
# Pipeline statistics
# ===========================================================================

class _PipelineStats:
    __slots__ = (
        "total_processed",
        "valid_count",
        "rejected_count",
        "stage_timings",
    )

    def __init__(self) -> None:
        self.total_processed: int = 0
        self.valid_count: int = 0
        self.rejected_count: int = 0
        self.stage_timings: Dict[str, float] = {
            "clean": 0.0,
            "label": 0.0,
            "validate": 0.0,
            "output": 0.0,
        }

    def record(self, valid: bool, timings: Dict[str, float]) -> None:
        self.total_processed += 1
        if valid:
            self.valid_count += 1
        else:
            self.rejected_count += 1
        for stage, dt in timings.items():
            self.stage_timings[stage] = self.stage_timings.get(stage, 0.0) + dt

    def snapshot(self) -> Dict[str, Any]:
        return {
            "total_processed": self.total_processed,
            "valid_count": self.valid_count,
            "rejected_count": self.rejected_count,
            "valid_rate": (self.valid_count / self.total_processed) if self.total_processed > 0 else 0.0,
            "stage_timings": dict(self.stage_timings),
        }


# ===========================================================================
# Main class
# ===========================================================================

class FiddlerTrainingPipeline:
    """Four-stage training pipeline: capture → clean → label → output.

    Attributes:
        sample_count: Total samples processed.
        stage_names: Ordered list of stage names.
        evolution_callback: Optional callback for self-evolution events.

    Reference (拿来主义):
        - DI-star ctools data_helper: normalisation + augmentation stages
        - agentos governance data_pipeline: stage orchestration pattern
    """

    def __init__(
        self,
        *,
        phase_thresholds: Dict[str, Tuple[float, float]] | None = None,
        required_fields: Sequence[str] | None = None,
    ) -> None:
        if phase_thresholds:
            global _PHASE_THRESHOLDS
            _PHASE_THRESHOLDS = dict(phase_thresholds)

        self._stats = _PipelineStats()
        self.evolution_callback: Optional[Callable[[Dict[str, Any]], None]] = None

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def sample_count(self) -> int:
        return self._stats.total_processed

    @property
    def stage_names(self) -> List[str]:
        return ["capture", "clean", "label", "output"]

    # ------------------------------------------------------------------
    # Individual stages (exposed for advanced callers)
    # ------------------------------------------------------------------

    def clean(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        """Run the clean stage on a raw packet."""
        return _CleanStage.execute(raw)

    def label(self, cleaned: Dict[str, Any]) -> Dict[str, Any]:
        """Run the label stage on a cleaned packet."""
        return _LabelStage.execute(cleaned)

    # ------------------------------------------------------------------
    # Full pipeline
    # ------------------------------------------------------------------

    def process(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        """Run the full pipeline on a single raw packet.

        Returns a dict with ``training_ready`` indicating validity.
        """
        timings: Dict[str, float] = {}

        # Stage 1: Clean
        t0 = time.monotonic()
        cleaned = _CleanStage.execute(raw)
        timings["clean"] = time.monotonic() - t0

        # Stage 2: Label
        t0 = time.monotonic()
        labelled = _LabelStage.execute(cleaned)
        timings["label"] = time.monotonic() - t0

        # Stage 3: Validate
        t0 = time.monotonic()
        valid = _ValidateStage.execute(labelled)
        timings["validate"] = time.monotonic() - t0

        # Stage 4: Output
        t0 = time.monotonic()
        result = _OutputStage.execute(labelled, valid)
        timings["output"] = time.monotonic() - t0

        # Record stats
        self._stats.record(valid, timings)

        # Evolution
        self._fire_evolution({
            "action": "process",
            "valid": valid,
            "game_phase": labelled.get("game_phase", "unknown"),
        })

        return result

    def batch_process(self, raws: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Process a batch of raw packets."""
        return [self.process(r) for r in raws]

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def get_stats(self) -> Dict[str, Any]:
        return self._stats.snapshot()

    # ------------------------------------------------------------------
    # Evolution
    # ------------------------------------------------------------------

    def _fire_evolution(self, event: Dict[str, Any]) -> None:
        event.setdefault("component", _EVOLUTION_KEY)
        event.setdefault("ts", time.time())
        cb = self.evolution_callback
        if cb is not None:
            try:
                cb(event)
            except Exception:
                logger.exception("evolution_callback raised in FiddlerTrainingPipeline")

    # ------------------------------------------------------------------
    # Describe / repr
    # ------------------------------------------------------------------

    def describe(self) -> Dict[str, Any]:
        return {
            "component": _EVOLUTION_KEY,
            "stages": self.stage_names,
            "stats": self.get_stats(),
        }

    def __repr__(self) -> str:  # pragma: no cover
        s = self._stats
        return (
            f"FiddlerTrainingPipeline(processed={s.total_processed}, "
            f"valid={s.valid_count}, rejected={s.rejected_count})"
        )


# ---------------------------------------------------------------------------
# Module-level convenience singleton
# ---------------------------------------------------------------------------
default_pipeline: FiddlerTrainingPipeline = FiddlerTrainingPipeline()
