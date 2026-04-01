# M970: ItemBuildPathOptimizer

## 概述

出装路径优化器 — 基于历史对局的出装路径效率分析，针对特定对手的反制出装推荐 + 出装时间节点优化

## 依赖

M906, M908, M969

## 架构模式

查看 Seraphine/app/lol/connector.py 上现有 LCU API connector 的实现方式,
理解其模式, 特别是 retry 装饰器和 PastRequest 是如何与 HTTP session 分离的。
从 connector.needLcu + retry 这个好例子开始。
然后, 遵循该模式实现 ItemBuildPathOptimizer。

## 参考

- Seraphine: github.com/ljszx/Seraphine
- operatorRL: github.com/dylanyunlon/operatorRL.git
- LoL Optimizer: github.com/oracle-devrel/leagueoflegends-optimizer
- Fiddler MCP: telerik.com/fiddler/fiddler-everywhere/documentation/mcp-server

## 使用

```python
from item_build_path_optimizer import ItemBuildPathOptimizer

instance = ItemBuildPathOptimizer()
await instance.initialize()
health = await instance.health_check()
```
