# M917: ObjectiveControlProfiler

Profile opponent objective control habits — dragon priority, herald usage, baron timing patterns

## Dependencies

- M906
- M908
- M916

## Architecture

This module follows the Seraphine connector pattern:
- LCU API integration via SeraphineConnectorBridge (M906)
- SGP dual-path fallback for CN/global compatibility
- Fiddler MCP pipeline for network traffic analysis
- TTL-aware caching for performance optimization

## Usage

```python
from objective_control_profiler import ObjectiveControlProfiler

module = ObjectiveControlProfiler(connector=bridge)
result = await module.analyze(input_data)
print(result.to_dict())
```

## Reference Projects

- [Seraphine](https://github.com/ljszx/Seraphine) — LCU API patterns
- [LoL Optimizer](https://github.com/oracle-devrel/leagueoflegends-optimizer) — ML pipeline
- [Fiddler MCP](https://www.telerik.com/fiddler/fiddler-everywhere/documentation/mcp-server/fiddler-mcp-server) — Network analysis
