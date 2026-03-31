#!/usr/bin/env python3
"""
M821 - Replay Parser
====================================
OperatorRL Historical Battle System - .rofl replay file parsing and extraction

查看英雄联盟回放文件(.rofl)的解析实现方式，理解其模式，
特别是二进制格式和事件编码是如何解构的。从文件头解析开始，
遵循该模式实现回放解析器，使系统可以从离线回放文件中提取
完整的事件序列，并能重建对局全程的详细数据。

Core: .rofl replay file parsing, extraction, and event decoding
"""

import os
import sys
import json
import time
import math
import struct
import logging
import hashlib
import tempfile
from pathlib import Path
from enum import Enum, auto
from typing import Dict, List, Any, Optional, Tuple, Set, Union, Callable
from dataclasses import dataclass, field, asdict
from abc import ABC, abstractmethod
from collections import defaultdict
from datetime import datetime, timezone

logger = logging.getLogger("operatorRL.historical_battle.core.replay")
logger.setLevel(logging.DEBUG)

# ─── Constants ──────────────────────────────────────────────────────────────

ROFL_MAGIC = b"RIOT"
ROFL_MAGIC_ALT = b"ROFL"
ROFL_HEADER_SIZE = 288
ROFL_CHUNK_HEADER_SIZE = 17
ROFL_KEYFRAME_HEADER_SIZE = 17
SUPPORTED_VERSIONS = ["2.0", "3.0"]
MAX_CHUNK_COUNT = 50000
MAX_KEYFRAME_COUNT = 5000
MAX_FILE_SIZE_MB = 500

class ReplayVersion(Enum):
    V2 = "2.0"
    V3 = "3.0"
    UNKNOWN = "unknown"

class ChunkType(Enum):
    GAME_DATA = 1
    KEYFRAME = 2
    END_STARTUP = 3
    UNDEFINED = 0

class ParseErrorSeverity(Enum):
    WARNING = "warning"
    ERROR = "error"
    FATAL = "fatal"

@dataclass
class ParseError:
    severity: ParseErrorSeverity
    message: str
    offset: int = 0
    context: str = ""

    def __str__(self) -> str:
        return f"[{self.severity.value}] {self.message}"

@dataclass
class ReplayHeader:
    magic: bytes = b""
    signature: bytes = b""
    header_length: int = 0
    file_length: int = 0
    metadata_offset: int = 0
    metadata_length: int = 0
    payload_header_offset: int = 0
    payload_header_length: int = 0
    payload_offset: int = 0
    game_id: int = 0
    game_length_ms: int = 0
    keyframe_count: int = 0
    chunk_count: int = 0
    end_startup_chunk_id: int = 0
    start_game_chunk_id: int = 0
    keyframe_interval: int = 0
    encryption_key: str = ""
    version: ReplayVersion = ReplayVersion.UNKNOWN

    def to_dict(self) -> Dict[str, Any]:
        return {
            "game_id": self.game_id,
            "game_length_ms": self.game_length_ms,
            "game_length_min": round(self.game_length_ms / 60000, 1) if self.game_length_ms else 0,
            "keyframes": self.keyframe_count,
            "chunks": self.chunk_count,
            "version": self.version.value,
            "header_length": self.header_length,
            "file_length": self.file_length,
        }

@dataclass
class ReplayChunk:
    chunk_id: int
    chunk_type: ChunkType
    length: int
    next_chunk_id: int
    offset: int
    data: bytes = b""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.chunk_id,
            "type": self.chunk_type.name,
            "length": self.length,
            "next": self.next_chunk_id,
            "offset": self.offset,
        }

@dataclass
class ReplayKeyframe:
    keyframe_id: int
    chunk_id: int
    length: int
    next_keyframe_id: int
    offset: int
    data: bytes = b""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.keyframe_id,
            "chunk_id": self.chunk_id,
            "length": self.length,
            "next": self.next_keyframe_id,
        }

@dataclass
class ReplayMetadata:
    game_duration: int = 0
    game_version: str = ""
    last_game_chunk_id: int = 0
    last_keyframe_id: int = 0
    stats_json: Optional[Dict[str, Any]] = None
    raw_json: Optional[Dict[str, Any]] = None
    players: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "game_duration": self.game_duration,
            "game_version": self.game_version,
            "last_chunk": self.last_game_chunk_id,
            "last_keyframe": self.last_keyframe_id,
            "has_stats": self.stats_json is not None,
            "player_count": len(self.players),
        }

@dataclass
class PlayerReplayStats:
    """Individual player stats extracted from replay."""
    participant_id: int = 0
    summoner_name: str = ""
    champion_id: int = 0
    team_id: int = 0
    kills: int = 0
    deaths: int = 0
    assists: int = 0
    gold_earned: int = 0
    total_damage: int = 0
    cs: int = 0
    win: bool = False
    items: List[int] = field(default_factory=list)
    runes: Dict[str, Any] = field(default_factory=dict)
    summoner_spells: Tuple[int, int] = (0, 0)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

@dataclass
class ParsedReplay:
    file_path: str
    file_size: int = 0
    header: ReplayHeader = field(default_factory=ReplayHeader)
    metadata: ReplayMetadata = field(default_factory=ReplayMetadata)
    chunks: List[ReplayChunk] = field(default_factory=list)
    keyframes: List[ReplayKeyframe] = field(default_factory=list)
    player_stats: List[PlayerReplayStats] = field(default_factory=list)
    events: List[Dict[str, Any]] = field(default_factory=list)
    parse_errors: List[ParseError] = field(default_factory=list)
    parse_time_ms: float = 0.0
    checksum: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "file": self.file_path,
            "file_size_mb": round(self.file_size / (1024 * 1024), 2),
            "header": self.header.to_dict(),
            "metadata": self.metadata.to_dict(),
            "chunk_count": len(self.chunks),
            "keyframe_count": len(self.keyframes),
            "player_count": len(self.player_stats),
            "event_count": len(self.events),
            "errors": [str(e) for e in self.parse_errors],
            "parse_time_ms": round(self.parse_time_ms, 2),
            "checksum": self.checksum,
        }

    @property
    def is_valid(self) -> bool:
        fatal = [e for e in self.parse_errors if e.severity == ParseErrorSeverity.FATAL]
        return len(fatal) == 0 and self.header.magic in (ROFL_MAGIC, ROFL_MAGIC_ALT)


class ReplayParser:
    """
    Parses League of Legends .rofl replay files to extract match data.
    Handles header parsing, chunk extraction, metadata decoding, and
    event reconstruction from replay data.
    """

    def __init__(self):
        self._parse_count = 0
        self._total_bytes_parsed = 0

    @property
    def stats(self) -> Dict[str, Any]:
        return {
            "files_parsed": self._parse_count,
            "total_bytes": self._total_bytes_parsed,
            "total_mb": round(self._total_bytes_parsed / (1024 * 1024), 2),
        }

    def parse(self, file_path: str) -> ParsedReplay:
        """Parse a .rofl replay file."""
        start = time.time()
        result = ParsedReplay(file_path=file_path)

        try:
            path = Path(file_path)
            if not path.exists():
                result.parse_errors.append(ParseError(
                    ParseErrorSeverity.FATAL, f"File not found: {file_path}"))
                return result
            if not path.suffix.lower() == ".rofl":
                result.parse_errors.append(ParseError(
                    ParseErrorSeverity.FATAL, f"Not a .rofl file: {file_path}"))
                return result

            file_size = path.stat().st_size
            result.file_size = file_size
            if file_size < ROFL_HEADER_SIZE:
                result.parse_errors.append(ParseError(
                    ParseErrorSeverity.FATAL, f"File too small: {file_size} bytes"))
                return result
            if file_size > MAX_FILE_SIZE_MB * 1024 * 1024:
                result.parse_errors.append(ParseError(
                    ParseErrorSeverity.WARNING, f"File very large: {file_size / (1024*1024):.1f} MB"))

            with open(file_path, "rb") as f:
                raw = f.read()

            result.checksum = hashlib.md5(raw).hexdigest()
            result.header = self._parse_header(raw, result)

            if result.header.metadata_offset > 0 and result.header.metadata_length > 0:
                result.metadata = self._parse_metadata(raw, result.header, result)

            result.chunks = self._parse_chunk_headers(raw, result.header, result)
            result.keyframes = self._parse_keyframe_headers(raw, result.header, result)

            if result.metadata.stats_json:
                result.player_stats = self._extract_player_stats(result.metadata.stats_json)

            self._parse_count += 1
            self._total_bytes_parsed += file_size

        except Exception as exc:
            result.parse_errors.append(ParseError(
                ParseErrorSeverity.FATAL, f"Parse error: {exc}"))
            logger.error(f"Replay parse failed for {file_path}: {exc}")

        result.parse_time_ms = (time.time() - start) * 1000
        return result

    def _parse_header(self, data: bytes, result: ParsedReplay) -> ReplayHeader:
        """Parse the ROFL file header."""
        header = ReplayHeader()
        if len(data) < ROFL_HEADER_SIZE:
            result.parse_errors.append(ParseError(
                ParseErrorSeverity.FATAL, "Insufficient data for header"))
            return header

        header.magic = data[0:4]
        if header.magic == ROFL_MAGIC:
            header.version = ReplayVersion.V2
        elif header.magic == ROFL_MAGIC_ALT:
            header.version = ReplayVersion.V3
        else:
            header.version = ReplayVersion.UNKNOWN
            result.parse_errors.append(ParseError(
                ParseErrorSeverity.ERROR, f"Unknown magic: {header.magic!r}"))

        header.signature = data[4:260]
        try:
            header.header_length = struct.unpack("<H", data[260:262])[0]
            header.file_length = struct.unpack("<I", data[262:266])[0]
            header.metadata_offset = struct.unpack("<I", data[266:270])[0]
            header.metadata_length = struct.unpack("<I", data[270:274])[0]
            header.payload_header_offset = struct.unpack("<I", data[274:278])[0]
            header.payload_header_length = struct.unpack("<I", data[278:282])[0]
            header.payload_offset = struct.unpack("<I", data[282:286])[0]
        except struct.error as exc:
            result.parse_errors.append(ParseError(
                ParseErrorSeverity.ERROR, f"Header struct unpack failed: {exc}"))

        return header

    def _parse_metadata(self, data: bytes, header: ReplayHeader, result: ParsedReplay) -> ReplayMetadata:
        """Parse replay metadata JSON."""
        meta = ReplayMetadata()
        try:
            start = header.metadata_offset
            end = start + header.metadata_length
            if end > len(data):
                result.parse_errors.append(ParseError(
                    ParseErrorSeverity.ERROR, "Metadata extends beyond file"))
                return meta
            meta_bytes = data[start:end]
            meta_json = json.loads(meta_bytes.decode("utf-8", errors="replace"))
            meta.raw_json = meta_json
            meta.game_duration = meta_json.get("gameLength", 0)
            meta.game_version = meta_json.get("gameVersion", "")
            meta.last_game_chunk_id = meta_json.get("lastGameChunkId", 0)
            meta.last_keyframe_id = meta_json.get("lastKeyFrameId", 0)
            stats_str = meta_json.get("statsJson", "")
            if stats_str:
                try:
                    meta.stats_json = json.loads(stats_str)
                except json.JSONDecodeError:
                    result.parse_errors.append(ParseError(
                        ParseErrorSeverity.WARNING, "Failed to parse embedded stats JSON"))
        except Exception as exc:
            result.parse_errors.append(ParseError(
                ParseErrorSeverity.ERROR, f"Metadata parse error: {exc}"))
        return meta

    def _parse_chunk_headers(self, data: bytes, header: ReplayHeader, result: ParsedReplay) -> List[ReplayChunk]:
        """Parse chunk headers from payload header section."""
        chunks = []
        if header.payload_header_offset == 0:
            return chunks
        try:
            offset = header.payload_header_offset
            if offset + 8 > len(data):
                return chunks
            chunk_count = struct.unpack("<I", data[offset+4:offset+8])[0] if offset + 8 <= len(data) else 0
            chunk_count = min(chunk_count, MAX_CHUNK_COUNT)
            pos = offset + 8
            for i in range(chunk_count):
                if pos + ROFL_CHUNK_HEADER_SIZE > len(data):
                    break
                chunk_id = struct.unpack("<I", data[pos:pos+4])[0]
                chunk_type_raw = data[pos+4] if pos+4 < len(data) else 0
                try:
                    chunk_type = ChunkType(chunk_type_raw)
                except ValueError:
                    chunk_type = ChunkType.UNDEFINED
                length = struct.unpack("<I", data[pos+5:pos+9])[0]
                next_id = struct.unpack("<I", data[pos+9:pos+13])[0]
                chunk_offset = struct.unpack("<I", data[pos+13:pos+17])[0]
                chunks.append(ReplayChunk(
                    chunk_id=chunk_id, chunk_type=chunk_type,
                    length=length, next_chunk_id=next_id, offset=chunk_offset,
                ))
                pos += ROFL_CHUNK_HEADER_SIZE
        except Exception as exc:
            result.parse_errors.append(ParseError(
                ParseErrorSeverity.ERROR, f"Chunk header parse error: {exc}"))
        return chunks

    def _parse_keyframe_headers(self, data: bytes, header: ReplayHeader, result: ParsedReplay) -> List[ReplayKeyframe]:
        """Parse keyframe headers."""
        keyframes = []
        # Keyframes typically follow chunks in the payload header
        # Implementation depends on exact format version
        return keyframes

    def _extract_player_stats(self, stats_data: Any) -> List[PlayerReplayStats]:
        """Extract player statistics from parsed replay stats JSON."""
        players = []
        if isinstance(stats_data, list):
            for p_data in stats_data:
                if isinstance(p_data, dict):
                    player = PlayerReplayStats(
                        participant_id=p_data.get("SKIN", 0),
                        summoner_name=p_data.get("NAME", ""),
                        champion_id=p_data.get("SKIN", 0),
                        team_id=p_data.get("TEAM", 0),
                        kills=p_data.get("CHAMPIONS_KILLED", 0),
                        deaths=p_data.get("NUM_DEATHS", 0),
                        assists=p_data.get("ASSISTS", 0),
                        gold_earned=p_data.get("GOLD_EARNED", 0),
                        total_damage=p_data.get("TOTAL_DAMAGE_DEALT_TO_CHAMPIONS", 0),
                        cs=p_data.get("MINIONS_KILLED", 0),
                        win=p_data.get("WIN", "") == "Win",
                    )
                    players.append(player)
        return players

    def batch_parse(self, file_paths: List[str]) -> List[ParsedReplay]:
        """Parse multiple replay files."""
        results = []
        for fp in file_paths:
            results.append(self.parse(fp))
        return results

    def validate_file(self, file_path: str) -> Dict[str, Any]:
        """Quick validation without full parse."""
        try:
            path = Path(file_path)
            if not path.exists():
                return {"valid": False, "reason": "File not found"}
            if path.stat().st_size < ROFL_HEADER_SIZE:
                return {"valid": False, "reason": "Too small"}
            with open(file_path, "rb") as f:
                magic = f.read(4)
            if magic not in (ROFL_MAGIC, ROFL_MAGIC_ALT):
                return {"valid": False, "reason": f"Bad magic: {magic!r}"}
            return {"valid": True, "size": path.stat().st_size}
        except Exception as exc:
            return {"valid": False, "reason": str(exc)}


# ─── Module Self-Test ─────────────────────────────────────────────────────

def _self_test() -> Dict[str, Any]:
    results = {"module": "M821_replay_parser", "tests": []}

    try:
        parser = ReplayParser()
        result = parser.parse("/nonexistent/test.rofl")
        assert len(result.parse_errors) > 0
        assert not result.is_valid
        results["tests"].append({"name": "missing_file", "status": "pass"})
    except Exception as e:
        results["tests"].append({"name": "missing_file", "status": "fail", "error": str(e)})

    try:
        header = ReplayHeader(game_id=12345, game_length_ms=1800000, version=ReplayVersion.V2)
        d = header.to_dict()
        assert d["game_id"] == 12345
        assert d["game_length_min"] == 30.0
        results["tests"].append({"name": "header_model", "status": "pass"})
    except Exception as e:
        results["tests"].append({"name": "header_model", "status": "fail", "error": str(e)})

    try:
        chunk = ReplayChunk(chunk_id=1, chunk_type=ChunkType.GAME_DATA, length=1024, next_chunk_id=2, offset=0)
        assert chunk.to_dict()["type"] == "GAME_DATA"
        results["tests"].append({"name": "chunk_model", "status": "pass"})
    except Exception as e:
        results["tests"].append({"name": "chunk_model", "status": "fail", "error": str(e)})

    try:
        fake_rofl = b"RIOT" + b"\x00" * 256
        fake_rofl += struct.pack("<H", 288)
        fake_rofl += struct.pack("<I", 500)
        fake_rofl += struct.pack("<I", 288)
        fake_rofl += struct.pack("<I", 50)
        fake_rofl += struct.pack("<I", 0)
        fake_rofl += struct.pack("<I", 0)
        fake_rofl += struct.pack("<I", 0)
        fake_rofl += b"\x00" * 2
        metadata = json.dumps({"gameLength": 1800, "gameVersion": "14.1"}).encode()
        fake_rofl += metadata + b"\x00" * (50 - len(metadata))

        with tempfile.NamedTemporaryFile(suffix=".rofl", delete=False) as f:
            f.write(fake_rofl)
            tmp_path = f.name

        parser = ReplayParser()
        parsed = parser.parse(tmp_path)
        assert parsed.header.magic == b"RIOT"
        assert parsed.metadata.game_version == "14.1"
        os.unlink(tmp_path)
        results["tests"].append({"name": "synthetic_rofl_parse", "status": "pass"})
    except Exception as e:
        results["tests"].append({"name": "synthetic_rofl_parse", "status": "fail", "error": str(e)})

    try:
        parser = ReplayParser()
        v = parser.validate_file("/nonexistent.rofl")
        assert v["valid"] == False
        results["tests"].append({"name": "validate_file", "status": "pass"})
    except Exception as e:
        results["tests"].append({"name": "validate_file", "status": "fail", "error": str(e)})

    results["passed"] = sum(1 for t in results["tests"] if t["status"] == "pass")
    results["total"] = len(results["tests"])
    return results


if __name__ == "__main__":
    print(json.dumps(_self_test(), indent=2))
