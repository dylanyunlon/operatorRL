# M983: TrainingDataExporter

## 概述

训练数据导出器 — 将历史分析结果转化为RL训练三元组，state-action-reward格式导出 + AgentLightning训练循环对接

## 依赖

M906, M908, M966, M979

## 架构模式

查看 Seraphine/app/lol/connector.py 上现有 LCU API connector 的实现方式,
理解其模式, 特别是 retry 装饰器和 PastRequest 是如何与 HTTP session 分离的。
从 connector.needLcu + retry 这个好例子开始。
然后, 遵循该模式实现 TrainingDataExporter。

## 参考

- Seraphine: github.com/ljszx/Seraphine
- operatorRL: github.com/dylanyunlon/operatorRL.git
- LoL Optimizer: github.com/oracle-devrel/leagueoflegends-optimizer
- Fiddler MCP: telerik.com/fiddler/fiddler-everywhere/documentation/mcp-server

## 使用

```python
from training_data_exporter import TrainingDataExporter

instance = TrainingDataExporter()
await instance.initialize()
health = await instance.health_check()
```
