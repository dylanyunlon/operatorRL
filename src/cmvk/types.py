"""
CMVK Type Definitions

Core types used throughout the verification library.
All types are immutable where possible for purity.
"""

from .verification import DriftType, VerificationScore

__all__ = ["DriftType", "VerificationScore"]
