# M856: BanPickSuggestionEngine

Suggests bans and picks based on team composition, meta, and opponent history

## Dependencies

M846, M850, M851, M852

## Interfaces

- `suggest_bans(context: dict) -> list`
- `suggest_picks(context: dict) -> list`
- `get_must_ban_list(patch: str, elo: str) -> list`
- `analyze_draft_phase(picks: list, bans: list) -> dict`
- `get_comfort_picks(puuid: str, available: list) -> list`
- `evaluate_pick_order(pick_order: list) -> dict`
- `simulate_draft(ally_bans: list, enemy_bans: list) -> dict`
- `get_flex_pick_value(champion_id: int) -> float`
- `generate_draft_strategy(context: dict) -> dict`
- `export_draft_plan(context: dict) -> str`

## Architecture

Follows Seraphine LCU connector patterns with Fiddler network capture.
Network capture preferred over vision for zero hallucination.

## Usage

```python
from ban_pick_suggestion_engine import BanPickSuggestionEngine

obj = BanPickSuggestionEngine()
print(obj.get_state())  # "ready"
```
