"""Kernel components: triage, auditor, patcher, memory."""

from .triage import FailureTriage, FixStrategy
from .memory import MemoryManager, PatchClassifier, SemanticPurge, LessonType

# Note: auditor and patcher are imported from agent_kernel for backward compatibility
try:
    from ..agent_kernel.completeness_auditor import CompletenessAuditor
    from ..agent_kernel.patcher import AgentPatcher
except ImportError:
    from agent_kernel.completeness_auditor import CompletenessAuditor
    from agent_kernel.patcher import AgentPatcher

__all__ = [
    "FailureTriage",
    "FixStrategy",
    "MemoryManager",
    "PatchClassifier",
    "SemanticPurge",
    "LessonType",
    "CompletenessAuditor",
    "AgentPatcher",
]
