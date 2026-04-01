# M934: MacroStrategyRecommender

## 概述

宏观策略推荐器 — 基于阵容类型+对手习惯+游戏阶段的分推/团战/入侵策略推荐

## 架构模式

查看 Seraphine connector/tools 上现有历史数据接口的实现方式,
理解其模式, 特别是 LCU API 和数据变换是如何分离的。
从 M906 SeraphineConnectorBridge 的 retry + PastRequest 这个好例子开始。
然后, 遵循该模式实现 MacroStrategyRecommender,
让 operatorRL 可以 宏观策略推荐器,
并能与 M906-M925 历史情报层及 M926-M945 预测分析层集成。

## 依赖

- M906
- M917
- M918
- M932

## 使用

```python
from M926_M945.macro_strategy_recommender import MacroStrategyRecommender

analyzer = MacroStrategyRecommender(connector=seraphine_bridge)
await analyzer.initialize()
result = await analyzer.analyze(puuid="target-puuid")
print(result.to_dict())
```

## 数据流

```
Seraphine Connector (M906) → MacroStrategyRecommender → AnalysisResult → 下游消费
```

## 文件结构

```
macro_strategy_recommender/
├── __init__.py
├── macro_strategy_recommender.py    # 主模块 (500+ 行)
├── config.json        # 模块配置
└── README.md          # 本文件
```
