"""
M978: FiddlerRealTimeAnalytics
Fiddler实时分析管道 — 通过Fiddler MCP Server实时捕获LCU API流量进行实时数据分析+异常检测+延迟监控
"""
from .fiddler_realtime_analytics import FiddlerRealTimeAnalytics

__all__ = ["FiddlerRealTimeAnalytics"]
__version__ = "1.0.0"
__module_id__ = "M978"
