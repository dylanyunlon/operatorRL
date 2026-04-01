# M971: RuneStrategyEngine

## 概述

符文策略引擎 — 基于英雄对位+对手习惯的符文组合优化，历史符文选择胜率矩阵 + 版本适配符文推荐

## 依赖

M906, M908, M970

## 架构模式

查看 Seraphine/app/lol/connector.py 上现有 LCU API connector 的实现方式,
理解其模式, 特别是 retry 装饰器和 PastRequest 是如何与 HTTP session 分离的。
从 connector.needLcu + retry 这个好例子开始。
然后, 遵循该模式实现 RuneStrategyEngine。

## 参考

- Seraphine: github.com/ljszx/Seraphine
- operatorRL: github.com/dylanyunlon/operatorRL.git
- LoL Optimizer: github.com/oracle-devrel/leagueoflegends-optimizer
- Fiddler MCP: telerik.com/fiddler/fiddler-everywhere/documentation/mcp-server

## 使用

```python
from rune_strategy_engine import RuneStrategyEngine

instance = RuneStrategyEngine()
await instance.initialize()
health = await instance.health_check()
```
