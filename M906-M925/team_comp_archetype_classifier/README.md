# M918: TeamCompArchetypeClassifier

Classify team compositions into archetypes — poke, engage, split, protect, pick — from draft data

## Dependencies

- M906
- M911
- M915

## Architecture

This module follows the Seraphine connector pattern:
- LCU API integration via SeraphineConnectorBridge (M906)
- SGP dual-path fallback for CN/global compatibility
- Fiddler MCP pipeline for network traffic analysis
- TTL-aware caching for performance optimization

## Usage

```python
from team_comp_archetype_classifier import TeamCompArchetypeClassifier

module = TeamCompArchetypeClassifier(connector=bridge)
result = await module.analyze(input_data)
print(result.to_dict())
```

## Reference Projects

- [Seraphine](https://github.com/ljszx/Seraphine) — LCU API patterns
- [LoL Optimizer](https://github.com/oracle-devrel/leagueoflegends-optimizer) — ML pipeline
- [Fiddler MCP](https://www.telerik.com/fiddler/fiddler-everywhere/documentation/mcp-server/fiddler-mcp-server) — Network analysis
