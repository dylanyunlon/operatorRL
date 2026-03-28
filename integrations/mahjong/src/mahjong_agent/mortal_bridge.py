"""
Mortal Bridge — Tenhou/Majsoul engine interface for operatorRL.

Provides GameBridgeABC implementation for Mahjong, bridging Mortal's
react_batch / MortalEngine interface into the operatorRL unified framework.

Location: integrations/mahjong/src/mahjong_agent/mortal_bridge.py

Reference (拿来主义):
  - Mortal/mortal/engine.py: MortalEngine.__init__, react_batch, _react_batch
  - Mortal/mortal/player.py: TestPlayer engine setup pattern
  - modules/game_bridge_abc.py: GameBridgeABC connect/disconnect/get_game_state/send_action
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "integrations.mahjong.mortal_bridge.v1"


class MortalBridge:
    """Bridge adapter connecting Mortal-style mahjong engines to operatorRL.

    Mirrors MortalEngine's react_batch interface but wraps it for
    GameBridgeABC compliance: connect/disconnect/get_game_state/send_action.

    Attributes:
        connected: Whether the bridge is currently connected.
        engine_type: Identifies this as a mortal-compatible bridge.
        evolution_callback: Optional callback for self-evolution events.
    """

    def __init__(
        self,
        engine_type: str = "mortal",
        name: str = "operatorRL-mahjong",
        boltzmann_epsilon: float = 0.0,
        boltzmann_temp: float = 1.0,
    ) -> None:
        # --- Mortal engine.py fields (拿来主义) ---
        self.engine_type = engine_type
        self.name = name
        self.boltzmann_epsilon = boltzmann_epsilon
        self.boltzmann_temp = boltzmann_temp

        # --- GameBridgeABC fields ---
        self.connected: bool = False
        self._game_state: dict[str, Any] = {}

        # --- Evolution pattern ---
        self.evolution_callback: Optional[Callable] = None

    # ---- GameBridgeABC interface ----

    @property
    def game_name(self) -> str:
        return "mahjong"

    def connect(self) -> None:
        """Establish connection to Mortal engine / game server."""
        self.connected = True
        self._game_state = {
            "round": 0,
            "turn": 0,
            "hand_34": [0] * 34,
            "discards": [],
            "melds": [],
            "dora_indicators": [],
            "scores": [25000, 25000, 25000, 25000],
        }
        logger.info("MortalBridge connected")

    def disconnect(self) -> None:
        """Close connection."""
        self.connected = False
        self._game_state = {}
        logger.info("MortalBridge disconnected")

    def get_game_state(self) -> dict[str, Any]:
        """Return current game state."""
        return dict(self._game_state)

    def send_action(self, action: Any) -> bool:
        """Send an action (mjai format dict) to the game.

        Args:
            action: Dict with at least 'type' key (e.g. 'dahai', 'chi', 'pon').

        Returns:
            True if action was accepted.
        """
        if not self.connected:
            return False
        if not isinstance(action, dict) or "type" not in action:
            return False
        self._game_state.setdefault("action_history", []).append(action)
        return True

    # ---- Mortal engine interface (拿来主义 from engine.py) ----

    def react(
        self, obs: list[float], masks: list[bool]
    ) -> tuple[int, list[float]]:
        """Single-observation react (simplified from react_batch).

        Mirrors MortalEngine._react_batch but for single observation.

        Args:
            obs: Observation vector (length 34 for tile counts).
            masks: Action mask (True = action is legal).

        Returns:
            Tuple of (action_index, q_values).
        """
        n = len(obs)
        # Stub Q-values: use observation directly
        q_values = [obs[i] if (i < len(masks) and masks[i]) else -1e9 for i in range(n)]

        # Boltzmann sampling (拿来主义 from engine.py)
        if self.boltzmann_epsilon > 0:
            import random
            if random.random() > self.boltzmann_epsilon:
                action = max(range(n), key=lambda i: q_values[i])
            else:
                valid = [i for i in range(n) if i < len(masks) and masks[i]]
                action = random.choice(valid) if valid else 0
        else:
            action = max(range(n), key=lambda i: q_values[i])

        return action, q_values

    # ---- mjai protocol helper ----

    def format_mjai_action(
        self,
        action_type: str,
        actor: int = 0,
        pai: str = "",
        tsumogiri: bool = False,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Format an action as an mjai protocol message.

        Reference: Mortal ExampleMjaiLogEngine response format.

        Returns:
            Dict with 'type', 'actor', 'pai', and optional fields.
        """
        msg: dict[str, Any] = {"type": action_type, "actor": actor}
        if pai:
            msg["pai"] = pai
        if action_type == "dahai":
            msg["tsumogiri"] = tsumogiri
        msg.update(kwargs)
        return msg

    # ---- Evolution pattern ----

    def _fire_evolution(self, data: dict[str, Any]) -> None:
        """Fire evolution callback if registered."""
        if self.evolution_callback is not None:
            self.evolution_callback(_EVOLUTION_KEY, data)
