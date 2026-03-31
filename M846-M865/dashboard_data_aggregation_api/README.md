# M864: DashboardDataAggregationAPI

REST API layer aggregating all module data for the real-time dashboard

## Dependencies

M846, M847, M848, M849, M850

## Interfaces

- `start_server(host: str, port: int) -> bool`
- `stop_server() -> bool`
- `register_data_source(name: str, provider) -> bool`
- `get_dashboard_state() -> dict`
- `get_summoner_card(puuid: str) -> dict`
- `get_match_overview(match_id: str) -> dict`
- `get_live_game_data() -> dict`
- `websocket_broadcast(event: str, data: dict) -> int`
- `get_api_metrics() -> dict`
- `export_snapshot(format: str) -> str`

## Architecture

Follows Seraphine LCU connector patterns with Fiddler network capture.
Network capture preferred over vision for zero hallucination.

## Usage

```python
from dashboard_data_aggregation_api import DashboardDataAggregationAPI

obj = DashboardDataAggregationAPI()
print(obj.get_state())  # "ready"
```
