# M988: OpponentProfileBuilder

## 对手画像构建器

Build comprehensive opponent profiles from historical matches — champion pool depth, role preference, playstyle classification (aggressive/passive/roam-heavy), win streaks

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
from opponent_profile_builder import OpponentProfileBuilder

instance = OpponentProfileBuilder(connector=my_connector)
await instance.initialize()
results = await instance.analyze(players)
await instance.shutdown()
```

## Integration

- M906 SeraphineConnectorBridge 提供数据源
- M866-M885 实时系统消费分析结果
- M926-M945 预测层叠加历史情报
- Fiddler MCP Server 提供网络抓包数据
