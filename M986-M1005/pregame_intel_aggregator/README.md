# M1002: PregameIntelAggregator

## 赛前情报聚合器

Aggregate all historical intelligence into a single pre-game briefing — threat assessment per opponent, team composition analysis, win condition identification, key matchup highlights, recommended bans based on opponent history

## Dependencies

M906, M986, M988, M989, M990, M991, M994

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
from pregame_intel_aggregator import PregameIntelAggregator

instance = PregameIntelAggregator(connector=my_connector)
await instance.initialize()
results = await instance.analyze(players)
await instance.shutdown()
```

## Integration

- M906 SeraphineConnectorBridge 提供数据源
- M866-M885 实时系统消费分析结果
- M926-M945 预测层叠加历史情报
- Fiddler MCP Server 提供网络抓包数据
