# M910: OpponentProfileBuilder

Build comprehensive opponent profiles from match history + ranked stats — playstyle classification

## Dependencies

- M906
- M907
- M908
- M909

## Architecture

This module follows the Seraphine connector pattern:
- LCU API integration via SeraphineConnectorBridge (M906)
- SGP dual-path fallback for CN/global compatibility
- Fiddler MCP pipeline for network traffic analysis
- TTL-aware caching for performance optimization

## Usage

```python
from opponent_profile_builder import OpponentProfileBuilder

module = OpponentProfileBuilder(connector=bridge)
result = await module.analyze(input_data)
print(result.to_dict())
```

## Reference Projects

- [Seraphine](https://github.com/ljszx/Seraphine) — LCU API patterns
- [LoL Optimizer](https://github.com/oracle-devrel/leagueoflegends-optimizer) — ML pipeline
- [Fiddler MCP](https://www.telerik.com/fiddler/fiddler-everywhere/documentation/mcp-server/fiddler-mcp-server) — Network analysis
