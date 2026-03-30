"""
Game Client Adapter — Adapts inference output to game client protocol.

Translates abstract model decisions into game-specific client commands.
Each game registers its own protocol adapter. Supports LoL Live Client,
Dota2 console commands, and Mahjong mjai protocol.

Location: agentlightning/deployment/game_client_adapter.py

Reference (拿来主义):
  查看 agentlightning/adapter/base.py 上现有 Adapter[T_from, T_to] 的
  泛型适配器接口, 理解其模式, 特别是 adapt方法如何将一种格式转换为另一种。
  从 extensions/protocol_decoder/src/dual_channel_fuser.py 这个好例子
  开始 — 它展示了多数据源格式统一的融合模式。
  遵循该模式实现 GameClientAdapter, 让推理管线的输出可以直接被
  各游戏客户端消费, 无需下游再做格式转换.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "agentlightning.deployment.game_client_adapter.v1"


class ProtocolAdapter:
    """Single game protocol adapter."""

    __slots__ = ("game", "encode_fn", "decode_fn", "request_count", "error_count")

    def __init__(
        self, game: str,
        encode_fn: Callable[[Dict[str, Any]], Any],
        decode_fn: Optional[Callable[[Any], Dict[str, Any]]] = None,
    ) -> None:
        self.game = game
        self.encode_fn = encode_fn
        self.decode_fn = decode_fn
        self.request_count: int = 0
        self.error_count: int = 0

    def encode(self, decision: Dict[str, Any]) -> Any:
        self.request_count += 1
        try:
            return self.encode_fn(decision)
        except Exception as exc:
            self.error_count += 1
            raise

    def decode(self, raw: Any) -> Dict[str, Any]:
        if self.decode_fn is None:
            raise NotImplementedError(f"No decoder for {self.game}")
        return self.decode_fn(raw)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "game": self.game,
            "request_count": self.request_count,
            "error_count": self.error_count,
            "has_decoder": self.decode_fn is not None,
        }


class GameClientAdapter:
    """Adapts inference output to game client protocols.

    Attributes:
        evolution_callback: Optional callback for evolution events.
    """

    def __init__(self) -> None:
        self._adapters: Dict[str, ProtocolAdapter] = {}
        self._stats = {"total_encoded": 0, "total_decoded": 0, "total_errors": 0}
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def register_adapter(
        self, game: str,
        encode_fn: Callable[[Dict[str, Any]], Any],
        decode_fn: Optional[Callable[[Any], Dict[str, Any]]] = None,
    ) -> None:
        self._adapters[game] = ProtocolAdapter(game, encode_fn, decode_fn)

    def encode(self, game: str, decision: Dict[str, Any]) -> Any:
        """Encode a decision for game client.

        Args:
            game: Game identifier.
            decision: Abstract decision dict.

        Returns:
            Game-specific encoded command.
        """
        adapter = self._adapters.get(game)
        if adapter is None:
            raise KeyError(f"No adapter for game '{game}'")
        result = adapter.encode(decision)
        self._stats["total_encoded"] += 1
        return result

    def decode(self, game: str, raw: Any) -> Dict[str, Any]:
        """Decode a game client message.

        Args:
            game: Game identifier.
            raw: Raw game client data.

        Returns:
            Standardized state dict.
        """
        adapter = self._adapters.get(game)
        if adapter is None:
            raise KeyError(f"No adapter for game '{game}'")
        result = adapter.decode(raw)
        self._stats["total_decoded"] += 1
        return result

    def encode_batch(
        self, game: str, decisions: List[Dict[str, Any]]
    ) -> List[Any]:
        return [self.encode(game, d) for d in decisions]

    def supported_games(self) -> List[str]:
        return list(self._adapters.keys())

    def has_adapter(self, game: str) -> bool:
        return game in self._adapters

    def get_adapter_info(self, game: str) -> Dict[str, Any]:
        if game not in self._adapters:
            raise KeyError(f"No adapter for '{game}'")
        return self._adapters[game].to_dict()

    def list_adapters(self) -> List[Dict[str, Any]]:
        return [a.to_dict() for a in self._adapters.values()]

    def get_stats(self) -> Dict[str, Any]:
        return dict(self._stats)

    def _fire_evolution(self, event_type: str, payload: Dict[str, Any]) -> None:
        if self.evolution_callback is not None:
            try:
                self.evolution_callback({
                    "source": _EVOLUTION_KEY, "type": event_type,
                    "timestamp": time.time(), "payload": payload,
                })
            except Exception as exc:
                logger.warning("Evolution callback error: %s", exc)
