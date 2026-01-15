"""
Self-Correcting Agent Kernel - Modern Module Structure.

This package implements the Partner-level repository structure with:
- src/kernel/: Core correction engine (triage, auditor, patcher, memory)
- src/agents/: Agent implementations (shadow_teacher, worker)
- src/interfaces/: External interfaces (telemetry)
"""

__version__ = "2.0.0"

# Import key components for easy access
from .kernel.triage import FailureTriage, FixStrategy
from .kernel.memory import MemoryManager, PatchClassifier, SemanticPurge, LessonType
from .agents.shadow_teacher import ShadowTeacher, diagnose_failure, counterfactual_run
from .agents.worker import AgentWorker, WorkerPool, AgentStatus
from .interfaces.telemetry import TelemetryEmitter, OutcomeAnalyzer, AuditLog, EventType

__all__ = [
    # Kernel components
    "FailureTriage",
    "FixStrategy",
    "MemoryManager",
    "PatchClassifier",
    "SemanticPurge",
    "LessonType",
    
    # Agent components
    "ShadowTeacher",
    "diagnose_failure",
    "counterfactual_run",
    "AgentWorker",
    "WorkerPool",
    "AgentStatus",
    
    # Interface components
    "TelemetryEmitter",
    "OutcomeAnalyzer",
    "AuditLog",
    "EventType",
]
