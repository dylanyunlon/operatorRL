"""
Data models for the self-correcting agent kernel.
"""

from typing import Dict, List, Optional, Any
from datetime import datetime
from enum import Enum
from pydantic import BaseModel, Field, ConfigDict


class FailureType(str, Enum):
    """Types of agent failures."""
    BLOCKED_BY_CONTROL_PLANE = "blocked_by_control_plane"
    TIMEOUT = "timeout"
    INVALID_ACTION = "invalid_action"
    RESOURCE_EXHAUSTED = "resource_exhausted"
    LOGIC_ERROR = "logic_error"
    UNKNOWN = "unknown"


class FailureSeverity(str, Enum):
    """Severity levels for failures."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class CognitiveGlitch(str, Enum):
    """Types of cognitive glitches that can occur in agent reasoning."""
    HALLUCINATION = "hallucination"  # Agent invents facts not in context
    LOGIC_ERROR = "logic_error"  # Agent misunderstands instructions or makes faulty inferences
    CONTEXT_GAP = "context_gap"  # Agent lacks necessary information in prompt/schema
    PERMISSION_ERROR = "permission_error"  # Agent attempts unauthorized actions
    SCHEMA_MISMATCH = "schema_mismatch"  # Agent references non-existent tables/fields
    TOOL_MISUSE = "tool_misuse"  # Agent uses tool with wrong parameter types or values
    POLICY_VIOLATION = "policy_violation"  # Agent violates policy boundaries (e.g., medical advice)
    NONE = "none"  # No cognitive glitch detected


class FailureTrace(BaseModel):
    """Full trace of an agent failure including reasoning chain."""
    
    user_prompt: str = Field(..., description="Original user prompt that led to failure")
    chain_of_thought: List[str] = Field(default_factory=list, description="Agent's reasoning steps")
    failed_action: Dict[str, Any] = Field(..., description="The action that failed")
    error_details: str = Field(..., description="Detailed error information")
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "user_prompt": "Delete the recent user records",
                "chain_of_thought": [
                    "User wants to delete records",
                    "I need to identify which records are 'recent'",
                    "I'll delete from users table"
                ],
                "failed_action": {
                    "action": "execute_sql",
                    "query": "DELETE FROM users WHERE created_at > '2024-01-01'"
                },
                "error_details": "Action blocked by control plane: Dangerous SQL query"
            }
        }
    )


class AgentFailure(BaseModel):
    """Represents a failure detected in an agent."""
    
    agent_id: str = Field(..., description="Unique identifier for the agent")
    failure_type: FailureType = Field(..., description="Type of failure")
    severity: FailureSeverity = Field(default=FailureSeverity.MEDIUM)
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    error_message: str = Field(..., description="Error message from the failure")
    context: Dict[str, Any] = Field(default_factory=dict, description="Additional context")
    stack_trace: Optional[str] = Field(None, description="Stack trace if available")
    failure_trace: Optional[FailureTrace] = Field(None, description="Full failure trace if available")
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "agent_id": "agent-123",
                "failure_type": "blocked_by_control_plane",
                "severity": "high",
                "error_message": "Agent action blocked by control plane policy",
                "context": {"action": "delete_file", "resource": "/etc/passwd"}
            }
        }
    )


class FailureAnalysis(BaseModel):
    """Analysis of an agent failure."""
    
    failure: AgentFailure
    root_cause: str = Field(..., description="Identified root cause")
    contributing_factors: List[str] = Field(default_factory=list)
    suggested_fixes: List[str] = Field(default_factory=list)
    confidence_score: float = Field(..., ge=0.0, le=1.0, description="Confidence in analysis")
    similar_failures: List[str] = Field(default_factory=list, description="IDs of similar past failures")
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "root_cause": "Agent attempted unauthorized file access",
                "contributing_factors": ["Missing permission check", "Inadequate input validation"],
                "suggested_fixes": ["Add permission validation", "Implement safe file access patterns"],
                "confidence_score": 0.85
            }
        }
    )


class DiagnosisJSON(BaseModel):
    """Structured diagnosis identifying cognitive glitches in agent reasoning."""
    
    cognitive_glitch: CognitiveGlitch = Field(..., description="Primary cognitive glitch identified")
    deep_problem: str = Field(..., description="Deep analysis of the problem")
    evidence: List[str] = Field(default_factory=list, description="Evidence supporting diagnosis")
    hint: str = Field(..., description="Hint to inject for counterfactual simulation")
    expected_fix: str = Field(..., description="Expected outcome of applying the hint")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Confidence in diagnosis")
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "cognitive_glitch": "hallucination",
                "deep_problem": "Agent invented table name 'recent_users' that doesn't exist in schema",
                "evidence": [
                    "Query references 'recent_users' table",
                    "Schema only contains 'users' table",
                    "No context provided about table names"
                ],
                "hint": "Available tables: users, orders, products. Use 'users' table with date filter.",
                "expected_fix": "Agent will query 'users' table with proper date filter",
                "confidence": 0.92
            }
        }
    )


class SimulationResult(BaseModel):
    """Result of simulating an alternative path."""
    
    simulation_id: str
    success: bool
    alternative_path: List[Dict[str, Any]] = Field(description="Steps in the alternative path")
    expected_outcome: str
    risk_score: float = Field(..., ge=0.0, le=1.0, description="Risk of the alternative")
    estimated_success_rate: float = Field(..., ge=0.0, le=1.0)
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "simulation_id": "sim-456",
                "success": True,
                "alternative_path": [
                    {"action": "validate_permissions", "params": {}},
                    {"action": "safe_file_access", "params": {"file": "/tmp/safe.txt"}}
                ],
                "expected_outcome": "Safe file operation completed",
                "risk_score": 0.15,
                "estimated_success_rate": 0.92
            }
        }
    )


class ShadowAgentResult(BaseModel):
    """Result of running a shadow agent with counterfactual simulation."""
    
    shadow_id: str
    original_prompt: str = Field(..., description="Original user prompt")
    injected_hint: str = Field(..., description="Hint injected into the prompt")
    modified_prompt: str = Field(..., description="Full prompt with hint")
    execution_success: bool = Field(..., description="Whether execution succeeded")
    output: str = Field(..., description="Output from shadow agent")
    reasoning_chain: List[str] = Field(default_factory=list, description="Shadow agent's reasoning")
    action_taken: Optional[Dict[str, Any]] = Field(None, description="Action the shadow agent took")
    verified: bool = Field(..., description="Whether the fix actually works")
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "shadow_id": "shadow-789",
                "original_prompt": "Delete recent user records",
                "injected_hint": "Available tables: users. 'Recent' means created_at > 7 days ago",
                "modified_prompt": "Delete recent user records. [HINT: Available tables: users. 'Recent' means created_at > 7 days ago]",
                "execution_success": True,
                "output": "Query executed successfully",
                "reasoning_chain": ["Parse user request", "Check hint for table info", "Build safe query"],
                "action_taken": {"action": "execute_sql", "query": "DELETE FROM users WHERE created_at > NOW() - INTERVAL 7 DAY"},
                "verified": True
            }
        }
    )


class CorrectionPatch(BaseModel):
    """A patch to correct an agent's behavior."""
    
    patch_id: str
    agent_id: str
    failure_analysis: FailureAnalysis
    simulation_result: SimulationResult
    patch_type: str = Field(..., description="Type of patch (code, config, rule)")
    patch_content: Dict[str, Any] = Field(..., description="The actual patch content")
    applied: bool = Field(default=False)
    applied_at: Optional[datetime] = None
    rollback_available: bool = Field(default=True)
    diagnosis: Optional["DiagnosisJSON"] = Field(None, description="Cognitive diagnosis if available")
    shadow_result: Optional[ShadowAgentResult] = Field(None, description="Shadow agent verification result")
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "patch_id": "patch-789",
                "agent_id": "agent-123",
                "patch_type": "code",
                "patch_content": {
                    "module": "file_handler",
                    "changes": [
                        {"type": "add_validation", "code": "if not has_permission(file): return"}
                    ]
                },
                "applied": True
            }
        }
    )


class PatchStrategy(str, Enum):
    """Strategy for applying patches."""
    SYSTEM_PROMPT = "system_prompt"  # Easy fix: Update system prompt
    RAG_MEMORY = "rag_memory"  # Hard fix: Inject into vector store
    CODE_CHANGE = "code_change"  # Direct code modification
    CONFIG_UPDATE = "config_update"  # Configuration change
    RULE_UPDATE = "rule_update"  # Policy/rule update


class AgentState(BaseModel):
    """Current state of an agent."""
    
    agent_id: str
    status: str = Field(..., description="Current status (running, failed, patched, etc.)")
    last_failure: Optional[AgentFailure] = None
    patches_applied: List[str] = Field(default_factory=list, description="List of patch IDs")
    success_rate: float = Field(default=1.0, ge=0.0, le=1.0)
    total_runs: int = Field(default=0)
    failed_runs: int = Field(default=0)
