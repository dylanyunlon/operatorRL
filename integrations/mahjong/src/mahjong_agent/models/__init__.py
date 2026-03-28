"""
Models subpackage — bot base classes and DRL adapters.

Exports:
    MjaiBotBase: Abstract mjai bot interface
    MortalAdapter: Mortal DRL engine adapter

Location: integrations/mahjong/src/mahjong_agent/models/__init__.py
"""

from mahjong_agent.models.mjai_bot_base import MjaiBotBase

__all__ = ["MjaiBotBase"]
