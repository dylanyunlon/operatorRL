# M968: DraftSimulationEngine

## 概述

Ban/Pick模拟引擎 — 基于历史英雄池+阵容原型的蒙特卡洛选人模拟，为BP阶段提供最优策略推荐序列

## 依赖

M906, M911, M918, M967

## 架构模式

查看 Seraphine/app/lol/connector.py 上现有 LCU API connector 的实现方式,
理解其模式, 特别是 retry 装饰器和 PastRequest 是如何与 HTTP session 分离的。
从 connector.needLcu + retry 这个好例子开始。
然后, 遵循该模式实现 DraftSimulationEngine。

## 参考

- Seraphine: github.com/ljszx/Seraphine
- operatorRL: github.com/dylanyunlon/operatorRL.git
- LoL Optimizer: github.com/oracle-devrel/leagueoflegends-optimizer
- Fiddler MCP: telerik.com/fiddler/fiddler-everywhere/documentation/mcp-server

## 使用

```python
from draft_simulation_engine import DraftSimulationEngine

instance = DraftSimulationEngine()
await instance.initialize()
health = await instance.health_check()
```
