"""
common/latency_recorder — Apollo latency_recorder parity.

Apollo reference:
    modules/common/latency_recorder/latency_recorder.h
    modules/common/latency_recorder/latency_recorder.cc

Tracks end-to-end pipeline latency from canbus → perception → prediction
→ planning → control, recording per-component and total latency for
every message that flows through the pipeline.
"""

from modules.common.latency_recorder.latency_recorder import (
    LatencyRecorder,
    LatencyRecord,
    PipelineLatencyTracker,
)

__all__ = ["LatencyRecorder", "LatencyRecord", "PipelineLatencyTracker"]
