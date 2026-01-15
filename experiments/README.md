# Data Contracts and Laziness Benchmark

This directory contains the implementation of the **data contracts (schemas)** that enforce strict typing between the Auditor and Patcher components, plus a comprehensive **laziness benchmark** to validate the system's ability to detect agent laziness.

## Problem Statement

**"If your Auditor cannot strictly talk to your Patcher, the system breaks."**

The self-correcting agent kernel requires rigorous data contracts to ensure that the Auditor and Patcher can communicate effectively. Without these contracts, the system cannot:
- Export structured logs for RLAIF fine-tuning
- Guarantee type safety across component boundaries
- Maintain a consistent "spine" of data flow

## Implementation

### 1. Data Contracts (The "Spine")

**File:** `src/kernel/schemas.py`

Defines three core Pydantic models:

#### `Lesson` - The Atomic Unit of Learning
```python
class Lesson(BaseModel):
    id: str
    trigger_pattern: str  # Context that triggered the failure
    rule_text: str        # Instruction to add to System Prompt
    lesson_type: Literal["syntax", "business", "security"]
    confidence_score: float  # Teacher's confidence (0.0-1.0)
    created_at: datetime
```

**Purpose:** Represents a single, specific piece of knowledge learned from a failure.

#### `FailureTrace` - The Evidence
```python
class FailureTrace(BaseModel):
    trace_id: str
    user_prompt: str
    agent_reasoning: str
    tool_call: Optional[Dict[str, Any]]
    tool_output: Optional[str]
    failure_type: Literal["omission_laziness", "commission_safety", "hallucination"]
    severity: Literal["critical", "non_critical"]
    timestamp: datetime
```

**Purpose:** Captures complete evidence of what went wrong.

#### `PatchRequest` - The Prescription
```python
class PatchRequest(BaseModel):
    trace_id: str
    diagnosis: str
    proposed_lesson: Lesson
    apply_strategy: Literal["hotfix_now", "batch_later"]
    context: Dict[str, Any]
```

**Purpose:** Combines failure evidence with the fix, specifying when to apply it (sync vs async).

### 2. Laziness Benchmark

**File:** `experiments/laziness_benchmark.py`

A stress test that validates the CompletenessAuditor's ability to detect laziness.

#### Test Cases

1. **Ambiguous Query** - "Find the Q3 report"
   - Agent: "No exact matches"
   - Expected: LAZY (should try "Quarter 3")

2. **Clear Answer** - "Who is the CEO?"
   - Agent: "Satya Nadella"
   - Expected: COMPETENT

3. **Permission Error** - "Check the logs"
   - Agent: "Cannot access directory"
   - Expected: LAZY (should try alternatives)

4. **Empty Result** - "Find error 500"
   - Agent: "No matches"
   - Expected: LAZY (should check archived logs)

5. **Successful Query** - "List projects"
   - Agent: "Found 47 projects"
   - Expected: COMPETENT

6. **Archived Data** - "Find Project_Alpha"
   - Agent: "Not in current registry"
   - Expected: LAZY (should check archived)

#### Results

```
Final Score: 6/6 (100.0%)
🎉 PERFECT SCORE! The auditor correctly identified all lazy vs competent responses.
```

### 3. Integration Test

**File:** `experiments/test_auditor_patcher_integration.py`

Validates the complete flow:
1. **Schema Creation** - Verify schemas can be created and validated
2. **Auditor-Patcher Flow** - Test the complete pipeline:
   - Auditor detects laziness
   - Create structured schemas from audit result
   - Patcher applies the patch
   - Verify patch was applied

#### Results

```
✅ PASS: Schema Creation
✅ PASS: Auditor-Patcher Flow

🎉 ALL TESTS PASSED!
The Auditor and Patcher can communicate using the data contracts.
```

## Usage

### Run the Laziness Benchmark

```bash
python experiments/laziness_benchmark.py
```

### Run the Integration Test

```bash
python experiments/test_auditor_patcher_integration.py
```

### Use Schemas in Code

```python
from src.kernel.schemas import Lesson, FailureTrace, PatchRequest

# Create a lesson
lesson = Lesson(
    trigger_pattern="search logs, empty result",
    rule_text="Always check archived partitions if recent logs are empty",
    lesson_type="business",
    confidence_score=0.92
)

# Create a failure trace
trace = FailureTrace(
    user_prompt="Find error 500",
    agent_reasoning="No matches found",
    tool_output="[]",
    failure_type="omission_laziness",
    severity="non_critical"
)

# Create a patch request
patch_request = PatchRequest(
    trace_id=trace.trace_id,
    diagnosis="Agent gave up without checking archived logs",
    proposed_lesson=lesson,
    apply_strategy="batch_later"
)
```

## Benefits

### Type Safety
- All data exchange uses Pydantic models
- Invalid data is caught at runtime
- IDE autocomplete and type checking

### RLAIF Export
- Schemas can be directly exported to JSON
- Perfect for fine-tuning datasets
- Structured logs for analysis

### Clear Contracts
- Auditor knows exactly what to send Patcher
- Patcher knows exactly what to expect
- No ambiguity in data flow

### Scale by Subtraction
- Lessons have lifecycle metadata
- Can purge syntax lessons on model upgrades
- Maintain only necessary business knowledge

## Architecture

```
┌──────────────┐
│   Auditor    │  Detects laziness
│ (Competeness)│  Generates gap analysis
└──────┬───────┘
       │
       │ Creates
       │
       ▼
┌──────────────┐
│ FailureTrace │  Evidence: what went wrong
└──────┬───────┘
       │
       │ Combines with
       │
       ▼
┌──────────────┐
│    Lesson    │  Knowledge: what to learn
└──────┬───────┘
       │
       │ Forms
       │
       ▼
┌──────────────┐
│ PatchRequest │  Prescription: how to fix
└──────┬───────┘
       │
       │ Sent to
       │
       ▼
┌──────────────┐
│   Patcher    │  Applies the fix
│ (Optimizer)  │  Updates agent
└──────────────┘
```

## Key Insights

### 1. Dual-Loop Architecture Support
- **Loop 1 (Runtime)**: `apply_strategy="hotfix_now"` for critical fixes
- **Loop 2 (Offline)**: `apply_strategy="batch_later"` for non-critical fixes

### 2. Lesson Types Map to Decay
- `lesson_type="syntax"` → Type A (High decay, purge on upgrade)
- `lesson_type="business"` → Type B (Zero decay, permanent)
- `lesson_type="security"` → Type B (Zero decay, permanent)

### 3. Differential Auditing
- Only audit "give-up signals" (5-10% of requests)
- Not every interaction (too expensive)
- Focused on lazy/omission failures

## Next Steps

1. **Export to RLAIF** - Use schemas to export training data
2. **Integrate with Triage** - Use `apply_strategy` for sync/async routing
3. **Semantic Purge** - Classify lessons by type for lifecycle management
4. **Expand Benchmarks** - Add more test cases for hallucinations and safety

## Files Created

- `src/kernel/schemas.py` - Data contracts (Lesson, FailureTrace, PatchRequest)
- `src/mocks/__init__.py` - MockAgent for testing
- `experiments/laziness_benchmark.py` - Laziness detection stress test
- `experiments/test_auditor_patcher_integration.py` - Integration test
- `experiments/README.md` - This file

## Performance

- **Laziness Detection**: 100% accuracy on 6 test cases
- **Integration Tests**: All passing
- **Existing Tests**: All kernel tests still passing
- **Type Safety**: Full Pydantic validation

---

**Status:** ✅ Complete and tested

**Accuracy:** 🎯 100% on laziness benchmark

**Integration:** ✅ Auditor-Patcher communication verified
