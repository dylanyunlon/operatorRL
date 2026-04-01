"""
M983: TrainingDataExporter
训练数据导出器 — 将历史分析结果转化为RL训练三元组，state-action-reward格式导出 + AgentLightning训练循环对接
"""
from .training_data_exporter import TrainingDataExporter

__all__ = ["TrainingDataExporter"]
__version__ = "1.0.0"
__module_id__ = "M983"
