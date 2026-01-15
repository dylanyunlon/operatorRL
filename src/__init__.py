"""
Self-Correcting Agent Kernel - Modern Module Structure.

This package implements the Partner-level repository structure with:
- src/kernel/: Core correction engine (triage, auditor, patcher, memory)
- src/agents/: Agent implementations (shadow_teacher, worker)
- src/interfaces/: External interfaces (telemetry)
"""

import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

__version__ = "2.0.0"

# Import key components for easy access
from src.kernel.triage import FailureTriage, FixStrategy
from src.kernel.memory import MemoryManager, PatchClassifier, SemanticPurge, LessonType
from src.agents.shadow_teacher import ShadowTeacher, diagnose_failure, counterfactual_run
from src.agents.worker import AgentWorker, WorkerPool, AgentStatus
from src.interfaces.telemetry import TelemetryEmitter, OutcomeAnalyzer, AuditLog, EventType

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
