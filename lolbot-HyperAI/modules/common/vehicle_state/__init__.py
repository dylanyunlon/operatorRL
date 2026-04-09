"""
common/vehicle_state — Apollo VehicleStateProvider parity.

Apollo reference:
    modules/common/vehicle_state/vehicle_state_provider.h
    modules/common/vehicle_state/vehicle_state_provider.cc

In our domain: GameStateProvider — singleton that holds the latest
authoritative game state snapshot, analogous to Apollo's vehicle state.
"""

from modules.common.vehicle_state.game_state_provider import (
    GameStateProvider,
)

__all__ = ["GameStateProvider"]
