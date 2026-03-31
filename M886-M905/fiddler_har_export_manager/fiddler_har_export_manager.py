#!/usr/bin/env python3
"""
M892 — FiddlerHarExportManager
================================
Manages Fiddler HAR file exports: parse, archive, index, and enable offline
replay of network request sequences for M880 ReplayAnalysisEngine.

Dependencies: M886
Reference: M866-M885 har_traffic_analyzer pattern
"""
from __future__ import annotations
import asyncio, collections, gzip, hashlib, json, logging, os, shutil, time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from enum import Enum, auto

logger = logging.getLogger("M892.FiddlerHarExportManager")

HAR_ARCHIVE_DIR = "har_archive"
HAR_INDEX_FILE = "har_index.json"
MAX_ARCHIVE_SIZE_MB = 500
COMPRESSION_ENABLED = True


class HarEntryType(Enum):
    MATCH_HISTORY = "match_history"
    SUMMONER_PROFILE = "summoner_profile"
    RANKED_STATS = "ranked_stats"
    CHAMP_SELECT = "champ_select"
    LIVE_CLIENT = "live_client"
    LCU_WEBSOCKET = "lcu_websocket"
    OTHER = "other"


@dataclass
class HarEntry:
    """Single entry from a HAR file."""
    entry_id: str
    timestamp: datetime
    method: str
    url: str
    status_code: int
    request_size: int
    response_size: int
    duration_ms: float
    mime_type: str
    entry_type: HarEntryType
    request_headers: Dict[str, str] = field(default_factory=dict)
    response_body_hash: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.entry_id, "ts": self.timestamp.isoformat(),
            "method": self.method, "url": self.url, "status": self.status_code,
            "req_size": self.request_size, "resp_size": self.response_size,
            "duration_ms": round(self.duration_ms, 2), "mime": self.mime_type,
            "type": self.entry_type.value, "body_hash": self.response_body_hash,
        }


@dataclass
class HarFileRecord:
    """Metadata for an archived HAR file."""
    file_id: str
    original_name: str
    archive_path: str
    file_size: int
    entry_count: int
    first_timestamp: Optional[datetime] = None
    last_timestamp: Optional[datetime] = None
    entry_type_counts: Dict[str, int] = field(default_factory=dict)
    game_ids: List[str] = field(default_factory=list)
    compressed: bool = False
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.file_id, "name": self.original_name,
            "path": self.archive_path, "size": self.file_size,
            "entries": self.entry_count, "types": self.entry_type_counts,
            "first": self.first_timestamp.isoformat() if self.first_timestamp else None,
            "last": self.last_timestamp.isoformat() if self.last_timestamp else None,
            "game_ids": self.game_ids, "compressed": self.compressed,
        }


class HarClassifier:
    """Classifies HAR entries by URL pattern into HarEntryType."""

    PATTERNS = [
        ("lol-match-history", HarEntryType.MATCH_HISTORY),
        ("lol-summoner", HarEntryType.SUMMONER_PROFILE),
        ("lol-ranked", HarEntryType.RANKED_STATS),
        ("lol-champ-select", HarEntryType.CHAMP_SELECT),
        ("liveclientdata", HarEntryType.LIVE_CLIENT),
        ("lol-gameflow", HarEntryType.LCU_WEBSOCKET),
    ]

    @classmethod
    def classify(cls, url: str) -> HarEntryType:
        url_lower = url.lower()
        for pattern, entry_type in cls.PATTERNS:
            if pattern in url_lower:
                return entry_type
        return HarEntryType.OTHER


class HarParser:
    """Parses HAR 1.2 format files into structured HarEntry objects."""

    @staticmethod
    def parse_file(file_path: str) -> Tuple[List[HarEntry], Dict[str, Any]]:
        """Parse a HAR file, return entries and metadata."""
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        har_log = data.get("log", {})
        raw_entries = har_log.get("entries", [])
        creator = har_log.get("creator", {})
        metadata = {
            "version": har_log.get("version", "1.2"),
            "creator": creator.get("name", "unknown"),
            "creator_version": creator.get("version", ""),
            "pages": len(har_log.get("pages", [])),
        }

        entries = []
        for i, raw in enumerate(raw_entries):
            request = raw.get("request", {})
            response = raw.get("response", {})
            content = response.get("content", {})

            try:
                ts = datetime.fromisoformat(
                    raw.get("startedDateTime", "").replace("Z", "+00:00")
                )
            except (ValueError, AttributeError):
                ts = datetime.now(timezone.utc)

            url = request.get("url", "")
            resp_body = content.get("text", "")
            body_hash = hashlib.md5(resp_body.encode()[:4096]).hexdigest()[:12] if resp_body else ""

            req_headers = {h["name"]: h["value"] for h in request.get("headers", [])
                          if isinstance(h, dict)}

            entries.append(HarEntry(
                entry_id=f"har-{i:06d}",
                timestamp=ts,
                method=request.get("method", "GET"),
                url=url,
                status_code=response.get("status", 0),
                request_size=request.get("headersSize", 0) + request.get("bodySize", 0),
                response_size=content.get("size", 0),
                duration_ms=raw.get("time", 0),
                mime_type=content.get("mimeType", ""),
                entry_type=HarClassifier.classify(url),
                request_headers=req_headers,
                response_body_hash=body_hash,
            ))

        return entries, metadata

    @staticmethod
    def parse_compressed(file_path: str) -> Tuple[List[HarEntry], Dict[str, Any]]:
        """Parse a gzipped HAR file."""
        with gzip.open(file_path, "rt", encoding="utf-8") as f:
            data = json.load(f)
        # Re-use the same logic by writing to temp then parsing
        # (simplified: parse directly from the loaded dict)
        har_log = data.get("log", {})
        raw_entries = har_log.get("entries", [])
        entries = []
        for i, raw in enumerate(raw_entries):
            req = raw.get("request", {})
            resp = raw.get("response", {})
            url = req.get("url", "")
            try:
                ts = datetime.fromisoformat(raw.get("startedDateTime", "").replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                ts = datetime.now(timezone.utc)
            entries.append(HarEntry(
                entry_id=f"har-{i:06d}", timestamp=ts, method=req.get("method", "GET"),
                url=url, status_code=resp.get("status", 0),
                request_size=0, response_size=resp.get("content", {}).get("size", 0),
                duration_ms=raw.get("time", 0), mime_type=resp.get("content", {}).get("mimeType", ""),
                entry_type=HarClassifier.classify(url), request_headers={},
            ))
        return entries, {"version": "1.2", "compressed": True}


class FiddlerHarExportManager:
    """
    Manages the HAR file lifecycle: import → parse → classify → archive → index.

    Provides:
    - Automatic HAR file import from Fiddler export directory
    - Classification of entries by LoL API endpoint type
    - Compressed archival with configurable size limits
    - Full-text index for fast replay lookup by game_id, timestamp, endpoint
    - Integration point for M880 ReplayAnalysisEngine offline analysis
    """

    def __init__(self, archive_dir: str = HAR_ARCHIVE_DIR):
        self._archive_dir = Path(archive_dir)
        self._archive_dir.mkdir(parents=True, exist_ok=True)
        self._parser = HarParser()
        self._index: Dict[str, HarFileRecord] = {}
        self._entries_by_type: Dict[HarEntryType, List[str]] = collections.defaultdict(list)
        self._watch_task: Optional[asyncio.Task] = None
        self._shutdown = asyncio.Event()
        self._stats = {
            "files_imported": 0, "entries_parsed": 0, "total_archive_size": 0,
            "types_distribution": {},
        }
        self._load_index()
        logger.info("FiddlerHarExportManager initialized (archive=%s)", archive_dir)

    def _load_index(self):
        """Load existing index from disk."""
        index_path = self._archive_dir / HAR_INDEX_FILE
        if index_path.exists():
            try:
                with open(index_path) as f:
                    data = json.load(f)
                for fid, rec in data.get("files", {}).items():
                    self._index[fid] = HarFileRecord(
                        file_id=fid, original_name=rec.get("name", ""),
                        archive_path=rec.get("path", ""), file_size=rec.get("size", 0),
                        entry_count=rec.get("entries", 0),
                        entry_type_counts=rec.get("types", {}),
                        game_ids=rec.get("game_ids", []),
                        compressed=rec.get("compressed", False),
                    )
                logger.info("Loaded index with %d HAR files", len(self._index))
            except (json.JSONDecodeError, KeyError) as exc:
                logger.warning("Index load error: %s", exc)

    def _save_index(self):
        """Persist index to disk."""
        index_path = self._archive_dir / HAR_INDEX_FILE
        data = {"files": {fid: rec.to_dict() for fid, rec in self._index.items()},
                "updated": datetime.now(timezone.utc).isoformat()}
        with open(index_path, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    async def import_har(self, file_path: str) -> Optional[HarFileRecord]:
        """Import and archive a HAR file."""
        path = Path(file_path)
        if not path.exists():
            logger.error("HAR file not found: %s", file_path)
            return None

        try:
            if file_path.endswith(".gz"):
                entries, meta = self._parser.parse_compressed(file_path)
            else:
                entries, meta = self._parser.parse_file(file_path)

            if not entries:
                logger.warning("No entries in HAR file: %s", file_path)
                return None

            file_id = hashlib.sha256(f"{path.name}-{path.stat().st_size}".encode()).hexdigest()[:16]

            type_counts: Dict[str, int] = collections.Counter()
            game_ids = set()
            for entry in entries:
                type_counts[entry.entry_type.value] += 1
                self._entries_by_type[entry.entry_type].append(file_id)
                if "gameId" in entry.url or "games/" in entry.url:
                    parts = entry.url.split("/")
                    for p in parts:
                        if p.isdigit() and len(p) > 8:
                            game_ids.add(p)

            archive_name = f"{file_id}_{path.stem}.har"
            if COMPRESSION_ENABLED:
                archive_name += ".gz"
                archive_path = self._archive_dir / archive_name
                with open(file_path, "rb") as f_in:
                    with gzip.open(archive_path, "wb") as f_out:
                        shutil.copyfileobj(f_in, f_out)
            else:
                archive_path = self._archive_dir / archive_name
                shutil.copy2(file_path, archive_path)

            record = HarFileRecord(
                file_id=file_id, original_name=path.name,
                archive_path=str(archive_path), file_size=archive_path.stat().st_size,
                entry_count=len(entries),
                first_timestamp=entries[0].timestamp if entries else None,
                last_timestamp=entries[-1].timestamp if entries else None,
                entry_type_counts=dict(type_counts), game_ids=list(game_ids),
                compressed=COMPRESSION_ENABLED,
            )

            self._index[file_id] = record
            self._stats["files_imported"] += 1
            self._stats["entries_parsed"] += len(entries)
            self._stats["total_archive_size"] += record.file_size
            self._save_index()

            logger.info("Imported %s: %d entries, %d bytes archived",
                        path.name, len(entries), record.file_size)
            return record

        except Exception as exc:
            logger.error("HAR import failed for %s: %s", file_path, exc)
            return None

    def get_entries_by_type(self, entry_type: HarEntryType) -> List[str]:
        """Get file IDs containing entries of a specific type."""
        return self._entries_by_type.get(entry_type, [])

    def get_entries_for_game(self, game_id: str) -> List[HarFileRecord]:
        """Find HAR files containing data for a specific game."""
        return [rec for rec in self._index.values() if game_id in rec.game_ids]

    def list_archived(self) -> List[Dict[str, Any]]:
        return [rec.to_dict() for rec in sorted(self._index.values(), key=lambda r: r.created_at, reverse=True)]

    async def cleanup_old_archives(self, max_age_days: int = 30):
        """Remove archives older than max_age_days."""
        cutoff = datetime.now(timezone.utc).timestamp() - (max_age_days * 86400)
        removed = 0
        for fid, rec in list(self._index.items()):
            if rec.created_at.timestamp() < cutoff:
                try:
                    Path(rec.archive_path).unlink(missing_ok=True)
                    del self._index[fid]
                    removed += 1
                except Exception as exc:
                    logger.error("Cleanup error for %s: %s", fid, exc)
        if removed:
            self._save_index()
            logger.info("Cleaned up %d old archives", removed)

    def export_stats(self) -> Dict[str, Any]:
        return {"manager_stats": self._stats, "archived_files": len(self._index),
                "total_size_mb": round(self._stats["total_archive_size"] / 1048576, 2)}



# ---------------------------------------------------------------------------
# Extended FiddlerHarExportManager utilities
# ---------------------------------------------------------------------------

class HarReplayEngine:
    """Replays HAR file entries in sequence for offline analysis."""

    def __init__(self, manager: FiddlerHarExportManager):
        self._manager = manager
        self._replay_position = 0
        self._current_entries: List[HarEntry] = []
        self._replay_speed = 1.0

    async def load_replay(self, file_id: str) -> bool:
        """Load a HAR file for replay."""
        record = self._manager._index.get(file_id)
        if not record:
            return False
        try:
            path = record.archive_path
            if record.compressed:
                self._current_entries, _ = HarParser.parse_compressed(path)
            else:
                self._current_entries, _ = HarParser.parse_file(path)
            self._replay_position = 0
            return True
        except Exception as exc:
            logger.error("Load replay error: %s", exc)
            return False

    def next_entry(self) -> Optional[HarEntry]:
        if self._replay_position >= len(self._current_entries):
            return None
        entry = self._current_entries[self._replay_position]
        self._replay_position += 1
        return entry

    def seek(self, position: int):
        self._replay_position = max(0, min(position, len(self._current_entries)))

    def get_entries_by_type(self, entry_type: HarEntryType) -> List[HarEntry]:
        return [e for e in self._current_entries if e.entry_type == entry_type]

    @property
    def total_entries(self) -> int:
        return len(self._current_entries)

    @property
    def progress(self) -> float:
        if not self._current_entries:
            return 0.0
        return self._replay_position / len(self._current_entries) * 100


class HarStatisticsCalculator:
    """Computes detailed statistics from HAR archive."""

    @staticmethod
    def compute_stats(entries: List[HarEntry]) -> Dict[str, Any]:
        if not entries:
            return {"count": 0}

        total_req_size = sum(e.request_size for e in entries)
        total_resp_size = sum(e.response_size for e in entries)
        avg_duration = sum(e.duration_ms for e in entries) / len(entries)

        type_dist = collections.Counter(e.entry_type.value for e in entries)
        status_dist = collections.Counter(e.status_code for e in entries)
        method_dist = collections.Counter(e.method for e in entries)

        durations = sorted(e.duration_ms for e in entries)
        p50 = durations[len(durations) // 2] if durations else 0
        p95 = durations[int(len(durations) * 0.95)] if durations else 0
        p99 = durations[int(len(durations) * 0.99)] if durations else 0

        time_range = None
        if entries[0].timestamp and entries[-1].timestamp:
            time_range = (entries[-1].timestamp - entries[0].timestamp).total_seconds()

        return {
            "count": len(entries),
            "total_request_bytes": total_req_size,
            "total_response_bytes": total_resp_size,
            "avg_duration_ms": round(avg_duration, 2),
            "p50_ms": round(p50, 2), "p95_ms": round(p95, 2), "p99_ms": round(p99, 2),
            "type_distribution": dict(type_dist),
            "status_distribution": dict(status_dist),
            "method_distribution": dict(method_dist),
            "time_range_seconds": round(time_range, 1) if time_range else None,
            "requests_per_second": round(len(entries) / time_range, 2) if time_range and time_range > 0 else None,
        }


class HarDiffTool:
    """Compare two HAR recordings to detect API behavior changes."""

    @staticmethod
    def diff(entries_a: List[HarEntry], entries_b: List[HarEntry]) -> Dict[str, Any]:
        urls_a = set(e.url for e in entries_a)
        urls_b = set(e.url for e in entries_b)

        only_in_a = urls_a - urls_b
        only_in_b = urls_b - urls_a
        common = urls_a & urls_b

        timing_changes = []
        for url in list(common)[:20]:
            dur_a = [e.duration_ms for e in entries_a if e.url == url]
            dur_b = [e.duration_ms for e in entries_b if e.url == url]
            avg_a = sum(dur_a) / len(dur_a) if dur_a else 0
            avg_b = sum(dur_b) / len(dur_b) if dur_b else 0
            if abs(avg_a - avg_b) > 50:
                timing_changes.append({
                    "url": url[:100], "avg_a_ms": round(avg_a, 1),
                    "avg_b_ms": round(avg_b, 1), "change_ms": round(avg_b - avg_a, 1),
                })

        return {
            "urls_only_in_a": len(only_in_a), "urls_only_in_b": len(only_in_b),
            "common_urls": len(common), "timing_changes": timing_changes,
        }



class HarAutoImporter:
    """Watches a directory for new HAR files and auto-imports them."""

    def __init__(self, manager: FiddlerHarExportManager, watch_dir: str):
        self._manager = manager
        self._watch_dir = Path(watch_dir)
        self._imported: Set[str] = set()
        self._poll_task: Optional[asyncio.Task] = None
        self._shutdown = asyncio.Event()

    async def start(self, poll_interval: float = 5.0):
        self._shutdown.clear()
        self._poll_task = asyncio.create_task(self._watch_loop(poll_interval))

    async def stop(self):
        self._shutdown.set()
        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
            try: await self._poll_task
            except asyncio.CancelledError: pass

    async def _watch_loop(self, interval: float):
        while not self._shutdown.is_set():
            try:
                if self._watch_dir.exists():
                    for f in self._watch_dir.glob("*.har"):
                        if str(f) not in self._imported:
                            await self._manager.import_har(str(f))
                            self._imported.add(str(f))
                    for f in self._watch_dir.glob("*.har.gz"):
                        if str(f) not in self._imported:
                            await self._manager.import_har(str(f))
                            self._imported.add(str(f))
            except asyncio.CancelledError: raise
            except Exception as exc:
                logger.error("Watch error: %s", exc)
            await asyncio.sleep(interval)

    @property
    def imported_count(self) -> int:
        return len(self._imported)
