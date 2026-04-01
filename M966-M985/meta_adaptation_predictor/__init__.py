"""
M980: MetaAdaptationPredictor
版本适应预测器 — 预测对手对新版本变更的适应速度与方向，基于历史版本切换时的英雄池调整模式
"""
from .meta_adaptation_predictor import MetaAdaptationPredictor

__all__ = ["MetaAdaptationPredictor"]
__version__ = "1.0.0"
__module_id__ = "M980"
