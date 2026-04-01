# M936: SynergyCounterMatrix

## 概述

协同克制矩阵 — 英雄对英雄+英雄组合的协同/克制评分矩阵,支持实时查询

## 架构模式

查看 Seraphine connector/tools 上现有历史数据接口的实现方式,
理解其模式, 特别是 LCU API 和数据变换是如何分离的。
从 M906 SeraphineConnectorBridge 的 retry + PastRequest 这个好例子开始。
然后, 遵循该模式实现 SynergyCounterMatrix,
让 operatorRL 可以 协同克制矩阵,
并能与 M906-M925 历史情报层及 M926-M945 预测分析层集成。

## 依赖

- M906
- M908
- M915

## 使用

```python
from M926_M945.synergy_counter_matrix import SynergyCounterMatrix

analyzer = SynergyCounterMatrix(connector=seraphine_bridge)
await analyzer.initialize()
result = await analyzer.analyze(puuid="target-puuid")
print(result.to_dict())
```

## 数据流

```
Seraphine Connector (M906) → SynergyCounterMatrix → AnalysisResult → 下游消费
```

## 文件结构

```
synergy_counter_matrix/
├── __init__.py
├── synergy_counter_matrix.py    # 主模块 (500+ 行)
├── config.json        # 模块配置
└── README.md          # 本文件
```
