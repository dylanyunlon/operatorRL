"""
Data contracts (schemas) for self-correcting agent kernel.

This module defines the rigorous data contracts between Auditor and Patcher.
These schemas use Pydantic to enforce type safety and can be exported
directly into Fine-Tuning datasets (RLAIF).

The "Spine" of the self-correcting system:
1. Lesson - The atomic unit of learning (what we learned)
2. FailureTrace - The evidence (what happened)
3. PatchRequest - The prescription (how to fix it)
"""

from pydantic import BaseModel, Field
from typing import List, Optional, Literal, Dict, Any
from datetime import datetime
from uuid import uuid4


# 1. The Atomic Lesson (What we learned)
class Lesson(BaseModel):
    """
    An atomic lesson learned from a failure.
    
    This represents a single, specific piece of knowledge that should
    be added to the agent's system prompt or memory to prevent future failures.
    """
    id: str = Field(default_factory=lambda: str(uuid4()))
    trigger_pattern: str = Field(..., description="The context/keywords that triggered this failure")
    rule_text: str = Field(..., description="The actual instruction to add to System Prompt")
    lesson_type: Literal["syntax", "business", "security"] = Field(
        ...,
        description="Type of lesson: syntax (model capability), business (domain knowledge), security (safety rule)"
    )
    confidence_score: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Teacher's confidence in this fix (0.0-1.0)"
    )
    created_at: datetime = Field(default_factory=datetime.now)
    
    model_config = {
        "json_schema_extra": {
            "example": {
                "id": "lesson-abc123",
                "trigger_pattern": "search logs, empty result, archived partition",
                "rule_text": "When searching logs, always check archived partitions if recent logs are empty",
                "lesson_type": "business",
                "confidence_score": 0.92,
                "created_at": "2026-01-15T23:00:00"
            }
        }
    }


# 2. The Failure Trace (The Evidence)
class FailureTrace(BaseModel):
    """
    Complete trace of a failure including evidence.
    
    This captures everything about what went wrong, including the user prompt,
    agent reasoning, tool execution, and the specific failure that occurred.
    """
    trace_id: str = Field(default_factory=lambda: str(uuid4()))
    user_prompt: str = Field(..., description="The user's original request")
    agent_reasoning: str = Field(..., description="The agent's reasoning/response")
    tool_call: Optional[Dict[str, Any]] = Field(
        None,
        description="The tool call that was made (if any)"
    )
    tool_output: Optional[str] = Field(
        None,
        description="The output from the tool execution"
    )
    failure_type: Literal["omission_laziness", "commission_safety", "hallucination"] = Field(
        ...,
        description="Type of failure: omission (gave up too early), commission (unsafe action), hallucination (invented facts)"
    )
    severity: Literal["critical", "non_critical"] = Field(
        ...,
        description="Severity of the failure"
    )
    timestamp: datetime = Field(default_factory=datetime.now)
    
    model_config = {
        "json_schema_extra": {
            "example": {
                "trace_id": "trace-xyz789",
                "user_prompt": "Find the Q3 report",
                "agent_reasoning": "I searched for 'Q3 report' but found no exact matches.",
                "tool_call": {"tool": "search_files", "query": "Q3 report"},
                "tool_output": "[]",
                "failure_type": "omission_laziness",
                "severity": "non_critical",
                "timestamp": "2026-01-15T23:00:00"
            }
        }
    }


# 3. The Patch (The Prescription)
class PatchRequest(BaseModel):
    """
    A request to patch an agent with a lesson.
    
    This combines the failure evidence (trace_id) with the diagnosis
    and proposed fix (lesson). It also specifies the application strategy
    (hotfix now vs batch later).
    """
    trace_id: str = Field(..., description="Reference to the FailureTrace that triggered this patch")
    diagnosis: str = Field(..., description="Why did it fail? Root cause analysis.")
    proposed_lesson: Lesson = Field(..., description="The lesson to apply")
    apply_strategy: Literal["hotfix_now", "batch_later"] = Field(
        ...,
        description="When to apply: hotfix_now (critical, sync) or batch_later (non-critical, async)"
    )
    context: Dict[str, Any] = Field(
        default_factory=dict,
        description="Additional context for the patch"
    )
    
    model_config = {
        "json_schema_extra": {
            "example": {
                "trace_id": "trace-xyz789",
                "diagnosis": "Agent gave up after finding no exact match for 'Q3 report' without trying alternative search terms like 'Quarter 3' or 'Q3-2024'",
                "proposed_lesson": {
                    "id": "lesson-abc123",
                    "trigger_pattern": "search failure, no exact matches",
                    "rule_text": "When search returns no results, try alternative terms and synonyms before giving up",
                    "lesson_type": "business",
                    "confidence_score": 0.88
                },
                "apply_strategy": "batch_later",
                "context": {"priority": "medium"}
            }
        }
    }
