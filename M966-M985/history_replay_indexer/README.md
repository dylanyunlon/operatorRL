# M981: HistoryReplayIndexer

## 概述

历史回放索引器 — 对局回放文件的关键时刻索引与检索，支持按击杀/死亡/团战/目标等事件类型检索历史回放片段

## 依赖

M906, M907, M908

## 架构模式

查看 Seraphine/app/lol/connector.py 上现有 LCU API connector 的实现方式,
理解其模式, 特别是 retry 装饰器和 PastRequest 是如何与 HTTP session 分离的。
从 connector.needLcu + retry 这个好例子开始。
然后, 遵循该模式实现 HistoryReplayIndexer。

## 参考

- Seraphine: github.com/ljszx/Seraphine
- operatorRL: github.com/dylanyunlon/operatorRL.git
- LoL Optimizer: github.com/oracle-devrel/leagueoflegends-optimizer
- Fiddler MCP: telerik.com/fiddler/fiddler-everywhere/documentation/mcp-server

## 使用

```python
from history_replay_indexer import HistoryReplayIndexer

instance = HistoryReplayIndexer()
await instance.initialize()
health = await instance.health_check()
```
