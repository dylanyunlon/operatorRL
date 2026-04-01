# M1005: HistoricalIntelOrchestrator

## 历史情报编排器

Top-level orchestrator — detects game flow phase (lobby→champ_select→loading→in_game), triggers appropriate historical data acquisition pipelines, coordinates all M986-M1004 modules, serves final intelligence to M866-M885 real-time system and M926-M945 predictive layer

## Dependencies

M906, M986, M987, M1002, M1003, M1004

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
from historical_intel_orchestrator import HistoricalIntelOrchestrator

instance = HistoricalIntelOrchestrator(connector=my_connector)
await instance.initialize()
results = await instance.analyze(players)
await instance.shutdown()
```

## Integration

- M906 SeraphineConnectorBridge 提供数据源
- M866-M885 实时系统消费分析结果
- M926-M945 预测层叠加历史情报
- Fiddler MCP Server 提供网络抓包数据
