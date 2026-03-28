"""
TDD Tests for M235-M236: Models subpackage.

M235: models/__init__.py
M236: mjai_bot_base.py (标准化决策接口)

10 tests for mjai bot base. Expected ~50% failure.

Location: integrations/mahjong/tests/test_models.py
"""

import json
import pytest


class TestModelsInit:
    """Tests for models/__init__.py"""

    def test_models_package_importable(self):
        from mahjong_agent.models import __doc__

    def test_models_exports_bot_base(self):
        from mahjong_agent.models import MjaiBotBase
        assert MjaiBotBase is not None


class TestMjaiBotBase:
    """Tests for mjai_bot_base.py — standardized decision interface.
    
    Ported from Akagi mjai_bot/base/bot.py with clean abstraction.
    """

    def test_bot_base_is_abstract(self):
        from mahjong_agent.models.mjai_bot_base import MjaiBotBase
        with pytest.raises(TypeError):
            MjaiBotBase()

    def test_bot_base_requires_react(self):
        from mahjong_agent.models.mjai_bot_base import MjaiBotBase
        class BadBot(MjaiBotBase):
            pass
        with pytest.raises(TypeError):
            BadBot()

    def test_bot_concrete_subclass_works(self):
        from mahjong_agent.models.mjai_bot_base import MjaiBotBase
        class DummyBot(MjaiBotBase):
            def react(self, events_json: str) -> str:
                return json.dumps({"type": "none"})
        bot = DummyBot()
        result = json.loads(bot.react('[{"type":"start_game","id":0,"names":["0","1","2","3"]}]'))
        assert result["type"] == "none"

    def test_bot_player_id_initially_none(self):
        from mahjong_agent.models.mjai_bot_base import MjaiBotBase
        class DummyBot(MjaiBotBase):
            def react(self, events_json: str) -> str:
                return json.dumps({"type": "none"})
        bot = DummyBot()
        assert bot.player_id is None

    def test_bot_start_game_sets_player_id(self):
        from mahjong_agent.models.mjai_bot_base import MjaiBotBase
        class SmartBot(MjaiBotBase):
            def react(self, events_json: str) -> str:
                events = json.loads(events_json)
                for e in events:
                    if e.get("type") == "start_game":
                        self.player_id = e["id"]
                return json.dumps({"type": "none"})
        bot = SmartBot()
        bot.react('[{"type":"start_game","id":2,"names":["0","1","2","3"]}]')
        assert bot.player_id == 2

    def test_bot_end_game_clears_player_id(self):
        from mahjong_agent.models.mjai_bot_base import MjaiBotBase
        class SmartBot(MjaiBotBase):
            def react(self, events_json: str) -> str:
                events = json.loads(events_json)
                for e in events:
                    if e.get("type") == "start_game":
                        self.player_id = e["id"]
                    elif e.get("type") == "end_game":
                        self.player_id = None
                return json.dumps({"type": "none"})
        bot = SmartBot()
        bot.react('[{"type":"start_game","id":0,"names":["0","1","2","3"]}]')
        bot.react('[{"type":"end_game"}]')
        assert bot.player_id is None

    def test_bot_react_handles_malformed_json(self):
        from mahjong_agent.models.mjai_bot_base import MjaiBotBase
        class SafeBot(MjaiBotBase):
            def react(self, events_json: str) -> str:
                try:
                    events = json.loads(events_json)
                except json.JSONDecodeError:
                    return json.dumps({"type": "none"})
                return json.dumps({"type": "none"})
        bot = SafeBot()
        result = json.loads(bot.react("not valid json"))
        assert result["type"] == "none"

    def test_bot_has_metadata_property(self):
        from mahjong_agent.models.mjai_bot_base import MjaiBotBase
        class MetaBot(MjaiBotBase):
            def react(self, events_json: str) -> str:
                return json.dumps({"type": "none"})
        bot = MetaBot()
        assert hasattr(bot, 'metadata')
        meta = bot.metadata
        assert isinstance(meta, dict)
        assert "bot_type" in meta
