# CMVK - Cross-Model Verification Kernel

[![PyPI version](https://badge.fury.io/py/cmvk.svg)](https://badge.fury.io/py/cmvk)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**Layer 1: The Primitive** — A mathematical and adversarial verification library for calculating drift/hallucination scores between outputs.

## Installation

```bash
pip install cmvk
```

For enhanced statistical functions (recommended):
```bash
pip install cmvk[scipy]
```

## Quick Start

```python
from cmvk import verify

# Compare two outputs
score = verify(
    output_a="def add(a, b): return a + b",
    output_b="def add(x, y): return x + y"
)

print(f"Drift Score: {score.drift_score:.3f}")  # 0.0 = identical, 1.0 = completely different
print(f"Confidence: {score.confidence:.3f}")
print(f"Drift Type: {score.drift_type.value}")
```

## Core Philosophy

CMVK is a **pure verification tool**. It calculates the mathematical drift between two outputs without any side effects:

- ✅ `verify(output_a, output_b) -> VerificationScore`
- ✅ Pure functions with no side effects
- ✅ Minimal dependencies (numpy only, scipy optional)
- ❌ No self-correction loops
- ❌ No agent control plane logic
- ❌ No LLM API calls

**CMVK is the tool used to verify; it is not the loop that triggers the correction.**

## API Reference

### `verify(output_a: str, output_b: str) -> VerificationScore`

Calculate drift/hallucination score between two text outputs.

```python
from cmvk import verify

score = verify(
    "The capital of France is Paris.",
    "Paris is the capital city of France."
)

# Returns VerificationScore with:
# - drift_score: float (0.0 to 1.0)
# - confidence: float (0.0 to 1.0)
# - drift_type: DriftType (SEMANTIC, STRUCTURAL, NUMERICAL, LEXICAL)
# - details: dict with component scores
```

### `verify_embeddings(embedding_a, embedding_b) -> VerificationScore`

Compare pre-computed embedding vectors using cosine distance and euclidean metrics.

```python
from cmvk import verify_embeddings
import numpy as np

emb_a = np.array([0.1, 0.2, 0.3, 0.4])
emb_b = np.array([0.15, 0.25, 0.28, 0.42])

score = verify_embeddings(emb_a, emb_b)
print(f"Semantic drift: {score.drift_score:.3f}")
```

### `verify_distributions(dist_a, dist_b) -> VerificationScore`

Compare probability distributions using KL divergence and Jensen-Shannon divergence.

```python
from cmvk import verify_distributions
import numpy as np

dist_a = np.array([0.2, 0.3, 0.5])
dist_b = np.array([0.25, 0.25, 0.5])

score = verify_distributions(dist_a, dist_b)
print(f"Distribution drift: {score.drift_score:.3f}")
print(f"KL divergence: {score.details['kl_divergence']:.4f}")
```

### `verify_sequences(seq_a, seq_b) -> VerificationScore`

Compare sequences using edit distance and longest common subsequence.

```python
from cmvk import verify_sequences

tokens_a = ["def", "add", "(", "a", ",", "b", ")"]
tokens_b = ["def", "add", "(", "x", ",", "y", ")"]

score = verify_sequences(tokens_a, tokens_b)
print(f"Sequence drift: {score.drift_score:.3f}")
print(f"Edit distance: {score.details['edit_distance']}")
```

### Batch Operations

```python
from cmvk import verify_batch, aggregate_scores

outputs_a = ["output 1", "output 2", "output 3"]
outputs_b = ["output 1 modified", "output 2 changed", "output 3 different"]

scores = verify_batch(outputs_a, outputs_b)
summary = aggregate_scores(scores)

print(f"Mean drift: {summary['mean_drift']:.3f}")
print(f"Std drift: {summary['std_drift']:.3f}")
print(f"Drift distribution: {summary['drift_type_distribution']}")
```

## Drift Types

| Type | Description |
|------|-------------|
| `SEMANTIC` | Meaning/embedding-based differences |
| `STRUCTURAL` | Code structure, indentation, line count |
| `NUMERICAL` | Differences in extracted numbers |
| `LEXICAL` | Word and character-level differences |

## VerificationScore

The `VerificationScore` is an immutable dataclass:

```python
@dataclass(frozen=True)
class VerificationScore:
    drift_score: float    # 0.0 (identical) to 1.0 (completely different)
    confidence: float     # 0.0 to 1.0
    drift_type: DriftType # Primary type of drift detected
    details: dict         # Component scores and metadata
```

## Dependencies

- **Required**: `numpy>=1.24.0`
- **Optional**: `scipy>=1.11.0` (enhanced statistical functions)

## Use Cases

1. **LLM Output Verification**: Compare outputs from different models to detect hallucinations
2. **Hallucination Detection**: Measure drift from ground truth or reference outputs
3. **Regression Testing**: Track output changes across model versions
4. **Adversarial Evaluation**: Quantify semantic preservation after perturbations
5. **Model Comparison**: Systematically compare outputs from different LLM providers

## Example: Detecting Hallucinations

```python
from cmvk import verify, DriftType

ground_truth = "The speed of light is approximately 299,792,458 meters per second."
model_output = "The speed of light is approximately 300 million meters per second."

score = verify(ground_truth, model_output)

if score.drift_score > 0.3:
    print(f"⚠️ Potential hallucination detected!")
    print(f"Drift score: {score.drift_score:.3f}")
    print(f"Primary drift type: {score.drift_type.value}")
else:
    print(f"✅ Output appears consistent (drift: {score.drift_score:.3f})")
```

## License

MIT License - see [LICENSE](LICENSE) for details.
