"""
Mortal Adapter — bridges Mortal DRL engine to MjaiBotBase interface.

Provides a clean adapter between the operatorRL mahjong agent and
Mortal's deep reinforcement learning model for mahjong decision-making.

When the actual Mortal model is not available, operates in stub mode
returning {"type": "none"} for all decisions.

Location: integrations/mahjong/src/mahjong_agent/models/mortal_adapter.py
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Optional

from mahjong_agent.models.mjai_bot_base import MjaiBotBase

logger = logging.getLogger(__name__)


@dataclass
class MortalConfig:
    """Configuration for the Mortal adapter."""
    model_path: str = ""
    is_3p: bool = False
    online: bool = False
    device: str = "cpu"


class MortalAdapter(MjaiBotBase):
    """Adapter wrapping Mortal DRL engine as an MjaiBotBase.

    If the Mortal model is not available (model_path empty or load fails),
    operates in stub mode — all react() calls return {"type": "none"}.
    """

    def __init__(self, config: MortalConfig | None = None) -> None:
        super().__init__()
        self._config = config or MortalConfig()
        self._model: Any = None

    def react(self, events_json: str) -> str:
        """Process mjai events through Mortal model.

        Handles start_game/end_game lifecycle. Delegates all other
        events to the loaded Mortal model (or returns none in stub mode).
        """
        try:
            events = json.loads(events_json)
        except json.JSONDecodeError as e:
            logger.error("Failed to parse events: %s", e)
            return json.dumps({"type": "none"}, separators=(",", ":"))

        return_action = None

        for event in events:
            event_type = event.get("type", "")

            if event_type == "start_game":
                self.player_id = event.get("id", 0)
                self._model = self._load_model()
                continue

            if event_type == "end_game":
                self.player_id = None
                self._model = None
                continue

            # Delegate to model if available
            if self._model is not None:
                try:
                    return_action = self._model.react(
                        json.dumps(event, separators=(",", ":"))
                    )
                except Exception as e:
                    logger.error("Mortal model error: %s", e)
                    return_action = None

        if return_action is None:
            result: dict[str, Any] = {"type": "none"}
            if self._config.online:
                result["meta"] = {"online": True}
            return json.dumps(result, separators=(",", ":"))

        # Inject online metadata if configured
        if self._config.online:
            try:
                data = json.loads(return_action)
                if "meta" not in data:
                    data["meta"] = {}
                data["meta"]["online"] = True
                return json.dumps(data, separators=(",", ":"))
            except json.JSONDecodeError:
                pass

        return return_action

    def _load_model(self) -> Any:
        """Attempt to load the Mortal model.

        Returns the model object, or None if unavailable.
        """
        if not self._config.model_path:
            logger.info("No model_path configured; Mortal in stub mode")
            return None

        try:
            # In production, this would import mortal.model and load weights
            # For now, return None (stub mode)
            logger.info(
                "Mortal model loading from %s (stub mode — actual model not bundled)",
                self._config.model_path,
            )
            return None
        except Exception as e:
            logger.error("Failed to load Mortal model: %s", e)
            return None

    @property
    def metadata(self) -> dict[str, Any]:
        """Mortal-specific metadata."""
        return {
            "bot_type": "mortal",
            "player_id": self.player_id,
            "class": self.__class__.__name__,
            "model_loaded": self._model is not None,
            "is_3p": self._config.is_3p,
            "online": self._config.online,
        }
