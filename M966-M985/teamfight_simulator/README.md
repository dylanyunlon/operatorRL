# M973: TeamfightSimulator

## 概述

团战模拟器 — 基于历史团战数据的胜率模拟，阵容克制关系 + 装备差距 + 等级差距的团战结果概率分布

## 依赖

M906, M908, M918, M967

## 架构模式

查看 Seraphine/app/lol/connector.py 上现有 LCU API connector 的实现方式,
理解其模式, 特别是 retry 装饰器和 PastRequest 是如何与 HTTP session 分离的。
从 connector.needLcu + retry 这个好例子开始。
然后, 遵循该模式实现 TeamfightSimulator。

## 参考

- Seraphine: github.com/ljszx/Seraphine
- operatorRL: github.com/dylanyunlon/operatorRL.git
- LoL Optimizer: github.com/oracle-devrel/leagueoflegends-optimizer
- Fiddler MCP: telerik.com/fiddler/fiddler-everywhere/documentation/mcp-server

## 使用

```python
from teamfight_simulator import TeamfightSimulator

instance = TeamfightSimulator()
await instance.initialize()
health = await instance.health_check()
```
