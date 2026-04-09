"""
cyber.common — Runtime globals and environment (Apollo parity).
================================================================

Apollo reference: ``cyber/common/``

Claude27: New layer — fills structural gap vs Apollo.
Location: lolbot-HyperAI/cyber/common/__init__.py
"""

from cyber.common.global_data import GlobalData
from cyber.common.environment import Environment
from cyber.common.macros import CYBER_RETURN_IF, CYBER_RETURN_VAL_IF

__all__ = [
    "GlobalData",
    "Environment",
    "CYBER_RETURN_IF",
    "CYBER_RETURN_VAL_IF",
]
