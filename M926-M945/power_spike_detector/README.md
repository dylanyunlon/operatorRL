# M932: PowerSpikeDetector

## 概述

强势期检测器 — 基于英雄等级/装备节点/技能冷却的动态强势期预测与提醒

## 架构模式

查看 Seraphine connector/tools 上现有历史数据接口的实现方式,
理解其模式, 特别是 LCU API 和数据变换是如何分离的。
从 M906 SeraphineConnectorBridge 的 retry + PastRequest 这个好例子开始。
然后, 遵循该模式实现 PowerSpikeDetector,
让 operatorRL 可以 强势期检测器,
并能与 M906-M925 历史情报层及 M926-M945 预测分析层集成。

## 依赖

- M906
- M908
- M929

## 使用

```python
from M926_M945.power_spike_detector import PowerSpikeDetector

analyzer = PowerSpikeDetector(connector=seraphine_bridge)
await analyzer.initialize()
result = await analyzer.analyze(puuid="target-puuid")
print(result.to_dict())
```

## 数据流

```
Seraphine Connector (M906) → PowerSpikeDetector → AnalysisResult → 下游消费
```

## 文件结构

```
power_spike_detector/
├── __init__.py
├── power_spike_detector.py    # 主模块 (500+ 行)
├── config.json        # 模块配置
└── README.md          # 本文件
```
