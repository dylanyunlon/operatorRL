"""
M982: VoiceNarrationPipeline
语音播报管道 — 将分析结果转化为实时语音播报，赛前情报简报 + 赛中局势播报 + 关键决策提醒的TTS管道
"""
from .voice_narration_pipeline import VoiceNarrationPipeline

__all__ = ["VoiceNarrationPipeline"]
__version__ = "1.0.0"
__module_id__ = "M982"
