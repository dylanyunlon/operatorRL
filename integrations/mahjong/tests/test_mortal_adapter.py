"""
TDD Tests for M240: Mortal adapter — bridges Mortal DRL engine to MjaiBotBase.

10 tests. Expected ~50% failure.

Location: integrations/mahjong/tests/test_mortal_adapter.py
"""

import json
import pytest


class TestMortalAdapter:
    """Tests for models/mortal_adapter.py"""

    def test_mortal_adapter_inherits_bot_base(self):
        from mahjong_agent.models.mortal_adapter import MortalAdapter
        from mahjong_agent.models.mjai_bot_base import MjaiBotBase
        adapter = MortalAdapter()
        assert isinstance(adapter, MjaiBotBase)

    def test_mortal_adapter_model_initially_none(self):
        from mahjong_agent.models.mortal_adapter import MortalAdapter
        adapter = MortalAdapter()
        assert adapter._model is None

    def test_mortal_adapter_react_without_model(self):
        from mahjong_agent.models.mortal_adapter import MortalAdapter
        adapter = MortalAdapter()
        result = json.loads(adapter.react('[{"type":"tsumo","actor":0,"pai":"5m"}]'))
        assert result["type"] == "none"

    def test_mortal_adapter_start_game_initializes(self):
        from mahjong_agent.models.mortal_adapter import MortalAdapter
        adapter = MortalAdapter()
        adapter.react('[{"type":"start_game","id":0,"names":["0","1","2","3"]}]')
        assert adapter.player_id == 0

    def test_mortal_adapter_end_game_clears_model(self):
        from mahjong_agent.models.mortal_adapter import MortalAdapter
        adapter = MortalAdapter()
        adapter.react('[{"type":"start_game","id":0,"names":["0","1","2","3"]}]')
        adapter.react('[{"type":"end_game"}]')
        assert adapter._model is None
        assert adapter.player_id is None

    def test_mortal_adapter_metadata_shows_mortal(self):
        from mahjong_agent.models.mortal_adapter import MortalAdapter
        adapter = MortalAdapter()
        assert adapter.metadata["bot_type"] == "mortal"

    def test_mortal_adapter_config_model_path(self):
        from mahjong_agent.models.mortal_adapter import MortalAdapter, MortalConfig
        cfg = MortalConfig(model_path="/tmp/test_model.pth")
        adapter = MortalAdapter(config=cfg)
        assert adapter._config.model_path == "/tmp/test_model.pth"

    def test_mortal_adapter_supports_3p_flag(self):
        from mahjong_agent.models.mortal_adapter import MortalAdapter, MortalConfig
        cfg = MortalConfig(is_3p=True)
        adapter = MortalAdapter(config=cfg)
        assert adapter._config.is_3p is True

    def test_mortal_adapter_online_mode(self):
        from mahjong_agent.models.mortal_adapter import MortalAdapter, MortalConfig
        cfg = MortalConfig(online=True)
        adapter = MortalAdapter(config=cfg)
        assert adapter._config.online is True

    def test_mortal_adapter_react_returns_valid_json(self):
        from mahjong_agent.models.mortal_adapter import MortalAdapter
        adapter = MortalAdapter()
        adapter.react('[{"type":"start_game","id":0,"names":["0","1","2","3"]}]')
        result_str = adapter.react('[{"type":"tsumo","actor":0,"pai":"3s"}]')
        result = json.loads(result_str)
        assert "type" in result
