# M976: MomentumShiftDetector

## 概述

局势转换检测器 — 历史对局中的翻盘/滚雪球模式识别，基于金币曲线+经验曲线+目标控制的局势转折点定位

## 依赖

M906, M908, M912, M966

## 架构模式

查看 Seraphine/app/lol/connector.py 上现有 LCU API connector 的实现方式,
理解其模式, 特别是 retry 装饰器和 PastRequest 是如何与 HTTP session 分离的。
从 connector.needLcu + retry 这个好例子开始。
然后, 遵循该模式实现 MomentumShiftDetector。

## 参考

- Seraphine: github.com/ljszx/Seraphine
- operatorRL: github.com/dylanyunlon/operatorRL.git
- LoL Optimizer: github.com/oracle-devrel/leagueoflegends-optimizer
- Fiddler MCP: telerik.com/fiddler/fiddler-everywhere/documentation/mcp-server

## 使用

```python
from momentum_shift_detector import MomentumShiftDetector

instance = MomentumShiftDetector()
await instance.initialize()
health = await instance.health_check()
```
