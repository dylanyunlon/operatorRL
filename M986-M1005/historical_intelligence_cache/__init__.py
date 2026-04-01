"""
M1004: HistoricalIntelligenceCache
历史情报缓存层 — Unified cache layer for all historical intelligence — LRU eviction with TTL, pre
"""
from .historical_intelligence_cache import HistoricalIntelligenceCache

__all__ = ["HistoricalIntelligenceCache"]
__version__ = "1.0.0"
__module_id__ = "M1004"
