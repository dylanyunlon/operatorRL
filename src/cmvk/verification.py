"""
CMVK Verification Module - Pure Mathematical Functions

This module provides pure functions for calculating drift/hallucination scores
between two outputs. These functions have no side effects and use only
numpy/scipy for mathematical operations.

Layer 1: The Primitive - Mathematical and adversarial verification.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum

import numpy as np
from numpy.typing import ArrayLike

try:
    from scipy import spatial, stats

    HAS_SCIPY = True
except ImportError:
    HAS_SCIPY = False


class DriftType(Enum):
    """Types of drift/divergence detected between outputs."""

    SEMANTIC = "semantic"
    STRUCTURAL = "structural"
    NUMERICAL = "numerical"
    LEXICAL = "lexical"


@dataclass(frozen=True)
class VerificationScore:
    """
    Immutable result of verification between two outputs.

    Attributes:
        drift_score: Overall drift score between 0.0 (identical) and 1.0 (completely different)
        confidence: Confidence in the score (0.0 to 1.0)
        drift_type: Primary type of drift detected
        details: Dictionary with component scores
    """

    drift_score: float
    confidence: float
    drift_type: DriftType
    details: dict


def verify(output_a: str, output_b: str) -> VerificationScore:
    """
    Calculate drift/hallucination score between two outputs.

    This is the primary verification function - a pure function with no side effects.
    Takes two outputs and returns a score indicating their divergence.

    Args:
        output_a: First output (typically from model A / generator)
        output_b: Second output (typically from model B / verifier)

    Returns:
        VerificationScore with drift score, confidence, and details

    Example:
        >>> score = verify("def add(a, b): return a + b", "def add(x, y): return x + y")
        >>> score.drift_score  # Low score - semantically similar
        0.15
    """
    if not output_a and not output_b:
        return VerificationScore(
            drift_score=0.0,
            confidence=1.0,
            drift_type=DriftType.LEXICAL,
            details={"reason": "both_empty"},
        )

    if not output_a or not output_b:
        return VerificationScore(
            drift_score=1.0,
            confidence=1.0,
            drift_type=DriftType.STRUCTURAL,
            details={"reason": "one_empty"},
        )

    # Calculate multiple drift components
    lexical_drift = _lexical_drift(output_a, output_b)
    structural_drift = _structural_drift(output_a, output_b)
    numerical_drift = _numerical_drift(output_a, output_b)

    # Weighted combination
    weights = {"lexical": 0.3, "structural": 0.4, "numerical": 0.3}

    combined_drift = (
        weights["lexical"] * lexical_drift["score"]
        + weights["structural"] * structural_drift["score"]
        + weights["numerical"] * numerical_drift["score"]
    )

    # Determine primary drift type
    scores = {
        DriftType.LEXICAL: lexical_drift["score"],
        DriftType.STRUCTURAL: structural_drift["score"],
        DriftType.NUMERICAL: numerical_drift["score"],
    }
    primary_drift = max(scores, key=lambda k: scores[k])

    # Calculate confidence based on agreement between methods
    score_values = list(scores.values())
    confidence = 1.0 - np.std(score_values) if len(score_values) > 1 else 0.8

    return VerificationScore(
        drift_score=float(np.clip(combined_drift, 0.0, 1.0)),
        confidence=float(np.clip(confidence, 0.0, 1.0)),
        drift_type=primary_drift,
        details={
            "lexical": lexical_drift,
            "structural": structural_drift,
            "numerical": numerical_drift,
            "weights": weights,
        },
    )


def verify_embeddings(embedding_a: ArrayLike, embedding_b: ArrayLike) -> VerificationScore:
    """
    Calculate drift score between two embedding vectors.

    Pure function for comparing pre-computed embeddings.

    Args:
        embedding_a: Embedding vector for output A
        embedding_b: Embedding vector for output B

    Returns:
        VerificationScore with cosine-distance based drift score
    """
    vec_a = np.asarray(embedding_a, dtype=np.float64)
    vec_b = np.asarray(embedding_b, dtype=np.float64)

    if vec_a.shape != vec_b.shape:
        return VerificationScore(
            drift_score=1.0,
            confidence=0.5,
            drift_type=DriftType.STRUCTURAL,
            details={"reason": "shape_mismatch", "shape_a": vec_a.shape, "shape_b": vec_b.shape},
        )

    # Cosine distance (0 = identical, 1 = orthogonal, 2 = opposite)
    if HAS_SCIPY:
        cosine_dist = spatial.distance.cosine(vec_a, vec_b)
    else:
        # Fallback implementation
        dot = np.dot(vec_a, vec_b)
        norm_a = np.linalg.norm(vec_a)
        norm_b = np.linalg.norm(vec_b)
        cosine_dist = 1.0 if norm_a == 0 or norm_b == 0 else 1.0 - (dot / (norm_a * norm_b))

    # Euclidean distance (normalized)
    euclidean_dist = np.linalg.norm(vec_a - vec_b)
    max_dist = np.sqrt(len(vec_a) * 4)  # Assuming normalized embeddings in [-1, 1]
    normalized_euclidean = min(euclidean_dist / max_dist, 1.0)

    # Combine metrics
    drift_score = 0.6 * cosine_dist + 0.4 * normalized_euclidean

    return VerificationScore(
        drift_score=float(np.clip(drift_score, 0.0, 1.0)),
        confidence=0.95,
        drift_type=DriftType.SEMANTIC,
        details={
            "cosine_distance": float(cosine_dist),
            "euclidean_distance": float(euclidean_dist),
            "normalized_euclidean": float(normalized_euclidean),
        },
    )


def verify_distributions(dist_a: ArrayLike, dist_b: ArrayLike) -> VerificationScore:
    """
    Calculate drift between two probability distributions.

    Uses KL divergence and other statistical measures to compare distributions.

    Args:
        dist_a: First probability distribution
        dist_b: Second probability distribution

    Returns:
        VerificationScore with distribution-based drift score
    """
    p = np.asarray(dist_a, dtype=np.float64)
    q = np.asarray(dist_b, dtype=np.float64)

    # Normalize to valid probability distributions
    p = p / (p.sum() + 1e-10)
    q = q / (q.sum() + 1e-10)

    # Add small epsilon to avoid log(0)
    eps = 1e-10
    p = np.clip(p, eps, 1.0)
    q = np.clip(q, eps, 1.0)

    if HAS_SCIPY:
        # KL divergence
        kl_div = stats.entropy(p, q)
        # Jensen-Shannon divergence (symmetric, bounded [0, 1])
        m = 0.5 * (p + q)
        js_div = 0.5 * stats.entropy(p, m) + 0.5 * stats.entropy(q, m)
    else:
        # Fallback implementations
        kl_div = float(np.sum(p * np.log(p / q)))
        m = 0.5 * (p + q)
        js_div = 0.5 * np.sum(p * np.log(p / m)) + 0.5 * np.sum(q * np.log(q / m))

    # Total variation distance
    tv_dist = 0.5 * np.sum(np.abs(p - q))

    # Combined drift (JS divergence is bounded [0, ln(2)])
    drift_score = js_div / np.log(2)  # Normalize to [0, 1]

    return VerificationScore(
        drift_score=float(np.clip(drift_score, 0.0, 1.0)),
        confidence=0.9,
        drift_type=DriftType.NUMERICAL,
        details={
            "kl_divergence": float(kl_div),
            "js_divergence": float(js_div),
            "total_variation": float(tv_dist),
        },
    )


def verify_sequences(seq_a: Sequence[str], seq_b: Sequence[str]) -> VerificationScore:
    """
    Calculate drift between two sequences of tokens/items.

    Uses edit distance and sequence alignment metrics.

    Args:
        seq_a: First sequence
        seq_b: Second sequence

    Returns:
        VerificationScore with sequence-based drift score
    """
    if not seq_a and not seq_b:
        return VerificationScore(
            drift_score=0.0,
            confidence=1.0,
            drift_type=DriftType.LEXICAL,
            details={"reason": "both_empty"},
        )

    # Levenshtein distance
    edit_dist = _levenshtein_distance(seq_a, seq_b)
    max_len = max(len(seq_a), len(seq_b))
    normalized_edit = edit_dist / max_len if max_len > 0 else 0.0

    # Jaccard similarity (set-based)
    set_a = set(seq_a)
    set_b = set(seq_b)
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    jaccard = intersection / union if union > 0 else 1.0
    jaccard_drift = 1.0 - jaccard

    # Order-aware similarity (longest common subsequence)
    lcs_len = _lcs_length(seq_a, seq_b)
    lcs_ratio = 2 * lcs_len / (len(seq_a) + len(seq_b)) if (len(seq_a) + len(seq_b)) > 0 else 1.0
    lcs_drift = 1.0 - lcs_ratio

    # Combined
    drift_score = 0.4 * normalized_edit + 0.3 * jaccard_drift + 0.3 * lcs_drift

    return VerificationScore(
        drift_score=float(np.clip(drift_score, 0.0, 1.0)),
        confidence=0.85,
        drift_type=DriftType.STRUCTURAL,
        details={
            "edit_distance": edit_dist,
            "normalized_edit": float(normalized_edit),
            "jaccard_similarity": float(jaccard),
            "lcs_ratio": float(lcs_ratio),
        },
    )


# ============================================================================
# Internal pure functions
# ============================================================================


def _lexical_drift(text_a: str, text_b: str) -> dict:
    """
    Calculate lexical drift between two texts.

    Pure function - no side effects.
    """
    # Character-level comparison
    chars_a = set(text_a)
    chars_b = set(text_b)
    char_jaccard = len(chars_a & chars_b) / len(chars_a | chars_b) if (chars_a | chars_b) else 1.0

    # Word-level comparison
    words_a = set(text_a.split())
    words_b = set(text_b.split())
    word_jaccard = len(words_a & words_b) / len(words_a | words_b) if (words_a | words_b) else 1.0

    # Length ratio
    len_a, len_b = len(text_a), len(text_b)
    length_ratio = min(len_a, len_b) / max(len_a, len_b) if max(len_a, len_b) > 0 else 1.0

    # Combined score (lower similarity = higher drift)
    similarity = 0.3 * char_jaccard + 0.5 * word_jaccard + 0.2 * length_ratio
    drift = 1.0 - similarity

    return {
        "score": drift,
        "char_jaccard": char_jaccard,
        "word_jaccard": word_jaccard,
        "length_ratio": length_ratio,
    }


def _structural_drift(text_a: str, text_b: str) -> dict:
    """
    Calculate structural drift between two texts.

    Analyzes structure like line count, indentation, code patterns.
    Pure function - no side effects.
    """
    lines_a = text_a.split("\n")
    lines_b = text_b.split("\n")

    # Line count difference
    line_count_a, line_count_b = len(lines_a), len(lines_b)
    line_ratio = (
        min(line_count_a, line_count_b) / max(line_count_a, line_count_b)
        if max(line_count_a, line_count_b) > 0
        else 1.0
    )

    # Indentation pattern
    indent_a = [len(line) - len(line.lstrip()) for line in lines_a if line.strip()]
    indent_b = [len(line) - len(line.lstrip()) for line in lines_b if line.strip()]

    if indent_a and indent_b:
        avg_indent_a = np.mean(indent_a)
        avg_indent_b = np.mean(indent_b)
        max_indent = max(avg_indent_a, avg_indent_b, 1)
        indent_similarity = 1.0 - abs(avg_indent_a - avg_indent_b) / max_indent
    else:
        indent_similarity = 1.0 if (not indent_a and not indent_b) else 0.5

    # Code pattern markers (for code comparison)
    patterns = ["def ", "class ", "import ", "return ", "if ", "for ", "while ", "try:", "except"]
    pattern_a = {p for p in patterns if p in text_a}
    pattern_b = {p for p in patterns if p in text_b}
    pattern_jaccard = (
        len(pattern_a & pattern_b) / len(pattern_a | pattern_b) if (pattern_a | pattern_b) else 1.0
    )

    # Combined
    similarity = 0.3 * line_ratio + 0.3 * indent_similarity + 0.4 * pattern_jaccard
    drift = 1.0 - similarity

    return {
        "score": drift,
        "line_ratio": line_ratio,
        "indent_similarity": indent_similarity,
        "pattern_jaccard": pattern_jaccard,
    }


def _numerical_drift(text_a: str, text_b: str) -> dict:
    """
    Calculate numerical drift by extracting and comparing numbers.

    Pure function - no side effects.
    """
    import re

    # Extract numbers from both texts
    number_pattern = r"-?\d+\.?\d*"
    numbers_a = [float(n) for n in re.findall(number_pattern, text_a)]
    numbers_b = [float(n) for n in re.findall(number_pattern, text_b)]

    if not numbers_a and not numbers_b:
        return {"score": 0.0, "reason": "no_numbers"}

    if not numbers_a or not numbers_b:
        return {"score": 0.5, "reason": "numbers_only_in_one"}

    # Compare statistics
    mean_a, mean_b = np.mean(numbers_a), np.mean(numbers_b)
    std_a, std_b = np.std(numbers_a), np.std(numbers_b)

    # Relative difference in means
    max_mean = max(abs(mean_a), abs(mean_b), 1e-10)
    mean_diff = abs(mean_a - mean_b) / max_mean

    # Relative difference in stds
    max_std = max(std_a, std_b, 1e-10)
    std_diff = abs(std_a - std_b) / max_std if max_std > 1e-10 else 0.0

    # Count difference
    count_ratio = min(len(numbers_a), len(numbers_b)) / max(len(numbers_a), len(numbers_b))

    # Combined
    drift = 0.4 * min(mean_diff, 1.0) + 0.3 * min(std_diff, 1.0) + 0.3 * (1.0 - count_ratio)

    return {
        "score": drift,
        "mean_a": mean_a,
        "mean_b": mean_b,
        "std_a": std_a,
        "std_b": std_b,
        "count_a": len(numbers_a),
        "count_b": len(numbers_b),
    }


def _levenshtein_distance(seq_a: Sequence, seq_b: Sequence) -> int:
    """
    Calculate Levenshtein edit distance between two sequences.

    Pure function using dynamic programming.
    """
    m, n = len(seq_a), len(seq_b)

    if m == 0:
        return n
    if n == 0:
        return m

    # Use numpy for efficiency
    dp = np.zeros((m + 1, n + 1), dtype=np.int32)
    dp[:, 0] = np.arange(m + 1)
    dp[0, :] = np.arange(n + 1)

    for i in range(1, m + 1):
        for j in range(1, n + 1):
            cost = 0 if seq_a[i - 1] == seq_b[j - 1] else 1
            dp[i, j] = min(
                dp[i - 1, j] + 1,  # deletion
                dp[i, j - 1] + 1,  # insertion
                dp[i - 1, j - 1] + cost,  # substitution
            )

    return int(dp[m, n])


def _lcs_length(seq_a: Sequence, seq_b: Sequence) -> int:
    """
    Calculate length of Longest Common Subsequence.

    Pure function using dynamic programming.
    """
    m, n = len(seq_a), len(seq_b)

    if m == 0 or n == 0:
        return 0

    dp = np.zeros((m + 1, n + 1), dtype=np.int32)

    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if seq_a[i - 1] == seq_b[j - 1]:
                dp[i, j] = dp[i - 1, j - 1] + 1
            else:
                dp[i, j] = max(dp[i - 1, j], dp[i, j - 1])

    return int(dp[m, n])


# ============================================================================
# Batch verification functions
# ============================================================================


def verify_batch(outputs_a: Sequence[str], outputs_b: Sequence[str]) -> list[VerificationScore]:
    """
    Verify multiple output pairs.

    Pure function that processes pairs in sequence.

    Args:
        outputs_a: Sequence of outputs from source A
        outputs_b: Sequence of outputs from source B (same length as outputs_a)

    Returns:
        List of VerificationScore for each pair
    """
    if len(outputs_a) != len(outputs_b):
        raise ValueError(
            f"Length mismatch: outputs_a has {len(outputs_a)} items, "
            f"outputs_b has {len(outputs_b)} items"
        )

    return [verify(a, b) for a, b in zip(outputs_a, outputs_b, strict=False)]


def aggregate_scores(scores: Sequence[VerificationScore]) -> dict:
    """
    Aggregate multiple verification scores into summary statistics.

    Pure function.

    Args:
        scores: Sequence of VerificationScore objects

    Returns:
        Dictionary with aggregate statistics
    """
    if not scores:
        return {"count": 0}

    drift_values = [s.drift_score for s in scores]
    confidence_values = [s.confidence for s in scores]

    drift_types: dict[str, int] = {}
    for s in scores:
        drift_types[s.drift_type.value] = drift_types.get(s.drift_type.value, 0) + 1

    return {
        "count": len(scores),
        "mean_drift": float(np.mean(drift_values)),
        "std_drift": float(np.std(drift_values)),
        "min_drift": float(np.min(drift_values)),
        "max_drift": float(np.max(drift_values)),
        "median_drift": float(np.median(drift_values)),
        "mean_confidence": float(np.mean(confidence_values)),
        "drift_type_distribution": drift_types,
    }
