# M861: RealtimeStrategyRecommender

Real-time strategy recommendations based on game state and historical patterns

## Dependencies

M846, M851, M858, M860

## Interfaces

- `get_recommendation(game_state: dict) -> dict`
- `get_lane_advice(game_state: dict, role: str) -> dict`
- `get_macro_advice(game_state: dict) -> dict`
- `get_teamfight_advice(game_state: dict) -> dict`
- `evaluate_current_decision(game_state: dict, action: str) -> dict`
- `get_split_push_value(game_state: dict) -> float`
- `get_roam_timing(game_state: dict, role: str) -> dict`
- `predict_enemy_strategy(game_state: dict) -> dict`
- `get_comeback_strategy(game_state: dict) -> dict`
- `export_strategy_log(session_id: str) -> str`

## Architecture

Follows Seraphine LCU connector patterns with Fiddler network capture.
Network capture preferred over vision for zero hallucination.

## Usage

```python
from realtime_strategy_recommender import RealtimeStrategyRecommender

obj = RealtimeStrategyRecommender()
print(obj.get_state())  # "ready"
```
