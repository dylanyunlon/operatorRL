"""
TDD Test Suite for M666-M685: Cross-Game Protocol Adaptation Layer.

Each module has 10 tests covering:
  - Basic instantiation and properties
  - Core functionality (happy path)
  - Edge cases (empty input, invalid input)
  - Integration between modules
  - Evolution callback firing
  - Statistics and monitoring

Design: Tests are structured so ~50% will fail on first run,
driving implementation refinement per TDD methodology.
"""

import json
import sys
import os
import time

# Add project paths — need to go up 2 dirs from tests/test_m666_m685/
_BASE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_paths = [
    os.path.join(_BASE, "extensions", "protocol_decoder", "src"),
    os.path.join(_BASE, "integrations", "lol-history", "src", "lol_history"),
    os.path.join(_BASE, "integrations", "lol", "src", "lol_agent"),
    os.path.join(_BASE, "agentos", "governance"),
    os.path.join(_BASE, "extensions", "fiddler_bridge", "src"),
]
for p in _paths:
    if p not in sys.path:
        sys.path.insert(0, p)


# =========================================================================
# M666: GameProtocolAdapterBase
# =========================================================================
class TestGameProtocolAdapterBase:
    """10 tests for GameProtocolAdapterBase."""

    def _make_concrete(self):
        from game_protocol_adapter_base import GameProtocolAdapterBase

        class ConcreteAdapter(GameProtocolAdapterBase):
            @property
            def game_type(self):
                return "test_game"
            def _connect_impl(self, config):
                return config.get("should_connect", True)
            def _disconnect_impl(self):
                pass
            def _decode_impl(self, raw_data):
                if not isinstance(raw_data, dict):
                    raise ValueError("expected dict")
                return {"parsed": True, **raw_data}
            def _normalize_impl(self, decoded):
                return {"game_type": "test_game", "game_time": decoded.get("game_time", 0), "players": [], "events": []}

        return ConcreteAdapter()

    def test_01_instantiation(self):
        a = self._make_concrete()
        assert a.game_type == "test_game"
        assert a.state == "disconnected"
        assert not a.is_connected

    def test_02_connect_disconnect_lifecycle(self):
        a = self._make_concrete()
        assert a.connect({}) is True
        assert a.is_connected
        assert a.state == "connected"
        a.disconnect()
        assert a.state == "disconnected"

    def test_03_connect_idempotent(self):
        a = self._make_concrete()
        a.connect({})
        assert a.connect({}) is True  # already connected

    def test_04_disconnect_idempotent(self):
        a = self._make_concrete()
        a.disconnect()  # already disconnected — no error
        assert a.state == "disconnected"

    def test_05_connect_failure(self):
        a = self._make_concrete()
        ok = a.connect({"should_connect": False})
        assert ok is False
        assert a.state == "disconnected"

    def test_06_decode_success(self):
        a = self._make_concrete()
        result = a.decode({"game_time": 100.0})
        assert result["_decoded"] is True
        assert result["_game_type"] == "test_game"
        assert result["parsed"] is True

    def test_07_decode_error_handling(self):
        a = self._make_concrete()
        result = a.decode("invalid")
        assert result["_decoded"] is False
        assert "_error" in result

    def test_08_normalize(self):
        a = self._make_concrete()
        decoded = a.decode({"game_time": 50.0})
        norm = a.normalize(decoded)
        assert norm["_normalized"] is True
        assert norm["game_type"] == "test_game"

    def test_09_decode_and_normalize_chain(self):
        a = self._make_concrete()
        result = a.decode_and_normalize({"game_time": 200.0})
        assert result.get("_normalized") is True
        assert result["game_type"] == "test_game"

    def test_10_health_and_stats(self):
        a = self._make_concrete()
        a.connect({})
        a.decode({"x": 1})
        h = a.get_health()
        assert h["game_type"] == "test_game"
        assert h["decode_count"] == 1
        assert h["is_connected"] is True
        s = a.get_stats()
        assert "state_history_len" in s


# =========================================================================
# M667: LolProtocolAdapter
# =========================================================================
class TestLolProtocolAdapter:

    def _make(self):
        from lol_protocol_adapter import LolProtocolAdapter
        return LolProtocolAdapter()

    def test_01_game_type(self):
        a = self._make()
        assert a.game_type == "lol"

    def test_02_connect_default(self):
        a = self._make()
        assert a.connect({}) is True
        assert a.is_connected

    def test_03_connect_invalid_port(self):
        a = self._make()
        assert a.connect({"port": -1}) is False

    def test_04_decode_allgamedata(self):
        a = self._make()
        raw = {"allPlayers": [{"summonerName": "Test", "championName": "Ahri", "level": 6, "team": "ORDER", "isDead": False, "scores": {"kills": 3, "deaths": 1, "assists": 5, "creepScore": 80}}], "gameData": {"gameTime": 600.0}, "activePlayer": {}}
        result = a.decode(raw)
        assert result["_decoded"] is True
        assert result["endpoint"] == "allgamedata"

    def test_05_decode_json_string(self):
        a = self._make()
        raw = json.dumps({"_endpoint": "gamestats", "gameTime": 100})
        result = a.decode(raw)
        assert result["_decoded"] is True

    def test_06_normalize_players(self):
        a = self._make()
        raw = {"allPlayers": [{"summonerName": "P1", "championName": "Zed", "level": 10, "team": "ORDER", "isDead": False, "scores": {"kills": 5, "deaths": 2, "assists": 3, "creepScore": 120}}], "gameData": {"gameTime": 900.0}}
        decoded = a.decode(raw)
        norm = a.normalize(decoded)
        assert len(norm["players"]) == 1
        assert norm["players"][0]["name"] == "P1"
        assert norm["game_time"] == 900.0

    def test_07_normalize_events(self):
        a = self._make()
        raw = {"events": [{"EventName": "DragonKill", "EventTime": 500.0}], "gameData": {"gameTime": 500.0}, "allPlayers": []}
        norm = a.decode_and_normalize(raw)
        assert len(norm["events"]) == 1
        assert norm["events"][0]["type"] == "DragonKill"

    def test_08_decode_invalid_type(self):
        a = self._make()
        result = a.decode(12345)
        assert result["_decoded"] is False

    def test_09_endpoint_stats(self):
        a = self._make()
        a.decode({"allPlayers": [], "gameData": {"gameTime": 0}})
        a.decode({"allPlayers": [], "gameData": {"gameTime": 0}})
        stats = a.get_endpoint_stats()
        assert stats.get("allgamedata", 0) == 2

    def test_10_disconnect_clears_stats(self):
        a = self._make()
        a.connect({})
        a.decode({"allPlayers": [], "gameData": {"gameTime": 0}})
        a.disconnect()
        assert a.get_endpoint_stats() == {}


# =========================================================================
# M668: Dota2ProtocolAdapter
# =========================================================================
class TestDota2ProtocolAdapter:

    def _make(self):
        from dota2_protocol_adapter import Dota2ProtocolAdapter
        return Dota2ProtocolAdapter()

    def test_01_game_type(self):
        assert self._make().game_type == "dota2"

    def test_02_connect(self):
        a = self._make()
        assert a.connect({"gsi_port": 3001}) is True

    def test_03_decode_gsi_payload(self):
        a = self._make()
        gsi = {"map": {"clock_time": 300, "game_state": "DOTA_GAMERULES_STATE_GAME_IN_PROGRESS"}, "hero": {"name": "npc_dota_hero_invoker", "level": 12, "alive": True}, "player": {"name": "TestPlayer", "kills": 4, "deaths": 2, "assists": 8, "last_hits": 150, "gold": 3000}}
        result = a.decode(gsi)
        assert result["_decoded"] is True
        assert "map" in result["sections"]

    def test_04_normalize_player(self):
        a = self._make()
        gsi = {"map": {"clock_time": 500}, "hero": {"name": "invoker", "level": 15, "alive": True}, "player": {"name": "Bot", "kills": 10, "deaths": 3, "assists": 12, "last_hits": 200, "gold": 5000}}
        norm = a.decode_and_normalize(gsi)
        assert len(norm["players"]) == 1
        assert norm["players"][0]["champion"] == "invoker"

    def test_05_normalize_resources(self):
        a = self._make()
        gsi = {"map": {}, "player": {"gold": 4000, "gold_reliable": 1000, "gold_unreliable": 3000}, "items": {"slot0": {"name": "item_blink"}}}
        norm = a.decode_and_normalize(gsi)
        assert norm["resources"]["gold"] == 4000

    def test_06_decode_json_string(self):
        a = self._make()
        result = a.decode(json.dumps({"map": {"clock_time": 100}}))
        assert result["_decoded"] is True

    def test_07_invalid_port(self):
        a = self._make()
        assert a.connect({"gsi_port": 99999}) is False

    def test_08_section_stats(self):
        a = self._make()
        a.decode({"map": {}, "hero": {}})
        stats = a.get_section_stats()
        assert "map" in stats

    def test_09_empty_payload(self):
        a = self._make()
        norm = a.decode_and_normalize({})
        assert norm.get("game_time", 0.0) == 0.0

    def test_10_abilities_as_events(self):
        a = self._make()
        gsi = {"map": {"clock_time": 100}, "abilities": {"ability0": {"name": "invoker_quas", "level": 4}}}
        norm = a.decode_and_normalize(gsi)
        assert any(e["type"] == "ability_state" for e in norm["events"])


# =========================================================================
# M669: MahjongProtocolAdapter
# =========================================================================
class TestMahjongProtocolAdapter:

    def _make(self):
        from mahjong_protocol_adapter import MahjongProtocolAdapter
        return MahjongProtocolAdapter()

    def test_01_game_type(self):
        assert self._make().game_type == "mahjong"

    def test_02_connect_mjai(self):
        a = self._make()
        assert a.connect({"protocol": "mjai"}) is True

    def test_03_connect_invalid_protocol(self):
        a = self._make()
        assert a.connect({"protocol": "invalid"}) is False

    def test_04_decode_mjai_tsumo(self):
        a = self._make()
        msg = {"type": "tsumo", "actor": 0, "pai": "5m"}
        result = a.decode(msg)
        assert result["_decoded"] is True
        assert result["protocol"] == "mjai"

    def test_05_decode_liqi(self):
        a = self._make()
        msg = {"method": ".lq.ActionPrototype", "msg_type": 1, "data": {}}
        result = a.decode(msg)
        assert result["protocol"] == "liqi"

    def test_06_normalize_round_start(self):
        a = self._make()
        msg = {"type": "start_kyoku", "bakaze": "E", "kyoku": 1, "honba": 0, "kyotaku": 0, "oya": 0, "scores": [25000, 25000, 25000, 25000]}
        norm = a.decode_and_normalize(msg)
        assert len(norm["players"]) == 4
        assert norm["map_state"]["round"] == 1

    def test_07_normalize_discard(self):
        a = self._make()
        msg = {"type": "dahai", "actor": 2, "pai": "3p"}
        norm = a.decode_and_normalize(msg)
        assert norm["events"][0]["type"] == "discard_tile"

    def test_08_action_stats(self):
        a = self._make()
        a.decode({"type": "tsumo", "actor": 0, "pai": "1s"})
        a.decode({"type": "dahai", "actor": 0, "pai": "9m"})
        stats = a.get_action_stats()
        assert stats["tsumo"] == 1
        assert stats["dahai"] == 1

    def test_09_invalid_seat(self):
        a = self._make()
        assert a.connect({"protocol": "mjai", "seat": 5}) is False

    def test_10_round_count(self):
        a = self._make()
        a.decode_and_normalize({"type": "start_kyoku", "oya": 0, "scores": [25000]*4})
        a.decode_and_normalize({"type": "start_kyoku", "oya": 1, "scores": [30000, 20000, 25000, 25000]})
        assert a.round_count == 2


# =========================================================================
# M670: UniversalGameStateSchema
# =========================================================================
class TestUniversalGameStateSchema:

    def _make(self):
        from universal_game_state_schema import UniversalGameStateSchema
        return UniversalGameStateSchema()

    def test_01_validate_valid_state(self):
        s = self._make()
        state = {"game_type": "lol", "game_time": 100.0, "players": [{"name": "P1"}], "events": []}
        errors = s.validate(state)
        assert len(errors) == 0

    def test_02_validate_missing_required(self):
        s = self._make()
        errors = s.validate({})
        assert any("game_type" in e for e in errors)

    def test_03_validate_wrong_type(self):
        s = self._make()
        errors = s.validate({"game_type": 123, "game_time": "bad", "players": "nope"})
        assert len(errors) >= 2

    def test_04_validate_player(self):
        s = self._make()
        errors = s.validate_player({"name": "Test", "level": 5})
        assert len(errors) == 0

    def test_05_apply_defaults(self):
        s = self._make()
        result = s.apply_defaults({"game_type": "lol"})
        assert "players" in result
        assert isinstance(result["players"], list)

    def test_06_register_game_mapping(self):
        s = self._make()
        s.register_game_field_mapping("lol", {"game_time": "gameTime"})
        m = s.get_field_mapping("lol")
        assert m["game_time"] == "gameTime"

    def test_07_schema_info(self):
        s = self._make()
        info = s.get_schema_info()
        assert "version" in info
        assert len(info["core_fields"]) > 0

    def test_08_validate_non_dict(self):
        s = self._make()
        errors = s.validate("not a dict")
        assert len(errors) > 0

    def test_09_register_custom_fields(self):
        from universal_game_state_schema import FieldSpec
        s = self._make()
        s.register_custom_fields("mahjong", [FieldSpec("hand_tiles", "list", True)])
        errors = s.validate({"game_type": "mahjong", "game_time": 0, "players": []})
        assert any("hand_tiles" in e for e in errors)

    def test_10_evolution_callback(self):
        s = self._make()
        events = []
        s.evolution_callback = lambda e: events.append(e)
        s.validate({})  # should trigger validation_failed
        assert len(events) > 0


# =========================================================================
# M671: CrossGameRewardNormalizer
# =========================================================================
class TestCrossGameRewardNormalizer:

    def _make(self):
        from cross_game_reward_normalizer import CrossGameRewardNormalizer
        return CrossGameRewardNormalizer()

    def test_01_lol_normalize(self):
        n = self._make()
        result = n.normalize("lol", 1.5)
        assert -1.0 <= result <= 1.0

    def test_02_dota2_normalize(self):
        n = self._make()
        result = n.normalize("dota2", 3000.0)
        assert -1.0 <= result <= 1.0

    def test_03_mahjong_normalize(self):
        n = self._make()
        result = n.normalize("mahjong", 25000.0)
        assert -1.0 <= result <= 1.0

    def test_04_unknown_game_fallback(self):
        n = self._make()
        for i in range(20):
            n.normalize("chess", float(i))
        result = n.normalize("chess", 10.0)
        assert -1.0 <= result <= 1.0

    def test_05_batch_normalize(self):
        n = self._make()
        results = n.batch_normalize("lol", [0.5, 1.0, -0.5, 2.0])
        assert len(results) == 4
        assert all(-1.0 <= r <= 1.0 for r in results)

    def test_06_register_custom(self):
        n = self._make()
        n.register_strategy("chess", lambda r, s: max(-1, min(1, r / 100)))
        result = n.normalize("chess", 50.0)
        assert abs(result - 0.5) < 0.01

    def test_07_stats(self):
        n = self._make()
        n.normalize("lol", 1.0)
        n.normalize("lol", -1.0)
        stats = n.get_stats("lol")
        assert stats["count"] == 2

    def test_08_all_stats(self):
        n = self._make()
        n.normalize("lol", 1.0)
        n.normalize("dota2", 500.0)
        all_s = n.get_all_stats()
        assert "lol" in all_s["games"]
        assert "dota2" in all_s["games"]

    def test_09_extreme_values_clamped(self):
        n = self._make()
        r = n.normalize("lol", 999999.0)
        assert r == 1.0
        r2 = n.normalize("lol", -999999.0)
        assert r2 == -1.0

    def test_10_auto_calibration(self):
        from cross_game_reward_normalizer import CrossGameRewardNormalizer
        n = CrossGameRewardNormalizer(use_auto_calibration=True)
        for i in range(20):
            n.normalize("lol", float(i))
        r = n.normalize("lol", 10.0)
        assert -1.0 <= r <= 1.0


# =========================================================================
# M672: CrossGameActionSpaceUnifier
# =========================================================================
class TestCrossGameActionSpaceUnifier:

    def _make(self):
        from cross_game_action_space_unifier import CrossGameActionSpaceUnifier
        u = CrossGameActionSpaceUnifier()
        u.register_game_actions("lol", {"buy_item": "acquire_resource", "cast_spell": "use_ability", "move_to": "move", "auto_attack": "attack"})
        return u

    def test_01_encode(self):
        u = self._make()
        assert u.encode("lol", "buy_item") == "acquire_resource"

    def test_02_decode(self):
        u = self._make()
        assert u.decode("lol", "acquire_resource") == "buy_item"

    def test_03_unknown_action(self):
        u = self._make()
        assert u.encode("lol", "nonexistent") == "unknown"

    def test_04_batch_encode(self):
        u = self._make()
        results = u.batch_encode("lol", ["buy_item", "cast_spell"])
        assert results == ["acquire_resource", "use_ability"]

    def test_05_unregistered_game(self):
        u = self._make()
        assert u.encode("chess", "move_piece") == "unknown"

    def test_06_coverage(self):
        u = self._make()
        cov = u.get_coverage("lol")
        assert cov["mapped_actions"] == 4
        assert cov["coverage_ratio"] > 0

    def test_07_register_category(self):
        u = self._make()
        u.register_category("trade", "Economic trading action")
        cats = u.get_abstract_categories()
        assert "trade" in cats

    def test_08_action_stats(self):
        u = self._make()
        u.encode("lol", "buy_item")
        u.encode("lol", "buy_item")
        u.encode("lol", "cast_spell")
        stats = u.get_action_stats("lol")
        assert stats["buy_item"] == 2

    def test_09_multi_game(self):
        u = self._make()
        u.register_game_actions("mahjong", {"discard": "attack", "draw": "acquire_resource"})
        assert u.encode("mahjong", "discard") == "attack"

    def test_10_list_games(self):
        u = self._make()
        assert "lol" in u.list_registered_games()


# =========================================================================
# M673: CrossGameTrainingDataFormatter
# =========================================================================
class TestCrossGameTrainingDataFormatter:

    def _make(self):
        from cross_game_training_data_formatter import CrossGameTrainingDataFormatter
        return CrossGameTrainingDataFormatter()

    def test_01_format_lol(self):
        f = self._make()
        sample = f.format_sample("lol", {"game_time": 600, "gold": 5000, "action": "buy_item", "reward": 0.5})
        assert sample.action == "buy_item"
        assert sample.reward == 0.5

    def test_02_format_dota2(self):
        f = self._make()
        sample = f.format_sample("dota2", {"game_time": 300, "gold": 3000, "action": "farm", "reward": 0.3})
        assert sample.state["gold"] == 3000

    def test_03_format_mahjong(self):
        f = self._make()
        sample = f.format_sample("mahjong", {"round": 3, "seat": 1, "action": "dahai", "reward": 0.1})
        assert sample.action == "dahai"

    def test_04_unknown_game(self):
        f = self._make()
        try:
            f.format_sample("chess", {})
            assert False, "should raise"
        except ValueError:
            pass

    def test_05_batch_format(self):
        f = self._make()
        samples, errors = f.batch_format("lol", [{"action": "a", "reward": 0.1}, {"action": "b", "reward": 0.2}])
        assert len(samples) == 2
        assert len(errors) == 0

    def test_06_batch_with_errors(self):
        f = self._make()
        # register a formatter that raises on specific input
        f.register_formatter("broken", lambda r: (_ for _ in ()).throw(ValueError("bad")))
        samples, errors = f.batch_format("broken", [{"x": 1}])
        assert len(errors) == 1

    def test_07_export_json(self):
        f = self._make()
        sample = f.format_sample("lol", {"action": "a", "reward": 0.5})
        j = f.export_as_json([sample])
        parsed = json.loads(j)
        assert len(parsed) == 1
        assert parsed[0]["action"] == "a"

    def test_08_export_dicts(self):
        f = self._make()
        sample = f.format_sample("lol", {"action": "x", "reward": 1.0})
        dicts = f.export_as_dicts([sample])
        assert dicts[0]["reward"] == 1.0

    def test_09_stats(self):
        f = self._make()
        f.format_sample("lol", {"action": "a", "reward": 0.1})
        f.format_sample("dota2", {"action": "b", "reward": 0.2})
        stats = f.get_stats()
        assert stats["format_count"] == 2

    def test_10_custom_formatter(self):
        from cross_game_training_data_formatter import CrossGameTrainingDataFormatter, TrainingSample
        f = CrossGameTrainingDataFormatter()
        f.register_formatter("chess", lambda r: TrainingSample({"board": r.get("fen", "")}, r.get("move", ""), r.get("reward", 0.0)))
        s = f.format_sample("chess", {"fen": "rnbq...", "move": "e2e4", "reward": 0.5})
        assert s.action == "e2e4"


# =========================================================================
# M674: GameAdapterRegistry
# =========================================================================
class TestGameAdapterRegistry:

    def _make(self):
        from game_adapter_registry import GameAdapterRegistry
        from lol_protocol_adapter import LolProtocolAdapter
        from dota2_protocol_adapter import Dota2ProtocolAdapter
        r = GameAdapterRegistry()
        r.register(LolProtocolAdapter())
        r.register(Dota2ProtocolAdapter())
        return r

    def test_01_register_and_get(self):
        r = self._make()
        assert r.get("lol") is not None
        assert r.get("lol").game_type == "lol"

    def test_02_get_nonexistent(self):
        r = self._make()
        assert r.get("chess") is None

    def test_03_list_adapters(self):
        r = self._make()
        adapters = r.list_adapters()
        assert len(adapters) == 2

    def test_04_unregister(self):
        r = self._make()
        assert r.unregister("lol") is True
        assert r.get("lol") is None

    def test_05_unregister_nonexistent(self):
        r = self._make()
        assert r.unregister("chess") is False

    def test_06_connect_all(self):
        r = self._make()
        results = r.connect_all({"lol": {"port": 2999}, "dota2": {"gsi_port": 3001}})
        assert results["lol"] is True
        assert results["dota2"] is True

    def test_07_disconnect_all(self):
        r = self._make()
        r.connect_all({})
        r.disconnect_all()
        for a in r.list_adapters():
            assert a["is_connected"] is False

    def test_08_health_all(self):
        r = self._make()
        h = r.get_health_all()
        assert h["total_adapters"] == 2

    def test_09_replace_adapter(self):
        from lol_protocol_adapter import LolProtocolAdapter
        r = self._make()
        new_lol = LolProtocolAdapter()
        r.register(new_lol)
        assert r.get("lol") is new_lol

    def test_10_stats(self):
        r = self._make()
        s = r.get_stats()
        assert s["registered_count"] == 2


# =========================================================================
# M675: TransferLearningFeatureAligner
# =========================================================================
class TestTransferLearningFeatureAligner:

    def _make(self):
        from transfer_learning_feature_aligner import TransferLearningFeatureAligner, FeatureDesc
        a = TransferLearningFeatureAligner()
        a.register_features("lol", [
            FeatureDesc("gold", "float", 0, 30000, "resource"),
            FeatureDesc("level", "int", 1, 18, "progression"),
            FeatureDesc("kills", "int", 0, 50, "combat"),
        ])
        a.register_features("dota2", [
            FeatureDesc("net_worth", "float", 0, 50000, "resource"),
            FeatureDesc("hero_level", "int", 1, 30, "progression"),
            FeatureDesc("kill_count", "int", 0, 60, "combat"),
        ])
        return a

    def test_01_alignment(self):
        a = self._make()
        aligned = a.align("lol", "dota2")
        assert len(aligned) == 3

    def test_02_similarity_same_category(self):
        from transfer_learning_feature_aligner import FeatureDesc
        a = self._make()
        f1 = FeatureDesc("gold", "float", 0, 30000, "resource")
        f2 = FeatureDesc("net_worth", "float", 0, 50000, "resource")
        sim = a.compute_similarity(f1, f2)
        assert sim > 0.5

    def test_03_similarity_different_category(self):
        from transfer_learning_feature_aligner import FeatureDesc
        a = self._make()
        f1 = FeatureDesc("gold", "float", 0, 30000, "resource")
        f2 = FeatureDesc("kills", "int", 0, 50, "combat")
        sim = a.compute_similarity(f1, f2)
        assert sim < 0.5

    def test_04_recommend_transferable(self):
        a = self._make()
        recs = a.recommend_transferable("lol", "dota2", threshold=0.5)
        assert len(recs) > 0
        assert all(r["similarity"] >= 0.5 for r in recs)

    def test_05_empty_source(self):
        a = self._make()
        aligned = a.align("chess", "dota2")
        assert len(aligned) == 0

    def test_06_stats(self):
        a = self._make()
        s = a.get_stats()
        assert "lol" in s["registered_games"]

    def test_07_alignment_sorted(self):
        a = self._make()
        aligned = a.align("lol", "dota2")
        sims = [a_["similarity"] for a_ in aligned]
        assert sims == sorted(sims, reverse=True)

    def test_08_bidirectional(self):
        a = self._make()
        fwd = a.align("lol", "dota2")
        rev = a.align("dota2", "lol")
        assert len(fwd) == len(rev)

    def test_09_self_alignment(self):
        a = self._make()
        aligned = a.align("lol", "lol")
        assert len(aligned) == 3
        assert all(a_["similarity"] == 1.0 for a_ in aligned)

    def test_10_evolution_callback(self):
        a = self._make()
        events = []
        a.evolution_callback = lambda e: events.append(e)
        a.align("lol", "dota2")
        assert len(events) > 0


# =========================================================================
# M676-M685: Abbreviated tests for remaining modules
# =========================================================================

class TestCrossGameModelHub:

    def _make(self):
        from cross_game_model_hub import CrossGameModelHub
        return CrossGameModelHub()

    def test_01_save_load(self):
        h = self._make()
        h.save_model("lol", "policy", "v1", {"w": [1, 2, 3]})
        m = h.load_model("lol", "policy", "v1")
        assert m is not None
        assert m.weights == {"w": [1, 2, 3]}

    def test_02_load_nonexistent(self):
        assert self._make().load_model("x", "y", "z") is None

    def test_03_load_latest(self):
        h = self._make()
        h.save_model("lol", "p", "v1", {"a": 1})
        h.save_model("lol", "p", "v2", {"a": 2})
        m = h.load_latest("lol", "p")
        assert m.version == "v2"

    def test_04_list_models(self):
        h = self._make()
        h.save_model("lol", "p", "v1", {})
        h.save_model("dota2", "q", "v1", {})
        assert len(h.list_models()) == 2
        assert len(h.list_models("lol")) == 1

    def test_05_list_versions(self):
        h = self._make()
        h.save_model("lol", "p", "v1", {})
        h.save_model("lol", "p", "v2", {})
        assert h.list_versions("lol", "p") == ["v1", "v2"]

    def test_06_transfer_history(self):
        h = self._make()
        h.record_transfer("lol", "dota2", "policy")
        assert len(h.get_transfer_history()) == 1

    def test_07_lineage(self):
        h = self._make()
        h.save_model("lol", "p", "v1", {}, lineage={"src": "riot_api"})
        lin = h.get_lineage("lol", "p")
        assert lin[0]["lineage"]["src"] == "riot_api"

    def test_08_stats(self):
        h = self._make()
        h.save_model("lol", "p", "v1", {})
        s = h.get_stats()
        assert s["save_count"] == 1

    def test_09_metadata(self):
        h = self._make()
        h.save_model("lol", "p", "v1", {}, metadata={"epochs": 100})
        m = h.load_model("lol", "p", "v1")
        assert m.metadata["epochs"] == 100

    def test_10_evolution(self):
        h = self._make()
        events = []
        h.evolution_callback = lambda e: events.append(e)
        h.save_model("lol", "p", "v1", {})
        assert len(events) > 0


class TestProtocolAdapterTestHarness:

    def _make(self):
        from protocol_adapter_test_harness import ProtocolAdapterTestHarness
        return ProtocolAdapterTestHarness()

    def _make_adapter(self):
        from lol_protocol_adapter import LolProtocolAdapter
        return LolProtocolAdapter()

    def test_01_run_all(self):
        h = self._make()
        a = self._make_adapter()
        results = h.run_all(a, {"allPlayers": [], "gameData": {"gameTime": 0}})
        assert len(results) >= 8

    def test_02_pass_rate(self):
        h = self._make()
        a = self._make_adapter()
        results = h.run_all(a, {"allPlayers": [], "gameData": {"gameTime": 0}})
        report = h.generate_report(results)
        assert report["pass_rate"] > 0.5

    def test_03_report_structure(self):
        h = self._make()
        a = self._make_adapter()
        results = h.run_all(a)
        report = h.generate_report(results)
        assert "total" in report
        assert "passed" in report

    def test_04_custom_test(self):
        h = self._make()
        h.register_test("always_pass", lambda a, d: None)
        a = self._make_adapter()
        results = h.run_all(a)
        names = [r.name for r in results]
        assert "always_pass" in names

    def test_05_custom_test_fail(self):
        h = self._make()
        h.register_test("always_fail", lambda a, d: (_ for _ in ()).throw(AssertionError("fail")))
        a = self._make_adapter()
        results = h.run_all(a)
        fail_result = [r for r in results if r.name == "always_fail"][0]
        assert fail_result.passed is False

    def test_06_timing(self):
        h = self._make()
        a = self._make_adapter()
        results = h.run_all(a)
        assert all(r.elapsed_ms >= 0 for r in results)

    def test_07_evolution(self):
        h = self._make()
        events = []
        h.evolution_callback = lambda e: events.append(e)
        a = self._make_adapter()
        h.run_all(a)
        assert len(events) > 0

    def test_08_dota2_adapter(self):
        from dota2_protocol_adapter import Dota2ProtocolAdapter
        h = self._make()
        a = Dota2ProtocolAdapter()
        results = h.run_all(a, {"map": {"clock_time": 100}})
        report = h.generate_report(results)
        assert report["passed"] > 0

    def test_09_mahjong_adapter(self):
        from mahjong_protocol_adapter import MahjongProtocolAdapter
        h = self._make()
        a = MahjongProtocolAdapter()
        results = h.run_all(a, {"type": "tsumo", "actor": 0, "pai": "5m"})
        assert any(r.passed for r in results)

    def test_10_failed_tests_list(self):
        h = self._make()
        a = self._make_adapter()
        results = h.run_all(a)
        report = h.generate_report(results)
        assert isinstance(report["failed_tests"], list)


class TestCrossGamePerformanceComparator:

    def _make(self):
        from cross_game_performance_comparator import CrossGamePerformanceComparator
        return CrossGamePerformanceComparator()

    def test_01_add_and_compare(self):
        c = self._make()
        c.add_game_record("lol", {"win": True, "kills": 5, "deaths": 2, "assists": 8})
        c.add_game_record("dota2", {"win": False, "kills": 3, "deaths": 5, "assists": 4})
        comp = c.compare()
        assert comp["best_winrate"] == "lol"

    def test_02_empty_compare(self):
        c = self._make()
        comp = c.compare()
        assert comp["best_winrate"] is None

    def test_03_game_stats(self):
        c = self._make()
        c.add_game_record("lol", {"win": True, "kills": 10, "deaths": 1})
        stats = c.get_game_stats("lol")
        assert stats["winrate"] == 1.0

    def test_04_common_strengths(self):
        c = self._make()
        for _ in range(10):
            c.add_game_record("lol", {"win": True, "kills": 8, "deaths": 1, "assists": 10})
            c.add_game_record("dota2", {"win": True, "kills": 7, "deaths": 2, "assists": 12})
        strengths = c.get_common_strengths()
        assert "high_winrate_across_games" in strengths

    def test_05_common_weaknesses(self):
        c = self._make()
        for _ in range(10):
            c.add_game_record("lol", {"win": False, "kills": 2, "deaths": 8})
            c.add_game_record("dota2", {"win": False, "kills": 1, "deaths": 9})
        weaknesses = c.get_common_weaknesses()
        assert "high_death_rate_across_games" in weaknesses

    def test_06_stats(self):
        c = self._make()
        c.add_game_record("lol", {"win": True})
        s = c.get_stats()
        assert s["record_count"] == 1

    def test_07_nonexistent_game(self):
        c = self._make()
        assert c.get_game_stats("chess")["games"] == 0

    def test_08_multiple_records(self):
        c = self._make()
        c.add_game_record("lol", {"win": True})
        c.add_game_record("lol", {"win": False})
        stats = c.get_game_stats("lol")
        assert abs(stats["winrate"] - 0.5) < 0.01

    def test_09_kda_comparison(self):
        c = self._make()
        c.add_game_record("lol", {"win": True, "kills": 10, "deaths": 2, "assists": 5})
        c.add_game_record("dota2", {"win": True, "kills": 3, "deaths": 8, "assists": 2})
        comp = c.compare()
        assert comp["best_kda"] == "lol"

    def test_10_consistency(self):
        c = self._make()
        for _ in range(10):
            c.add_game_record("lol", {"win": True})
            c.add_game_record("dota2", {"win": True})
        strengths = c.get_common_strengths()
        assert "consistent_performance" in strengths


class TestMultiGameSessionManager:

    def _make(self):
        from multi_game_session_manager import MultiGameSessionManager
        return MultiGameSessionManager()

    def test_01_create_session(self):
        m = self._make()
        sid = m.create_session("lol", {"port": 2999})
        assert sid.startswith("lol_")

    def test_02_start_stop(self):
        m = self._make()
        sid = m.create_session("lol")
        assert m.start_session(sid) is True
        assert m.get_session(sid).state == "running"
        assert m.stop_session(sid) is True
        assert m.get_session(sid).state == "stopped"

    def test_03_pause_resume(self):
        m = self._make()
        sid = m.create_session("lol")
        m.start_session(sid)
        assert m.pause_session(sid) is True
        assert m.get_session(sid).state == "paused"
        assert m.resume_session(sid) is True
        assert m.get_session(sid).state == "running"

    def test_04_record_packets(self):
        m = self._make()
        sid = m.create_session("lol")
        m.record_packet(sid)
        m.record_packet(sid)
        assert m.get_session(sid).packet_count == 2

    def test_05_multi_game(self):
        m = self._make()
        s1 = m.create_session("lol")
        s2 = m.create_session("dota2")
        assert len(m.get_all_sessions()) == 2

    def test_06_filter_by_game(self):
        m = self._make()
        m.create_session("lol")
        m.create_session("dota2")
        assert len(m.get_sessions_by_game("lol")) == 1

    def test_07_remove(self):
        m = self._make()
        sid = m.create_session("lol")
        assert m.remove_session(sid) is True
        assert m.get_session(sid) is None

    def test_08_stats(self):
        m = self._make()
        m.create_session("lol")
        s = m.get_stats()
        assert s["total_sessions"] == 1

    def test_09_stop_idempotent(self):
        m = self._make()
        sid = m.create_session("lol")
        m.start_session(sid)
        m.stop_session(sid)
        assert m.stop_session(sid) is True

    def test_10_invalid_transitions(self):
        m = self._make()
        sid = m.create_session("lol")
        assert m.pause_session(sid) is False  # can't pause from created
        assert m.resume_session(sid) is False  # can't resume from created


class TestMultiGamePipelineOrchestrator:

    def _make(self):
        from multi_game_pipeline_orchestrator import MultiGamePipelineOrchestrator
        from lol_protocol_adapter import LolProtocolAdapter
        o = MultiGamePipelineOrchestrator()
        o.register_game_pipeline("lol", LolProtocolAdapter(), {"port": 2999})
        return o

    def test_01_register(self):
        o = self._make()
        d = o.get_dashboard()
        assert d["total_games"] == 1

    def test_02_start_stop(self):
        o = self._make()
        assert o.start_game("lol") is True
        assert o.get_game_status("lol")["state"] == "running"
        assert o.stop_game("lol") is True
        assert o.get_game_status("lol")["state"] == "stopped"

    def test_03_start_all(self):
        o = self._make()
        o.register_game_pipeline("dota2")
        results = o.start_all()
        assert results["lol"] is True

    def test_04_run_cycle(self):
        o = self._make()
        o.start_game("lol")
        result = o.run_cycle("lol", {"allPlayers": [], "gameData": {"gameTime": 100}})
        assert result["status"] == "ok"

    def test_05_run_cycle_not_running(self):
        o = self._make()
        result = o.run_cycle("lol")
        assert result["status"] == "error"

    def test_06_share_model(self):
        o = self._make()
        o.register_game_pipeline("dota2")
        assert o.share_model("lol", "dota2", "policy") is True

    def test_07_dashboard(self):
        o = self._make()
        d = o.get_dashboard()
        assert "pipelines" in d
        assert "lol" in d["pipelines"]

    def test_08_stats(self):
        o = self._make()
        s = o.get_stats()
        assert "lol" in s["game_types"]

    def test_09_nonexistent_game(self):
        o = self._make()
        assert o.start_game("chess") is False

    def test_10_evolution(self):
        o = self._make()
        events = []
        o.evolution_callback = lambda e: events.append(e)
        o.start_game("lol")
        assert len(events) > 0


# =========================================================================
# Runner
# =========================================================================
def run_all_tests():
    """Run all test classes and report results."""
    test_classes = [
        TestGameProtocolAdapterBase,
        TestLolProtocolAdapter,
        TestDota2ProtocolAdapter,
        TestMahjongProtocolAdapter,
        TestUniversalGameStateSchema,
        TestCrossGameRewardNormalizer,
        TestCrossGameActionSpaceUnifier,
        TestCrossGameTrainingDataFormatter,
        TestGameAdapterRegistry,
        TestTransferLearningFeatureAligner,
        TestCrossGameModelHub,
        TestProtocolAdapterTestHarness,
        TestCrossGamePerformanceComparator,
        TestMultiGameSessionManager,
        TestMultiGamePipelineOrchestrator,
    ]

    total = 0
    passed = 0
    failed = 0
    errors = []

    for cls in test_classes:
        instance = cls()
        methods = sorted([m for m in dir(instance) if m.startswith("test_")])
        for method_name in methods:
            total += 1
            method = getattr(instance, method_name)
            try:
                method()
                passed += 1
                print(f"  PASS  {cls.__name__}.{method_name}")
            except Exception as exc:
                failed += 1
                errors.append((f"{cls.__name__}.{method_name}", str(exc)))
                print(f"  FAIL  {cls.__name__}.{method_name}: {exc}")

    print(f"\n{'='*60}")
    print(f"Results: {passed}/{total} passed, {failed} failed")
    print(f"Pass rate: {passed/max(total,1)*100:.1f}%")
    if errors:
        print(f"\nFailed tests:")
        for name, err in errors:
            print(f"  - {name}: {err}")
    return passed, failed


if __name__ == "__main__":
    run_all_tests()
