"""
M1026: MatchHistoryDeepFetcher
深度对局历史获取器 — 对接Seraphine LCU connector的/lol-match-history/v1/products/lol端点,批量拉取最近100场对局详情
"""
from .match_history_deep_fetcher import MatchHistoryDeepFetcher

__all__ = ["MatchHistoryDeepFetcher"]
__version__ = "1.0.0"
__module_id__ = "M1026"
