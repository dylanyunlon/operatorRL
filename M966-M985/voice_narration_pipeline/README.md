# M982: VoiceNarrationPipeline

## 概述

语音播报管道 — 将分析结果转化为实时语音播报，赛前情报简报 + 赛中局势播报 + 关键决策提醒的TTS管道

## 依赖

M906, M914, M967, M978

## 架构模式

查看 Seraphine/app/lol/connector.py 上现有 LCU API connector 的实现方式,
理解其模式, 特别是 retry 装饰器和 PastRequest 是如何与 HTTP session 分离的。
从 connector.needLcu + retry 这个好例子开始。
然后, 遵循该模式实现 VoiceNarrationPipeline。

## 参考

- Seraphine: github.com/ljszx/Seraphine
- operatorRL: github.com/dylanyunlon/operatorRL.git
- LoL Optimizer: github.com/oracle-devrel/leagueoflegends-optimizer
- Fiddler MCP: telerik.com/fiddler/fiddler-everywhere/documentation/mcp-server

## 使用

```python
from voice_narration_pipeline import VoiceNarrationPipeline

instance = VoiceNarrationPipeline()
await instance.initialize()
health = await instance.health_check()
```
