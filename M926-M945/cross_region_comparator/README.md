# M942: CrossRegionComparator

## 概述

跨区对比分析器 — 对比不同大区玩家的英雄偏好/出装/打法差异,SGP多区数据聚合

## 架构模式

查看 Seraphine connector/tools 上现有历史数据接口的实现方式,
理解其模式, 特别是 LCU API 和数据变换是如何分离的。
从 M906 SeraphineConnectorBridge 的 retry + PastRequest 这个好例子开始。
然后, 遵循该模式实现 CrossRegionComparator,
让 operatorRL 可以 跨区对比分析器,
并能与 M906-M925 历史情报层及 M926-M945 预测分析层集成。

## 依赖

- M906
- M908
- M923

## 使用

```python
from M926_M945.cross_region_comparator import CrossRegionComparator

analyzer = CrossRegionComparator(connector=seraphine_bridge)
await analyzer.initialize()
result = await analyzer.analyze(puuid="target-puuid")
print(result.to_dict())
```

## 数据流

```
Seraphine Connector (M906) → CrossRegionComparator → AnalysisResult → 下游消费
```

## 文件结构

```
cross_region_comparator/
├── __init__.py
├── cross_region_comparator.py    # 主模块 (500+ 行)
├── config.json        # 模块配置
└── README.md          # 本文件
```
