"""
M968: DraftSimulationEngine
Ban/Pick模拟引擎 — 基于历史英雄池+阵容原型的蒙特卡洛选人模拟，为BP阶段提供最优策略推荐序列
"""
from .draft_simulation_engine import DraftSimulationEngine

__all__ = ["DraftSimulationEngine"]
__version__ = "1.0.0"
__module_id__ = "M968"
