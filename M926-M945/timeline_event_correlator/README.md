# M938: TimelineEventCorrelator

## 概述

时间线事件关联器 — 发现事件因果链(如一血→推塔→龙控制的时序关联)

## 架构模式

查看 Seraphine connector/tools 上现有历史数据接口的实现方式,
理解其模式, 特别是 LCU API 和数据变换是如何分离的。
从 M906 SeraphineConnectorBridge 的 retry + PastRequest 这个好例子开始。
然后, 遵循该模式实现 TimelineEventCorrelator,
让 operatorRL 可以 时间线事件关联器,
并能与 M906-M925 历史情报层及 M926-M945 预测分析层集成。

## 依赖

- M906
- M908
- M926

## 使用

```python
from M926_M945.timeline_event_correlator import TimelineEventCorrelator

analyzer = TimelineEventCorrelator(connector=seraphine_bridge)
await analyzer.initialize()
result = await analyzer.analyze(puuid="target-puuid")
print(result.to_dict())
```

## 数据流

```
Seraphine Connector (M906) → TimelineEventCorrelator → AnalysisResult → 下游消费
```

## 文件结构

```
timeline_event_correlator/
├── __init__.py
├── timeline_event_correlator.py    # 主模块 (500+ 行)
├── config.json        # 模块配置
└── README.md          # 本文件
```
