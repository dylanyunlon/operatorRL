"""
CMVK - Cross-Model Verification Kernel

A mathematical and adversarial verification library for calculating
drift/hallucination scores between outputs.

Layer 1: The Primitive
Publication Target: PyPI (pip install cmvk)

This library provides pure functions for verification:
- verify(output_a, output_b) -> VerificationScore
- verify_embeddings(embedding_a, embedding_b) -> VerificationScore
- verify_distributions(dist_a, dist_b) -> VerificationScore
- verify_sequences(seq_a, seq_b) -> VerificationScore

All functions are pure (no side effects) and use only numpy/scipy.
"""

__version__ = "0.1.0"

from .verification import (
    DriftType,
    VerificationScore,
    aggregate_scores,
    verify,
    verify_batch,
    verify_distributions,
    verify_embeddings,
    verify_sequences,
)

__all__ = [
    # Version
    "__version__",
    # Types
    "DriftType",
    "VerificationScore",
    # Core verification functions
    "verify",
    "verify_embeddings",
    "verify_distributions",
    "verify_sequences",
    # Batch operations
    "verify_batch",
    "aggregate_scores",
]
