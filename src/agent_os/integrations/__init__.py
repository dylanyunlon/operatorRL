"""
Agent OS Integrations

Adapters to wrap existing agent frameworks with Agent OS governance.
"""

from .langchain_adapter import LangChainKernel
from .crewai_adapter import CrewAIKernel
from .autogen_adapter import AutoGenKernel
from .base import BaseIntegration

__all__ = [
    "LangChainKernel",
    "CrewAIKernel", 
    "AutoGenKernel",
    "BaseIntegration",
]
