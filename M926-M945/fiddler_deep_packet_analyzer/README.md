# M943: FiddlerDeepPacketAnalyzer

## 概述

Fiddler深度包分析器 — 解析LCU/SGP网络包的深层字段,提取隐藏数据(MMR估算/行为评分)

## 架构模式

查看 Seraphine connector/tools 上现有历史数据接口的实现方式,
理解其模式, 特别是 LCU API 和数据变换是如何分离的。
从 M906 SeraphineConnectorBridge 的 retry + PastRequest 这个好例子开始。
然后, 遵循该模式实现 FiddlerDeepPacketAnalyzer,
让 operatorRL 可以 Fiddler深度包分析器,
并能与 M906-M925 历史情报层及 M926-M945 预测分析层集成。

## 依赖

- M906
- M919
- M943

## 使用

```python
from M926_M945.fiddler_deep_packet_analyzer import FiddlerDeepPacketAnalyzer

analyzer = FiddlerDeepPacketAnalyzer(connector=seraphine_bridge)
await analyzer.initialize()
result = await analyzer.analyze(puuid="target-puuid")
print(result.to_dict())
```

## 数据流

```
Seraphine Connector (M906) → FiddlerDeepPacketAnalyzer → AnalysisResult → 下游消费
```

## 文件结构

```
fiddler_deep_packet_analyzer/
├── __init__.py
├── fiddler_deep_packet_analyzer.py    # 主模块 (500+ 行)
├── config.json        # 模块配置
└── README.md          # 本文件
```
