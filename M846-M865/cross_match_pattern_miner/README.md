# M860: CrossMatchPatternMiner

Mines patterns across multiple matches to identify trends and anomalies

## Dependencies

M846, M847, M849

## Interfaces

- `mine_patterns(puuid: str, match_ids: list) -> dict`
- `find_recurring_mistakes(puuid: str) -> list`
- `detect_improvement_trends(puuid: str) -> dict`
- `get_power_spike_patterns(puuid: str, champion_id: int) -> dict`
- `analyze_loss_conditions(puuid: str, recent_n: int) -> dict`
- `find_win_conditions(puuid: str, recent_n: int) -> dict`
- `cluster_game_outcomes(puuid: str) -> dict`
- `get_consistency_score(puuid: str) -> float`
- `detect_meta_adaptation(puuid: str) -> dict`
- `export_pattern_report(puuid: str) -> str`

## Architecture

Follows Seraphine LCU connector patterns with Fiddler network capture.
Network capture preferred over vision for zero hallucination.

## Usage

```python
from cross_match_pattern_miner import CrossMatchPatternMiner

obj = CrossMatchPatternMiner()
print(obj.get_state())  # "ready"
```
