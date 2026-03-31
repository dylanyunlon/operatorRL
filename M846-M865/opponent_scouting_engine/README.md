# M852: OpponentScoutingEngine

Scouts opponents by mining their historical match data for patterns and weaknesses

## Dependencies

M846, M847, M848

## Interfaces

- `scout_opponent(puuid: str) -> dict`
- `get_champion_pool(puuid: str) -> list`
- `detect_patterns(puuid: str, recent_n: int) -> dict`
- `find_weaknesses(puuid: str) -> list`
- `get_lane_tendencies(puuid: str, role: str) -> dict`
- `predict_champion_pick(puuid: str, context: dict) -> list`
- `analyze_death_patterns(puuid: str) -> dict`
- `get_roaming_frequency(puuid: str) -> float`
- `scout_team(puuids: list) -> dict`
- `generate_scouting_report(puuid: str) -> str`

## Architecture

Follows Seraphine LCU connector patterns with Fiddler network capture.
Network capture preferred over vision for zero hallucination.

## Usage

```python
from opponent_scouting_engine import OpponentScoutingEngine

obj = OpponentScoutingEngine()
print(obj.get_state())  # "ready"
```
