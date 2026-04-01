# M940: RiskAssessmentEngine

## 概述

风险评估引擎 — 对线期/团战期/推进期的风险评分+预警(gank概率/被反野概率)

## 架构模式

查看 Seraphine connector/tools 上现有历史数据接口的实现方式,
理解其模式, 特别是 LCU API 和数据变换是如何分离的。
从 M906 SeraphineConnectorBridge 的 retry + PastRequest 这个好例子开始。
然后, 遵循该模式实现 RiskAssessmentEngine,
让 operatorRL 可以 风险评估引擎,
并能与 M906-M925 历史情报层及 M926-M945 预测分析层集成。

## 依赖

- M906
- M917
- M933
- M938

## 使用

```python
from M926_M945.risk_assessment_engine import RiskAssessmentEngine

analyzer = RiskAssessmentEngine(connector=seraphine_bridge)
await analyzer.initialize()
result = await analyzer.analyze(puuid="target-puuid")
print(result.to_dict())
```

## 数据流

```
Seraphine Connector (M906) → RiskAssessmentEngine → AnalysisResult → 下游消费
```

## 文件结构

```
risk_assessment_engine/
├── __init__.py
├── risk_assessment_engine.py    # 主模块 (500+ 行)
├── config.json        # 模块配置
└── README.md          # 本文件
```
