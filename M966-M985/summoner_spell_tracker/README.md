# M975: SummonerSpellTracker

## 概述

召唤师技能追踪器 — 基于历史数据的闪现/传送使用模式分析，技能CD预测 + 使用倾向性(攻击型/防御型)分类

## 依赖

M906, M908, M969

## 架构模式

查看 Seraphine/app/lol/connector.py 上现有 LCU API connector 的实现方式,
理解其模式, 特别是 retry 装饰器和 PastRequest 是如何与 HTTP session 分离的。
从 connector.needLcu + retry 这个好例子开始。
然后, 遵循该模式实现 SummonerSpellTracker。

## 参考

- Seraphine: github.com/ljszx/Seraphine
- operatorRL: github.com/dylanyunlon/operatorRL.git
- LoL Optimizer: github.com/oracle-devrel/leagueoflegends-optimizer
- Fiddler MCP: telerik.com/fiddler/fiddler-everywhere/documentation/mcp-server

## 使用

```python
from summoner_spell_tracker import SummonerSpellTracker

instance = SummonerSpellTracker()
await instance.initialize()
health = await instance.health_check()
```
