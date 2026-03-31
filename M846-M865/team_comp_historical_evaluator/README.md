# M851: TeamCompHistoricalEvaluator

Evaluates team compositions using historical win rate data and synergy analysis

## Dependencies

M846, M850

## Interfaces

- `evaluate_composition(ally_ids: list, enemy_ids: list) -> dict`
- `get_synergy_score(champion_ids: list) -> float`
- `get_comp_archetype(champion_ids: list) -> str`
- `predict_early_game_strength(ally_ids: list, enemy_ids: list) -> float`
- `predict_late_game_strength(ally_ids: list, enemy_ids: list) -> float`
- `suggest_flex_picks(current_picks: list, bans: list) -> list`
- `analyze_win_conditions(ally_ids: list, enemy_ids: list) -> dict`
- `get_historical_comps(archetype: str, min_games: int) -> list`
- `compare_comps(comp_a: list, comp_b: list) -> dict`
- `generate_draft_report(ally_ids: list, enemy_ids: list) -> str`

## Architecture

Follows Seraphine LCU connector patterns with Fiddler network capture.
Network capture preferred over vision for zero hallucination.

## Usage

```python
from team_comp_historical_evaluator import TeamCompHistoricalEvaluator

obj = TeamCompHistoricalEvaluator()
print(obj.get_state())  # "ready"
```
