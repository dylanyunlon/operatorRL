"""
CMVK - Carbon Market Verification Kernel

Mathematical verification engine for carbon credit claims.
Uses deterministic calculations rather than LLM inference for auditability.

"The AI didn't decide; the Math decided. The AI just managed the workflow."
"""

from .kernel import VerificationKernel, DriftMetric, VerificationResult
from .vectors import ClaimVector, ObservationVector

__all__ = [
    "VerificationKernel",
    "DriftMetric",
    "VerificationResult",
    "ClaimVector",
    "ObservationVector",
]
