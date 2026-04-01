# M928: BanPickRecommendationEngine

## 概述

禁选推荐引擎 — 基于对手英雄池+Meta胜率+克制关系的多维度评分推荐系统

## 架构模式

查看 Seraphine connector/tools 上现有历史数据接口的实现方式,
理解其模式, 特别是 LCU API 和数据变换是如何分离的。
从 M906 SeraphineConnectorBridge 的 retry + PastRequest 这个好例子开始。
然后, 遵循该模式实现 BanPickRecommendationEngine,
让 operatorRL 可以 禁选推荐引擎,
并能与 M906-M925 历史情报层及 M926-M945 预测分析层集成。

## 依赖

- M906
- M911
- M915
- M930

## 使用

```python
from M926_M945.ban_pick_recommendation_engine import BanPickRecommendationEngine

analyzer = BanPickRecommendationEngine(connector=seraphine_bridge)
await analyzer.initialize()
result = await analyzer.analyze(puuid="target-puuid")
print(result.to_dict())
```

## 数据流

```
Seraphine Connector (M906) → BanPickRecommendationEngine → AnalysisResult → 下游消费
```

## 文件结构

```
ban_pick_recommendation_engine/
├── __init__.py
├── ban_pick_recommendation_engine.py    # 主模块 (500+ 行)
├── config.json        # 模块配置
└── README.md          # 本文件
```
