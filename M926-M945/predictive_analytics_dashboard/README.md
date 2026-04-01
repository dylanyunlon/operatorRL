# M945: PredictiveAnalyticsDashboard

## 概述

预测分析仪表盘 — 统一HTML/JSON报告+语音简报+实时WebSocket推送的前端聚合层

## 架构模式

查看 Seraphine connector/tools 上现有历史数据接口的实现方式,
理解其模式, 特别是 LCU API 和数据变换是如何分离的。
从 M906 SeraphineConnectorBridge 的 retry + PastRequest 这个好例子开始。
然后, 遵循该模式实现 PredictiveAnalyticsDashboard,
让 operatorRL 可以 预测分析仪表盘,
并能与 M906-M925 历史情报层及 M926-M945 预测分析层集成。

## 依赖

- M906
- M925
- M931
- M944

## 使用

```python
from M926_M945.predictive_analytics_dashboard import PredictiveAnalyticsDashboard

analyzer = PredictiveAnalyticsDashboard(connector=seraphine_bridge)
await analyzer.initialize()
result = await analyzer.analyze(puuid="target-puuid")
print(result.to_dict())
```

## 数据流

```
Seraphine Connector (M906) → PredictiveAnalyticsDashboard → AnalysisResult → 下游消费
```

## 文件结构

```
predictive_analytics_dashboard/
├── __init__.py
├── predictive_analytics_dashboard.py    # 主模块 (500+ 行)
├── config.json        # 模块配置
└── README.md          # 本文件
```
