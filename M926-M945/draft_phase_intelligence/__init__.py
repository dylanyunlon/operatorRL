"""
M927: DraftPhaseIntelligence
选英雄阶段智能辅助 — 实时监听champ select WebSocket事件,结合对手历史数据给出禁选建议
"""
from .draft_phase_intelligence import DraftPhaseIntelligence

__all__ = ["DraftPhaseIntelligence"]
