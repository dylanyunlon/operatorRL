"""
M985: PredictiveIntelligenceOrchestrator
预测情报编排器 — 统一编排所有M966-M984模块的顶层管道，调度分析任务 + 缓存策略 + 健康监控 + 与M866-M885实时系统对接
"""
from .predictive_intelligence_orchestrator import PredictiveIntelligenceOrchestrator

__all__ = ["PredictiveIntelligenceOrchestrator"]
__version__ = "1.0.0"
__module_id__ = "M985"
