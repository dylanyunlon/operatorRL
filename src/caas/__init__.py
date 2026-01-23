"""
CaaS Core: Layer 1 Primitive for Context Management

A pure, logic-only library for routing context, handling RAG fallacies,
and managing context windows. This library does not know what an "agent" is;
it only knows about text and vectors.

Publication Target: PyPI (pip install caas-core)

Design Philosophy:
- Stateless: Context routing logic is stateless and processes only the data passed to it
- No Agent Dependencies: No imports of Agent, Supervisor, or agent frameworks
- Pure Functions: Methods accept generic strings (identifiers) or dicts (metadata)
- Decoupled: Does not query active agent runtimes

Forbidden Dependencies:
- agent-control-plane
- iatp
- scak

Allowed Dependencies:
- numpy / pandas (for data handling)
- openai / langchain (optional, only for embeddings if needed)
"""

__version__ = "0.2.0"
