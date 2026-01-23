"""
Core data models for the Inter-Agent Trust Protocol (IATP).
"""
from enum import Enum
from typing import Optional, Dict, Any, Literal
from pydantic import BaseModel, Field


class TrustLevel(str, Enum):
    """Trust levels for agents."""
    VERIFIED_PARTNER = "verified_partner"
    TRUSTED = "trusted"
    STANDARD = "standard"
    UNKNOWN = "unknown"
    UNTRUSTED = "untrusted"


class ReversibilityLevel(str, Enum):
    """Reversibility support levels."""
    FULL = "full"  # Full rollback support
    PARTIAL = "partial"  # Limited rollback (e.g., with fees)
    NONE = "none"  # No rollback support


class RetentionPolicy(str, Enum):
    """Data retention policies."""
    EPHEMERAL = "ephemeral"  # Data deleted after session
    TEMPORARY = "temporary"  # Data stored temporarily (e.g., 30 days)
    PERMANENT = "permanent"  # Data stored indefinitely
    FOREVER = "forever"  # Alias for permanent (for compatibility)


class PrivacyContract(BaseModel):
    """Privacy contract specifying data handling policies."""
    retention: RetentionPolicy = Field(
        ...,
        description="How long the agent stores data"
    )
    storage_location: Optional[str] = Field(
        None,
        description="Geographic location of data storage (e.g., 'us-west')"
    )
    human_review: bool = Field(
        False,
        description="Whether humans may review the data"
    )
    encryption_at_rest: bool = Field(
        True,
        description="Whether data is encrypted at rest"
    )
    encryption_in_transit: bool = Field(
        True,
        description="Whether data is encrypted in transit"
    )


class AgentCapabilities(BaseModel):
    """Capabilities advertised by an agent."""
    idempotency: bool = Field(
        False,
        description="Whether duplicate requests are handled safely"
    )
    reversibility: ReversibilityLevel = Field(
        ReversibilityLevel.NONE,
        description="Level of transaction reversibility support"
    )
    undo_window: Optional[str] = Field(
        None,
        description="Time window for undo operations (e.g., '24h', '7d')"
    )
    sla_latency: Optional[str] = Field(
        None,
        description="Promised response latency (e.g., '2000ms', '5s')"
    )
    rate_limit: Optional[int] = Field(
        None,
        description="Maximum requests per minute"
    )


class CapabilityManifest(BaseModel):
    """
    Capability manifest exchanged during handshake.
    This is the core metadata that agents exchange.
    """
    agent_id: str = Field(
        ...,
        description="Unique identifier for the agent"
    )
    agent_version: Optional[str] = Field(
        None,
        description="Version of the agent"
    )
    trust_level: TrustLevel = Field(
        TrustLevel.STANDARD,
        description="Trust level of the agent"
    )
    capabilities: AgentCapabilities = Field(
        ...,
        description="Capabilities supported by the agent"
    )
    privacy_contract: PrivacyContract = Field(
        ...,
        description="Privacy policies of the agent"
    )
    
    def calculate_trust_score(self) -> int:
        """
        Calculate a trust score (0-10) based on capabilities and privacy.
        """
        score = 5  # Start with neutral score
        
        # Trust level adjustments
        trust_scores = {
            TrustLevel.VERIFIED_PARTNER: 3,
            TrustLevel.TRUSTED: 2,
            TrustLevel.STANDARD: 0,
            TrustLevel.UNKNOWN: -2,
            TrustLevel.UNTRUSTED: -5
        }
        score += trust_scores.get(self.trust_level, 0)
        
        # Capability bonuses
        if self.capabilities.idempotency:
            score += 1
        if self.capabilities.reversibility in [ReversibilityLevel.FULL, ReversibilityLevel.PARTIAL]:
            score += 1
        
        # Privacy bonuses
        if self.privacy_contract.retention == RetentionPolicy.EPHEMERAL:
            score += 2
        elif self.privacy_contract.retention in [RetentionPolicy.PERMANENT, RetentionPolicy.FOREVER]:
            score -= 2
        
        if not self.privacy_contract.human_review:
            score += 1
        
        # Clamp to 0-10 range
        return max(0, min(10, score))


class QuarantineSession(BaseModel):
    """Session information for quarantined/untrusted requests."""
    session_id: str
    trace_id: str
    warning_message: str
    user_override: bool = False
    timestamp: str
    manifest: Optional[CapabilityManifest] = None


class TracingContext(BaseModel):
    """Distributed tracing context."""
    trace_id: str = Field(
        ...,
        description="Unique trace ID for the request"
    )
    parent_trace_id: Optional[str] = Field(
        None,
        description="Parent trace ID if this is part of a chain"
    )
    timestamp: str = Field(
        ...,
        description="ISO 8601 timestamp"
    )
    agent_id: str = Field(
        ...,
        description="ID of the agent processing this request"
    )
