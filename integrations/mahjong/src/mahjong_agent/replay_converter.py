"""
Replay Converter — Converts Tenhou XML and Majsoul JSON replays to training spans.

Location: integrations/mahjong/src/mahjong_agent/replay_converter.py

Reference (拿来主义):
  - Mortal dataloader.py: replay file loading patterns
  - Akagi bridge: majsoul message format
  - DI-star replay_decoder.py: replay → training data pipeline
  - operatorRL ReplayAnalyzer: .dem → training span pattern
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "integrations.mahjong.replay_converter.v1"

_REWARD_MIN = -10.0
_REWARD_MAX = 10.0


class ReplayConverter:
    """Converts mahjong replay files to operatorRL training spans.

    Supports Tenhou XML format and Majsoul JSON format.
    Output spans follow the standard operatorRL format:
    {states: [...], actions: [...], reward: float}

    Attributes:
        evolution_callback: Optional callback for self-evolution events.
    """

    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable] = None

    def detect_format(self, data: str) -> str:
        """Detect replay format from content.

        Args:
            data: Raw string content.

        Returns:
            'tenhou', 'majsoul', or 'unknown'.
        """
        stripped = data.strip()
        if stripped.startswith("<mjloggm") or stripped.startswith("<?xml"):
            return "tenhou"
        elif stripped.startswith("{") and ('"head"' in stripped or '"data"' in stripped):
            return "majsoul"
        return "unknown"

    def convert_tenhou(self, xml_data: str) -> list[dict[str, Any]]:
        """Convert Tenhou XML replay to training spans.

        Args:
            xml_data: Raw XML string from Tenhou replay.

        Returns:
            List of training span dicts.
        """
        if not xml_data or not xml_data.strip():
            return []

        spans = []
        try:
            # Simple XML parsing without external dependencies
            # Extract INIT tags for hand data
            states = []
            actions = []

            idx = 0
            while True:
                init_start = xml_data.find("<INIT", idx)
                if init_start < 0:
                    break
                init_end = xml_data.find("/>", init_start)
                if init_end < 0:
                    break

                init_tag = xml_data[init_start:init_end + 2]

                # Extract hai0 (player 0 hand)
                hai_start = init_tag.find('hai0="')
                if hai_start >= 0:
                    hai_start += 6
                    hai_end = init_tag.find('"', hai_start)
                    hai_str = init_tag[hai_start:hai_end]
                    if hai_str:
                        tiles = [int(t) for t in hai_str.split(",") if t.strip()]
                        hand_34 = [0] * 34
                        for t136 in tiles:
                            hand_34[t136 // 4] += 1
                        states.append({"hand": hand_34})

                idx = init_end + 2

            if states:
                # Generate stub actions and reward
                for s in states:
                    actions.append(0)
                spans.append(self.build_training_span(states, actions, 0.0))

        except Exception as e:
            logger.warning("Tenhou XML parse error: %s", e)

        return spans

    def convert_majsoul(self, json_data: str) -> list[dict[str, Any]]:
        """Convert Majsoul JSON replay to training spans.

        Args:
            json_data: Raw JSON string from Majsoul replay.

        Returns:
            List of training span dicts.
        """
        if not json_data or not json_data.strip():
            return []

        spans = []
        try:
            record = json.loads(json_data)
            data_list = record.get("data", [])

            states = []
            actions = []

            for entry in data_list:
                name = entry.get("name", "")
                entry_data = entry.get("data", {})

                if name == "RecordNewRound":
                    tiles = entry_data.get("tiles0", [])
                    if tiles:
                        # Convert tile strings to 34-dim vector
                        hand_34 = [0] * 34
                        for t in tiles:
                            try:
                                hand_34[self._tile_str_to_id(t)] += 1
                            except (ValueError, IndexError):
                                pass
                        states.append({"hand": hand_34})
                        actions.append(0)

            if states:
                spans.append(self.build_training_span(states, actions, 0.0))

        except (json.JSONDecodeError, Exception) as e:
            logger.warning("Majsoul JSON parse error: %s", e)

        return spans

    def build_training_span(
        self,
        states: list[Any],
        actions: list[Any],
        reward: float,
    ) -> dict[str, Any]:
        """Build a training span in operatorRL standard format.

        Args:
            states: List of state observations.
            actions: List of action indices.
            reward: Episode reward (will be clipped to [min, max]).

        Returns:
            Dict with 'states', 'actions', 'reward' keys.
        """
        clipped_reward = max(_REWARD_MIN, min(_REWARD_MAX, reward))
        return {
            "states": list(states),
            "actions": list(actions),
            "reward": float(clipped_reward),
        }

    def batch_convert(
        self, records: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Convert a batch of replays.

        Args:
            records: List of dicts with 'format' and 'data' keys.

        Returns:
            Flat list of all training spans.
        """
        all_spans = []
        for rec in records:
            fmt = rec.get("format", "unknown")
            data = rec.get("data", "")
            if fmt == "tenhou":
                all_spans.extend(self.convert_tenhou(data))
            elif fmt == "majsoul":
                all_spans.extend(self.convert_majsoul(data))
            else:
                detected = self.detect_format(data)
                if detected == "tenhou":
                    all_spans.extend(self.convert_tenhou(data))
                elif detected == "majsoul":
                    all_spans.extend(self.convert_majsoul(data))
        return all_spans

    def _tile_str_to_id(self, tile_str: str) -> int:
        suit_offset = {"m": 0, "p": 9, "s": 18, "z": 27}
        num = int(tile_str[:-1])
        suit = tile_str[-1].lower()
        return suit_offset[suit] + (num - 1)

    # ---- Evolution pattern ----

    def _fire_evolution(self, data: dict[str, Any]) -> None:
        if self.evolution_callback is not None:
            self.evolution_callback(_EVOLUTION_KEY, data)
