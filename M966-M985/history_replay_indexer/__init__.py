"""
M981: HistoryReplayIndexer
历史回放索引器 — 对局回放文件的关键时刻索引与检索，支持按击杀/死亡/团战/目标等事件类型检索历史回放片段
"""
from .history_replay_indexer import HistoryReplayIndexer

__all__ = ["HistoryReplayIndexer"]
__version__ = "1.0.0"
__module_id__ = "M981"
