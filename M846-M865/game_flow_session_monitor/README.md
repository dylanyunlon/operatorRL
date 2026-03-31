# M854: GameFlowSessionMonitor

Monitors LCU game flow session states in real-time (lobby, champ select, in-game)

## Dependencies

M846, M847

## Interfaces

- `start_monitoring() -> bool`
- `stop_monitoring() -> bool`
- `get_current_phase() -> str`
- `get_lobby_info() -> dict`
- `get_champ_select_state() -> dict`
- `get_in_game_state() -> dict`
- `register_phase_callback(phase: str, callback) -> str`
- `unregister_callback(callback_id: str) -> bool`
- `get_session_history() -> list`
- `is_in_game() -> bool`

## Architecture

Follows Seraphine LCU connector patterns with Fiddler network capture.
Network capture preferred over vision for zero hallucination.

## Usage

```python
from game_flow_session_monitor import GameFlowSessionMonitor

obj = GameFlowSessionMonitor()
print(obj.get_state())  # "ready"
```
