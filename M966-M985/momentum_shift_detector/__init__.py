"""
M976: MomentumShiftDetector
局势转换检测器 — 历史对局中的翻盘/滚雪球模式识别，基于金币曲线+经验曲线+目标控制的局势转折点定位
"""
from .momentum_shift_detector import MomentumShiftDetector

__all__ = ["MomentumShiftDetector"]
__version__ = "1.0.0"
__module_id__ = "M976"
