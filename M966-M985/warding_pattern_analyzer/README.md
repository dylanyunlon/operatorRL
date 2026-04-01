# M974: WardingPatternAnalyzer

## 概述

插眼模式分析器 — 对手历史视野控制习惯挖掘，常用插眼位置热力图 + 排眼频率 + 视野盲区识别

## 依赖

M906, M908, M916

## 架构模式

查看 Seraphine/app/lol/connector.py 上现有 LCU API connector 的实现方式,
理解其模式, 特别是 retry 装饰器和 PastRequest 是如何与 HTTP session 分离的。
从 connector.needLcu + retry 这个好例子开始。
然后, 遵循该模式实现 WardingPatternAnalyzer。

## 参考

- Seraphine: github.com/ljszx/Seraphine
- operatorRL: github.com/dylanyunlon/operatorRL.git
- LoL Optimizer: github.com/oracle-devrel/leagueoflegends-optimizer
- Fiddler MCP: telerik.com/fiddler/fiddler-everywhere/documentation/mcp-server

## 使用

```python
from warding_pattern_analyzer import WardingPatternAnalyzer

instance = WardingPatternAnalyzer()
await instance.initialize()
health = await instance.health_check()
```
