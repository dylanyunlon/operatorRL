"""
Inter-Agent Trust Protocol (IATP)

A Zero-Config Sidecar for Agent Communication that provides:
- Discovery: Capability manifest exchange
- Trust: Security validation and privacy checks
- Reversibility: Transaction tracking and audit logging

Quick Start:
    pip install inter-agent-trust-protocol
    
    # Run as sidecar
    uvicorn iatp.main:app --host 0.0.0.0 --port 8081
    
    # Or use Docker
    docker run -p 8081:8081 -e IATP_AGENT_URL=http://my-agent:8000 iatp-sidecar
"""

__version__ = "0.3.0"

from iatp.models import (
    CapabilityManifest,
    AgentCapabilities,
    PrivacyContract,
    TrustLevel,
    ReversibilityLevel,
    RetentionPolicy,
    QuarantineSession,
    TracingContext,
)

from iatp.sidecar import SidecarProxy, create_sidecar
from iatp.security import SecurityValidator, PrivacyScrubber
from iatp.telemetry import FlightRecorder, TraceIDGenerator
from iatp.policy_engine import IATPPolicyEngine
from iatp.recovery import IATPRecoveryEngine

__all__ = [
    # Models
    "CapabilityManifest",
    "AgentCapabilities",
    "PrivacyContract",
    "TrustLevel",
    "ReversibilityLevel",
    "RetentionPolicy",
    "QuarantineSession",
    "TracingContext",
    # Sidecar
    "SidecarProxy",
    "create_sidecar",
    # Security
    "SecurityValidator",
    "PrivacyScrubber",
    # Telemetry
    "FlightRecorder",
    "TraceIDGenerator",
    # Policy Engine (agent-control-plane integration)
    "IATPPolicyEngine",
    # Recovery Engine (scak integration)
    "IATPRecoveryEngine",
]
