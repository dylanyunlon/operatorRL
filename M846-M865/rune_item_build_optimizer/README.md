# M855: RuneItemBuildOptimizer

Optimizes rune pages and item builds using historical win rate data

## Dependencies

M846, M850

## Interfaces

- `get_optimal_runes(champion_id: int, role: str, matchup_id: int) -> dict`
- `get_optimal_build(champion_id: int, role: str, game_state: dict) -> list`
- `get_situational_items(champion_id: int, enemy_comp: list) -> list`
- `analyze_build_efficiency(items: list, champion_id: int) -> dict`
- `get_first_item_spike(champion_id: int, role: str) -> dict`
- `get_boot_recommendation(champion_id: int, context: dict) -> dict`
- `track_build_meta_shifts(champion_id: int, patches: list) -> dict`
- `compare_builds(build_a: list, build_b: list, context: dict) -> dict`
- `get_pro_player_builds(champion_id: int, recent_n: int) -> list`
- `export_build_guide(champion_id: int, role: str) -> str`

## Architecture

Follows Seraphine LCU connector patterns with Fiddler network capture.
Network capture preferred over vision for zero hallucination.

## Usage

```python
from rune_item_build_optimizer import RuneItemBuildOptimizer

obj = RuneItemBuildOptimizer()
print(obj.get_state())  # "ready"
```
