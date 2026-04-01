# M927: DraftPhaseIntelligence

## 概述

选英雄阶段智能辅助 — 实时监听champ select WebSocket事件,结合对手历史数据给出禁选建议

## 架构模式

查看 Seraphine connector/tools 上现有历史数据接口的实现方式,
理解其模式, 特别是 LCU API 和数据变换是如何分离的。
从 M906 SeraphineConnectorBridge 的 retry + PastRequest 这个好例子开始。
然后, 遵循该模式实现 DraftPhaseIntelligence,
让 operatorRL 可以 选英雄阶段智能辅助,
并能与 M906-M925 历史情报层及 M926-M945 预测分析层集成。

## 依赖

- M906
- M910
- M911
- M928

## 使用

```python
from M926_M945.draft_phase_intelligence import DraftPhaseIntelligence

analyzer = DraftPhaseIntelligence(connector=seraphine_bridge)
await analyzer.initialize()
result = await analyzer.analyze(puuid="target-puuid")
print(result.to_dict())
```

## 数据流

```
Seraphine Connector (M906) → DraftPhaseIntelligence → AnalysisResult → 下游消费
```

## 文件结构

```
draft_phase_intelligence/
├── __init__.py
├── draft_phase_intelligence.py    # 主模块 (500+ 行)
├── config.json        # 模块配置
└── README.md          # 本文件
```
