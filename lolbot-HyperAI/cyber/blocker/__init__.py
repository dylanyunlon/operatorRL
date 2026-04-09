"""
cyber.blocker — Intra-process message blocking and pub/sub.
=============================================================

Apollo reference: ``cyber/blocker/``

The blocker layer provides in-process publish/subscribe that bypasses
the transport layer for same-process component communication.

Claude27: New layer — fills structural gap vs Apollo.
Location: lolbot-HyperAI/cyber/blocker/__init__.py
"""

from cyber.blocker.blocker import Blocker
from cyber.blocker.blocker_manager import BlockerManager
from cyber.blocker.intra_reader import IntraReader
from cyber.blocker.intra_writer import IntraWriter

__all__ = [
    "Blocker",
    "BlockerManager",
    "IntraReader",
    "IntraWriter",
]
