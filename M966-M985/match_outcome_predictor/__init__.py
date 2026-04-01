"""
M967: MatchOutcomePredictor
对局结果预测器 — 赛前基于双方历史数据的胜率预测引擎，使用ELO变种+英雄对位胜率+近期状态的加权贝叶斯模型
"""
from .match_outcome_predictor import MatchOutcomePredictor

__all__ = ["MatchOutcomePredictor"]
__version__ = "1.0.0"
__module_id__ = "M967"
