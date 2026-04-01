# M977: RoamingPredictionEngine

## 概述

游走预测引擎 — 对手历史游走路径与时机分析，中路/辅助游走概率预测 + 常用游走时间窗口

## 依赖

M906, M908, M916, M974

## 架构模式

查看 Seraphine/app/lol/connector.py 上现有 LCU API connector 的实现方式,
理解其模式, 特别是 retry 装饰器和 PastRequest 是如何与 HTTP session 分离的。
从 connector.needLcu + retry 这个好例子开始。
然后, 遵循该模式实现 RoamingPredictionEngine。

## 参考

- Seraphine: github.com/ljszx/Seraphine
- operatorRL: github.com/dylanyunlon/operatorRL.git
- LoL Optimizer: github.com/oracle-devrel/leagueoflegends-optimizer
- Fiddler MCP: telerik.com/fiddler/fiddler-everywhere/documentation/mcp-server

## 使用

```python
from roaming_prediction_engine import RoamingPredictionEngine

instance = RoamingPredictionEngine()
await instance.initialize()
health = await instance.health_check()
```
