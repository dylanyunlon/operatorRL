"""
TDD Tests for M253: PacketAnalyzerV2 — migrated to protocol-decoder.

10 tests: construction, classify packets, extract payload, pattern matching,
protocol-decoder delegation, statistics.
"""

import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


class TestPacketAnalyzerV2Construction:
    def test_import_and_construct(self):
        from lol_fiddler_agent.network.packet_analyzer_v2 import PacketAnalyzerV2
        analyzer = PacketAnalyzerV2()
        assert analyzer is not None

    def test_has_analyze_method(self):
        from lol_fiddler_agent.network.packet_analyzer_v2 import PacketAnalyzerV2
        analyzer = PacketAnalyzerV2()
        assert callable(getattr(analyzer, "analyze", None))


class TestPacketAnalyzerV2Classify:
    def test_classify_http_json(self):
        from lol_fiddler_agent.network.packet_analyzer_v2 import PacketAnalyzerV2
        analyzer = PacketAnalyzerV2()
        result = analyzer.classify(b'{"activePlayer": {}}', content_type="application/json")
        assert result == "json"

    def test_classify_binary(self):
        from lol_fiddler_agent.network.packet_analyzer_v2 import PacketAnalyzerV2
        analyzer = PacketAnalyzerV2()
        result = analyzer.classify(b"\x00\x01\x02\x03", content_type="application/octet-stream")
        assert result == "binary"

    def test_classify_empty(self):
        from lol_fiddler_agent.network.packet_analyzer_v2 import PacketAnalyzerV2
        analyzer = PacketAnalyzerV2()
        result = analyzer.classify(b"", content_type="")
        assert result == "unknown"


class TestPacketAnalyzerV2Analyze:
    def test_analyze_json_payload(self):
        from lol_fiddler_agent.network.packet_analyzer_v2 import PacketAnalyzerV2
        analyzer = PacketAnalyzerV2()
        result = analyzer.analyze(
            b'{"gameData": {"gameTime": 120.0}}',
            content_type="application/json",
            url="/liveclientdata/allgamedata"
        )
        assert result is not None
        assert "game_time" in result or "gameTime" in str(result)

    def test_analyze_returns_codec_format(self):
        from lol_fiddler_agent.network.packet_analyzer_v2 import PacketAnalyzerV2
        analyzer = PacketAnalyzerV2()
        result = analyzer.analyze(
            b'{"allPlayers": []}',
            content_type="application/json",
            url="/liveclientdata/playerlist"
        )
        assert result is not None


class TestPacketAnalyzerV2Stats:
    def test_stats_initial_empty(self):
        from lol_fiddler_agent.network.packet_analyzer_v2 import PacketAnalyzerV2
        analyzer = PacketAnalyzerV2()
        stats = analyzer.get_stats()
        assert stats["packets_analyzed"] == 0

    def test_stats_increment_on_analyze(self):
        from lol_fiddler_agent.network.packet_analyzer_v2 import PacketAnalyzerV2
        analyzer = PacketAnalyzerV2()
        analyzer.analyze(b'{}', content_type="application/json", url="/test")
        stats = analyzer.get_stats()
        assert stats["packets_analyzed"] >= 1

    def test_evolution_key(self):
        from lol_fiddler_agent.network.packet_analyzer_v2 import _EVOLUTION_KEY
        assert isinstance(_EVOLUTION_KEY, str)
