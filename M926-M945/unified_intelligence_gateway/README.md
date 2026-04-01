# M944: UnifiedIntelligenceGateway

## 概述

统一情报API网关 — 聚合M906-M945所有模块的RESTful API入口,支持WebSocket实时推送

## 架构模式

查看 Seraphine connector/tools 上现有历史数据接口的实现方式,
理解其模式, 特别是 LCU API 和数据变换是如何分离的。
从 M906 SeraphineConnectorBridge 的 retry + PastRequest 这个好例子开始。
然后, 遵循该模式实现 UnifiedIntelligenceGateway,
让 operatorRL 可以 统一情报API网关,
并能与 M906-M925 历史情报层及 M926-M945 预测分析层集成。

## 依赖

- M906
- M914
- M922
- M925

## 使用

```python
from M926_M945.unified_intelligence_gateway import UnifiedIntelligenceGateway

analyzer = UnifiedIntelligenceGateway(connector=seraphine_bridge)
await analyzer.initialize()
result = await analyzer.analyze(puuid="target-puuid")
print(result.to_dict())
```

## 数据流

```
Seraphine Connector (M906) → UnifiedIntelligenceGateway → AnalysisResult → 下游消费
```

## 文件结构

```
unified_intelligence_gateway/
├── __init__.py
├── unified_intelligence_gateway.py    # 主模块 (500+ 行)
├── config.json        # 模块配置
└── README.md          # 本文件
```
