"""
M966: HistoricalPatternRecognizer
历史模式识别器 — 基于对局时间线的对手行为模式聚类与分类，使用滑动窗口时序分析从Seraphine获取的历史对局中提取可复现的行为序列
"""
from .historical_pattern_recognizer import HistoricalPatternRecognizer

__all__ = ["HistoricalPatternRecognizer"]
__version__ = "1.0.0"
__module_id__ = "M966"
