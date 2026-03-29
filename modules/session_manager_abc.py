"""
Session Manager ABC — Cross-game unified session lifecycle interface.

Provides abstract base class for all game session managers,
enabling unified transition/reset/duration access across games.

Location: modules/session_manager_abc.py

Reference (拿来主义):
  - integrations/lol/src/lol_agent/game_session_manager.py: state machine
  - modules/game_bridge_abc.py: ABC pattern
  - DI-star/distar/ctools/worker/learner/base_learner.py: lifecycle hooks
"""

from __future__ import annotations

from abc import ABC, abstractmethod

_EVOLUTION_KEY: str = "modules.session_manager_abc.v1"


class SessionManagerABC(ABC):
    """Abstract base class for game session managers.

    All session managers must provide:
    - state: property returning current state
    - transition: move to a new state
    - reset: reset to initial state
    - get_duration: return session duration
    """

    @property
    @abstractmethod
    def state(self) -> str:
        """Current session state string."""
        ...

    @abstractmethod
    def transition(self, new_state: str) -> None:
        """Transition to a new session state.

        Args:
            new_state: Target state.
        """
        ...

    @abstractmethod
    def reset(self) -> None:
        """Reset session to initial state."""
        ...

    @abstractmethod
    def get_duration(self) -> float:
        """Return current session duration in seconds.

        Returns:
            Duration as float.
        """
        ...
