# M972: ObjectivePriorityForecaster

## 概述

目标优先级预测器 — 基于对手历史的龙/峡谷先锋/男爵争夺模式预测，提供下一个目标的争夺概率与最优时间窗口

## 依赖

M906, M917, M966

## 架构模式

查看 Seraphine/app/lol/connector.py 上现有 LCU API connector 的实现方式,
理解其模式, 特别是 retry 装饰器和 PastRequest 是如何与 HTTP session 分离的。
从 connector.needLcu + retry 这个好例子开始。
然后, 遵循该模式实现 ObjectivePriorityForecaster。

## 参考

- Seraphine: github.com/ljszx/Seraphine
- operatorRL: github.com/dylanyunlon/operatorRL.git
- LoL Optimizer: github.com/oracle-devrel/leagueoflegends-optimizer
- Fiddler MCP: telerik.com/fiddler/fiddler-everywhere/documentation/mcp-server

## 使用

```python
from objective_priority_forecaster import ObjectivePriorityForecaster

instance = ObjectivePriorityForecaster()
await instance.initialize()
health = await instance.health_check()
```
