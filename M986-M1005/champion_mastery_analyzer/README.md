# M989: ChampionMasteryAnalyzer

## 英雄熟练度分析器

Deep analysis of each player's champion mastery — games played, win rate, KDA trend, comfort picks vs meta picks, one-trick detection, champion pool breadth scoring

## Dependencies

M906, M987

## Architecture

遵循 Seraphine/app/lol/connector.py 的模式:
- `PastRequest` 审计每一次 API 调用
- `@retry` 装饰器实现指数退避重试
- `@need_initialized` 守卫确保模块就绪
- `TTLCache` LRU+TTL 缓存层
- `asyncio.Semaphore` 并发控制
- `ConnectorProtocol` 鸭子类型桥接 M906

## Usage

```python
from champion_mastery_analyzer import ChampionMasteryAnalyzer

instance = ChampionMasteryAnalyzer(connector=my_connector)
await instance.initialize()
results = await instance.analyze(players)
await instance.shutdown()
```

## Integration

- M906 SeraphineConnectorBridge 提供数据源
- M866-M885 实时系统消费分析结果
- M926-M945 预测层叠加历史情报
- Fiddler MCP Server 提供网络抓包数据
