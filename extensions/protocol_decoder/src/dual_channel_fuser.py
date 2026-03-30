"""
Dual Channel Fuser — Fiddler + Live Client Data fusion.

Merges data from two sources: (1) Fiddler-captured protocol traffic
and (2) Riot's Live Client Data API polls.  Fuses records with matching
timestamps (within a configurable tolerance) into enriched game-state
snapshots for downstream training.

Location: extensions/protocol_decoder/src/dual_channel_fuser.py

Reference (拿来主义):
  - Akagi MITM: dual data source (protobuf + mjai)
  - DI-star: multi-source observation merging
  - integrations/lol/src/lol_agent/live_danger_fuser.py: fusion pattern
  - extensions/protocol-decoder/src/dual_channel_fuser.py: planned location
  - agentos/governance/data_pipeline.py: pipeline stage merging

Design Notes (Knuth-level critique):
  User:
    - Configurable priority (fiddler vs liveclient) for conflicting fields.
    - Time tolerance prevents false fusions across game-time gaps.
    - clear() resets both buffers — clean slate between matches.
  System:
    - Fusion is O(N*M) worst case but bounded by buffer sizes.
    - Used timestamps are tracked to prevent double-fuse.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "extensions.protocol_decoder.dual_channel_fuser.v1"

_DEFAULT_TIME_TOLERANCE: float = 2.0  # seconds


class DualChannelFuser:
    """Fuse Fiddler-captured data with Live Client Data API results.

    Usage:
        fuser = DualChannelFuser(time_tolerance=2.0, priority="liveclient")
        fuser.ingest_fiddler({"game_time": 100.0, "extra": "X"})
        fuser.ingest_liveclient({"gameTime": 101.0, "allPlayers": [...]})
        fused = fuser.fuse()

    Attributes:
        fuse_count: Total fused records produced.
        fiddler_count: Number of Fiddler records ingested.
        liveclient_count: Number of Live Client records ingested.
        evolution_callback: Optional callback for self-evolution events.
    """

    def __init__(
        self,
        *,
        time_tolerance: float = _DEFAULT_TIME_TOLERANCE,
        priority: str = "liveclient",
    ) -> None:
        self._time_tolerance = time_tolerance
        self._priority = priority  # "fiddler" or "liveclient"

        self._fiddler_buf: List[Dict[str, Any]] = []
        self._liveclient_buf: List[Dict[str, Any]] = []
        self._fused_indices_f: Set[int] = set()
        self._fused_indices_l: Set[int] = set()

        self._fuse_count: int = 0

        self.evolution_callback: Optional[Callable[[Dict[str, Any]], None]] = None

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def fuse_count(self) -> int:
        return self._fuse_count

    @property
    def fiddler_count(self) -> int:
        return len(self._fiddler_buf)

    @property
    def liveclient_count(self) -> int:
        return len(self._liveclient_buf)

    # ------------------------------------------------------------------
    # Ingest
    # ------------------------------------------------------------------

    def ingest_fiddler(self, record: Dict[str, Any]) -> None:
        """Ingest a Fiddler-captured record."""
        record.setdefault("_ingest_ts", time.time())
        self._fiddler_buf.append(record)
        self._fire_evolution({"action": "ingest_fiddler"})

    def ingest_liveclient(self, record: Dict[str, Any]) -> None:
        """Ingest a Live Client Data API record."""
        record.setdefault("_ingest_ts", time.time())
        self._liveclient_buf.append(record)
        self._fire_evolution({"action": "ingest_liveclient"})

    # ------------------------------------------------------------------
    # Fusion
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_game_time(record: Dict[str, Any]) -> Optional[float]:
        """Extract game_time from either schema."""
        for key in ("game_time", "gameTime", "GameTime", "time"):
            val = record.get(key)
            if val is not None and isinstance(val, (int, float)):
                return float(val)
        return None

    def _merge_records(
        self,
        fiddler: Dict[str, Any],
        liveclient: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Merge two records with priority-based conflict resolution."""
        if self._priority == "liveclient":
            base = dict(fiddler)
            overlay = liveclient
        else:
            base = dict(liveclient)
            overlay = fiddler

        for k, v in overlay.items():
            if k.startswith("_"):
                continue
            base[k] = v

        # Normalise game_time
        gt = self._extract_game_time(base)
        if gt is not None:
            base["game_time"] = gt

        base["_fused"] = True
        base["_fuse_ts"] = time.time()
        return base

    def fuse(self) -> List[Dict[str, Any]]:
        """Fuse all un-fused matching records.

        Matches Fiddler and Live Client records whose game_time values
        are within ``time_tolerance`` seconds.  Each record is fused
        at most once.

        Returns:
            List of fused records.
        """
        results: List[Dict[str, Any]] = []

        for fi, frec in enumerate(self._fiddler_buf):
            if fi in self._fused_indices_f:
                continue
            ft = self._extract_game_time(frec)
            if ft is None:
                continue

            for li, lrec in enumerate(self._liveclient_buf):
                if li in self._fused_indices_l:
                    continue
                lt = self._extract_game_time(lrec)
                if lt is None:
                    continue

                if abs(ft - lt) <= self._time_tolerance:
                    merged = self._merge_records(frec, lrec)
                    results.append(merged)
                    self._fused_indices_f.add(fi)
                    self._fused_indices_l.add(li)
                    self._fuse_count += 1
                    break

        if results:
            self._fire_evolution({
                "action": "fuse",
                "fused_count": len(results),
                "total_fused": self._fuse_count,
            })

        return results

    # ------------------------------------------------------------------
    # Clear
    # ------------------------------------------------------------------

    def clear(self) -> None:
        """Clear both buffers and reset fusion state."""
        self._fiddler_buf.clear()
        self._liveclient_buf.clear()
        self._fused_indices_f.clear()
        self._fused_indices_l.clear()
        self._fire_evolution({"action": "clear"})

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def get_stats(self) -> Dict[str, Any]:
        return {
            "fuse_count": self._fuse_count,
            "fiddler_count": len(self._fiddler_buf),
            "liveclient_count": len(self._liveclient_buf),
            "fused_fiddler": len(self._fused_indices_f),
            "fused_liveclient": len(self._fused_indices_l),
            "time_tolerance": self._time_tolerance,
            "priority": self._priority,
        }

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
                logger.exception("evolution_callback raised in DualChannelFuser")

    def __repr__(self) -> str:
        return (
            f"DualChannelFuser(fiddler={self.fiddler_count}, "
            f"liveclient={self.liveclient_count}, fused={self._fuse_count})"
        )


default_fuser: DualChannelFuser = DualChannelFuser()
