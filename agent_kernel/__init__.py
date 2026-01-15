"""
Self-Correcting Agent Kernel

A system that monitors agent failures, analyzes them, simulates better paths,
and automatically patches agents to prevent future failures.
"""

__version__ = "0.1.0"

from .kernel import SelfCorrectingAgentKernel
from .models import AgentFailure, FailureAnalysis, CorrectionPatch

__all__ = [
    "SelfCorrectingAgentKernel",
    "AgentFailure",
    "FailureAnalysis",
    "CorrectionPatch",
]
