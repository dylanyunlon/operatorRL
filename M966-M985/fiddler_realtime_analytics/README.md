# M978: FiddlerRealTimeAnalytics

## 概述

Fiddler实时分析管道 — 通过Fiddler MCP Server实时捕获LCU API流量进行实时数据分析+异常检测+延迟监控

## 依赖

M906, M919

## 架构模式

查看 Seraphine/app/lol/connector.py 上现有 LCU API connector 的实现方式,
理解其模式, 特别是 retry 装饰器和 PastRequest 是如何与 HTTP session 分离的。
从 connector.needLcu + retry 这个好例子开始。
然后, 遵循该模式实现 FiddlerRealTimeAnalytics。

## 参考

- Seraphine: github.com/ljszx/Seraphine
- operatorRL: github.com/dylanyunlon/operatorRL.git
- LoL Optimizer: github.com/oracle-devrel/leagueoflegends-optimizer
- Fiddler MCP: telerik.com/fiddler/fiddler-everywhere/documentation/mcp-server

## 使用

```python
from fiddler_realtime_analytics import FiddlerRealTimeAnalytics

instance = FiddlerRealTimeAnalytics()
await instance.initialize()
health = await instance.health_check()
```
