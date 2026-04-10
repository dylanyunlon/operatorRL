"""
modules/canbus — CAN Bus Data Acquisition Module
=================================================

Apollo reference: modules/canbus/

The "CAN bus" of lolbot-HyperAI: periodically polls the LoL Live Client
Data API and Fiddler MCP bridge to acquire raw game data.

Claude30: Added timeout_handler, command_processor, guardian submodule
"""

from modules.canbus.canbus_component import (
    CanbusComponent,
    CanbusConfig,
    ConnectionState,
)
# Claude30: Timeout + command handling
from modules.canbus.timeout_handler import (
    GuardianTimeoutHandler,
    GuardianTimeoutConfig,
    TimeoutRecovery,
    TimeoutState,
)
from modules.canbus.command_processor import (
    CommandProcessor,
    CommandProcessorConfig,
    ControlCommandProcessor,
    ChassisCommandProcessor,
    GuardianCommandProcessor,
    CommandProcessorFactory,
    ControlCommand,
    ChassisCommand,
)

__all__ = [
    # Core component
    "CanbusComponent",
    "CanbusConfig",
    "ConnectionState",
    # Claude30: Timeout handling
    "GuardianTimeoutHandler",
    "GuardianTimeoutConfig",
    "TimeoutRecovery",
    "TimeoutState",
    # Claude30: Command processing
    "CommandProcessor",
    "CommandProcessorConfig",
    "ControlCommandProcessor",
    "ChassisCommandProcessor",
    "GuardianCommandProcessor",
    "CommandProcessorFactory",
    "ControlCommand",
    "ChassisCommand",
]

