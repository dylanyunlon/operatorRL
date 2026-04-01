# M986: LiveMatchPlayerResolver

## 实时对局玩家解析器

Resolve all 10 players in current match via LCU gameflow-session + champ-select WebSocket, extract puuid/summonerId/championId for downstream historical queries

## Dependencies

M906, M907

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
from live_match_player_resolver import LiveMatchPlayerResolver

instance = LiveMatchPlayerResolver(connector=my_connector)
await instance.initialize()
results = await instance.analyze(players)
await instance.shutdown()
```

## Integration

- M906 SeraphineConnectorBridge 提供数据源
- M866-M885 实时系统消费分析结果
- M926-M945 预测层叠加历史情报
- Fiddler MCP Server 提供网络抓包数据
