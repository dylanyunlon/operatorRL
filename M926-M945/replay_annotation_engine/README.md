# M941: ReplayAnnotationEngine

## 概述

回放标注引擎 — 自动标注replay中的关键事件(失误/精彩操作/决策点)并生成报告

## 架构模式

查看 Seraphine connector/tools 上现有历史数据接口的实现方式,
理解其模式, 特别是 LCU API 和数据变换是如何分离的。
从 M906 SeraphineConnectorBridge 的 retry + PastRequest 这个好例子开始。
然后, 遵循该模式实现 ReplayAnnotationEngine,
让 operatorRL 可以 回放标注引擎,
并能与 M906-M925 历史情报层及 M926-M945 预测分析层集成。

## 依赖

- M906
- M926
- M938
- M939

## 使用

```python
from M926_M945.replay_annotation_engine import ReplayAnnotationEngine

analyzer = ReplayAnnotationEngine(connector=seraphine_bridge)
await analyzer.initialize()
result = await analyzer.analyze(puuid="target-puuid")
print(result.to_dict())
```

## 数据流

```
Seraphine Connector (M906) → ReplayAnnotationEngine → AnalysisResult → 下游消费
```

## 文件结构

```
replay_annotation_engine/
├── __init__.py
├── replay_annotation_engine.py    # 主模块 (500+ 行)
├── config.json        # 模块配置
└── README.md          # 本文件
```
