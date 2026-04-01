# M926: ReplayTimelineAnalyzer

## 概述

回放时间线深度分析 — 从replay文件提取完整timeline事件,构建分钟级状态快照,识别关键转折点

## 架构模式

查看 Seraphine connector/tools 上现有历史数据接口的实现方式,
理解其模式, 特别是 LCU API 和数据变换是如何分离的。
从 M906 SeraphineConnectorBridge 的 retry + PastRequest 这个好例子开始。
然后, 遵循该模式实现 ReplayTimelineAnalyzer,
让 operatorRL 可以 回放时间线深度分析,
并能与 M906-M925 历史情报层及 M926-M945 预测分析层集成。

## 依赖

- M906
- M908
- M938

## 使用

```python
from M926_M945.replay_timeline_analyzer import ReplayTimelineAnalyzer

analyzer = ReplayTimelineAnalyzer(connector=seraphine_bridge)
await analyzer.initialize()
result = await analyzer.analyze(puuid="target-puuid")
print(result.to_dict())
```

## 数据流

```
Seraphine Connector (M906) → ReplayTimelineAnalyzer → AnalysisResult → 下游消费
```

## 文件结构

```
replay_timeline_analyzer/
├── __init__.py
├── replay_timeline_analyzer.py    # 主模块 (500+ 行)
├── config.json        # 模块配置
└── README.md          # 本文件
```
