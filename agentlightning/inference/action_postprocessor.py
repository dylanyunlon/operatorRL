"""
Action Postprocessor — Validate, clamp, and format model actions.

Takes raw model action outputs and transforms them into game-specific
executable commands. Handles action masking (invalid actions), cooldown
enforcement, safety checks, and format conversion.

Location: agentlightning/inference/action_postprocessor.py

Reference (拿来主义):
  查看 dota2bot-OpenHyperAI/BotLib/ 上现有 hero_*.lua 中 Consider*Action
  的动作验证方式, 理解其模式, 特别是 utility分数计算如何与动作合法性
  检查(IsAbilityReady, GetCooldown)分离。
  从 integrations/lol/src/lol_agent/decision_engine.py 这个好例子开始 —
  它将decide输出为strategy dict, 下游负责转换为具体操作。
  遵循该模式实现 ActionPostprocessor, 让动作采样器(M536)输出的原始
  动作可以经过合法性检查、冷却约束、安全过滤后转换为可执行的游戏指令.

Design Notes (Knuth-level critique):
  User:
    - Action masking prevents illegal moves (e.g., flash on CD)
    - Safety filter prevents obviously bad actions (dive fountain)
    - Format conversion makes actions game-client-ready
  System:
    - Validators are registered per-game, zero coupling between games
    - Cooldown tracking is O(1) per action check
    - Rejection stats help diagnose policy training gaps
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "agentlightning.inference.action_postprocessor.v1"


class ActionValidator:
    """Validates whether an action is legal in current game state.

    Attributes:
        name: Validator name.
        check_fn: Callable(action, game_state) → bool.
    """

    __slots__ = ("name", "check_fn", "rejection_count")

    def __init__(
        self,
        name: str,
        check_fn: Callable[[Dict[str, Any], Dict[str, Any]], bool],
    ) -> None:
        self.name = name
        self.check_fn = check_fn
        self.rejection_count: int = 0

    def validate(self, action: Dict[str, Any], game_state: Dict[str, Any]) -> bool:
        """Check if action is valid.

        Returns:
            True if valid, False if rejected.
        """
        result = self.check_fn(action, game_state)
        if not result:
            self.rejection_count += 1
        return result


class CooldownTracker:
    """Tracks action cooldowns.

    Attributes:
        cooldowns: Dict of action_id → ready_at timestamp.
    """

    def __init__(self) -> None:
        self._cooldowns: Dict[str, float] = {}

    def set_cooldown(self, action_id: str, duration: float) -> None:
        """Set cooldown for an action.

        Args:
            action_id: Action identifier.
            duration: Cooldown duration in seconds.
        """
        self._cooldowns[action_id] = time.time() + duration

    def is_ready(self, action_id: str) -> bool:
        """Check if action is off cooldown.

        Args:
            action_id: Action identifier.

        Returns:
            True if ready (no cooldown or expired).
        """
        ready_at = self._cooldowns.get(action_id, 0.0)
        return time.time() >= ready_at

    def remaining(self, action_id: str) -> float:
        """Get remaining cooldown in seconds.

        Args:
            action_id: Action identifier.

        Returns:
            Remaining seconds, or 0.0 if ready.
        """
        ready_at = self._cooldowns.get(action_id, 0.0)
        return max(0.0, ready_at - time.time())

    def clear(self) -> None:
        """Clear all cooldowns."""
        self._cooldowns.clear()

    def active_cooldowns(self) -> Dict[str, float]:
        """Get all active cooldowns with remaining time."""
        now = time.time()
        return {
            k: max(0.0, v - now)
            for k, v in self._cooldowns.items()
            if v > now
        }


class ActionPostprocessor:
    """Postprocesses model action outputs for game execution.

    Applies validation, cooldown checks, safety filters, and
    format conversion to raw model actions.

    Attributes:
        evolution_callback: Optional callback for evolution events.
    """

    def __init__(self) -> None:
        self._validators: List[ActionValidator] = []
        self._cooldown_tracker = CooldownTracker()
        self._safety_rules: List[Callable[[Dict[str, Any], Dict[str, Any]], bool]] = []
        self._formatters: Dict[str, Callable[[Dict[str, Any]], Dict[str, Any]]] = {}
        self._fallback_action: Optional[Dict[str, Any]] = None
        self._stats = {
            "total_processed": 0,
            "total_rejected": 0,
            "total_fallbacks": 0,
            "total_formatted": 0,
        }
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    # --- Registration ---

    def add_validator(
        self,
        name: str,
        check_fn: Callable[[Dict[str, Any], Dict[str, Any]], bool],
    ) -> None:
        """Register an action validator.

        Args:
            name: Validator name.
            check_fn: Callable(action, game_state) → bool.
        """
        self._validators.append(ActionValidator(name, check_fn))

    def add_safety_rule(
        self,
        rule_fn: Callable[[Dict[str, Any], Dict[str, Any]], bool],
    ) -> None:
        """Register a safety rule.

        Args:
            rule_fn: Callable(action, game_state) → True if safe.
        """
        self._safety_rules.append(rule_fn)

    def register_formatter(
        self,
        game: str,
        formatter: Callable[[Dict[str, Any]], Dict[str, Any]],
    ) -> None:
        """Register a game-specific action formatter.

        Args:
            game: Game identifier.
            formatter: Callable(action) → formatted_action.
        """
        self._formatters[game] = formatter

    def set_fallback_action(self, action: Dict[str, Any]) -> None:
        """Set fallback action when all candidates rejected.

        Args:
            action: Default safe action dict.
        """
        self._fallback_action = action

    # --- Processing ---

    def process(
        self,
        action: Dict[str, Any],
        game_state: Dict[str, Any],
        game: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Postprocess a single action.

        Runs validation, cooldown check, safety filter, and formatting.

        Args:
            action: Raw model action output.
            game_state: Current game state.
            game: Game identifier for formatting.

        Returns:
            Postprocessed action dict, or fallback action.
        """
        self._stats["total_processed"] += 1

        # Validation
        for validator in self._validators:
            if not validator.validate(action, game_state):
                self._stats["total_rejected"] += 1
                logger.debug("Action rejected by validator: %s", validator.name)
                return self._get_fallback(action)

        # Cooldown check
        action_id = action.get("action_id", action.get("type", ""))
        if action_id and not self._cooldown_tracker.is_ready(action_id):
            self._stats["total_rejected"] += 1
            logger.debug("Action on cooldown: %s", action_id)
            return self._get_fallback(action)

        # Safety rules
        for rule in self._safety_rules:
            if not rule(action, game_state):
                self._stats["total_rejected"] += 1
                logger.debug("Action rejected by safety rule")
                return self._get_fallback(action)

        # Clamp numerical values
        action = self._clamp_values(action)

        # Format for game
        if game and game in self._formatters:
            try:
                action = self._formatters[game](action)
                self._stats["total_formatted"] += 1
            except Exception as exc:
                logger.warning("Formatter error for %s: %s", game, exc)

        return action

    def process_batch(
        self,
        actions: List[Dict[str, Any]],
        game_states: List[Dict[str, Any]],
        game: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Postprocess a batch of actions.

        Args:
            actions: List of raw actions.
            game_states: Corresponding game states.
            game: Game identifier.

        Returns:
            List of postprocessed actions.
        """
        results: List[Dict[str, Any]] = []
        for action, state in zip(actions, game_states):
            results.append(self.process(action, state, game=game))
        return results

    def apply_action_mask(
        self,
        action_logits: List[float],
        valid_mask: List[bool],
    ) -> List[float]:
        """Apply action mask to logits.

        Sets invalid action logits to large negative value.

        Args:
            action_logits: Raw logit values.
            valid_mask: Boolean mask (True = valid).

        Returns:
            Masked logits list.
        """
        if len(action_logits) != len(valid_mask):
            return action_logits
        masked = []
        for logit, valid in zip(action_logits, valid_mask):
            masked.append(logit if valid else -1e9)
        return masked

    # --- Cooldown Management ---

    def set_cooldown(self, action_id: str, duration: float) -> None:
        """Set cooldown for an action.

        Args:
            action_id: Action identifier.
            duration: Duration in seconds.
        """
        self._cooldown_tracker.set_cooldown(action_id, duration)

    def is_action_ready(self, action_id: str) -> bool:
        """Check if an action is off cooldown."""
        return self._cooldown_tracker.is_ready(action_id)

    def get_active_cooldowns(self) -> Dict[str, float]:
        """Get all active cooldowns."""
        return self._cooldown_tracker.active_cooldowns()

    # --- Stats ---

    def get_stats(self) -> Dict[str, Any]:
        """Get postprocessor statistics."""
        validator_stats = {
            v.name: v.rejection_count for v in self._validators
        }
        return {
            **self._stats,
            "validator_rejections": validator_stats,
            "validator_count": len(self._validators),
            "safety_rule_count": len(self._safety_rules),
            "formatter_count": len(self._formatters),
            "rejection_rate": (
                self._stats["total_rejected"] /
                max(self._stats["total_processed"], 1)
            ),
        }

    def reset_stats(self) -> None:
        """Reset statistics."""
        self._stats = {
            "total_processed": 0,
            "total_rejected": 0,
            "total_fallbacks": 0,
            "total_formatted": 0,
        }
        for v in self._validators:
            v.rejection_count = 0

    # --- Internal ---

    def _get_fallback(self, original_action: Dict[str, Any]) -> Dict[str, Any]:
        """Return fallback action."""
        self._stats["total_fallbacks"] += 1
        if self._fallback_action is not None:
            return dict(self._fallback_action)
        return {"type": "noop", "original": original_action.get("type", "unknown")}

    def _clamp_values(self, action: Dict[str, Any]) -> Dict[str, Any]:
        """Clamp numerical action values to reasonable ranges."""
        result = dict(action)
        for key, val in result.items():
            if isinstance(val, float):
                if val != val:  # NaN check
                    result[key] = 0.0
                elif abs(val) > 1e6:
                    result[key] = max(-1e6, min(1e6, val))
        return result

    def _fire_evolution(self, event_type: str, payload: Dict[str, Any]) -> None:
        if self.evolution_callback is not None:
            try:
                self.evolution_callback({
                    "source": _EVOLUTION_KEY,
                    "type": event_type,
                    "timestamp": time.time(),
                    "payload": payload,
                })
            except Exception as exc:
                logger.warning("Evolution callback error: %s", exc)
