# M849: MatchTimelineReconstructor

Reconstructs match timelines with event sequencing and state snapshots

## Dependencies

M846, M847

## Interfaces

- `reconstruct_timeline(match_id: str) -> dict`
- `get_events_at_time(match_id: str, timestamp_ms: int) -> list`
- `get_gold_diff_timeline(match_id: str) -> list`
- `get_objective_events(match_id: str) -> list`
- `get_kill_events(match_id: str) -> list`
- `get_item_events(match_id: str, puuid: str) -> list`
- `get_ward_events(match_id: str) -> list`
- `compute_momentum_shifts(match_id: str) -> list`
- `generate_replay_summary(match_id: str) -> dict`
- `compare_timelines(match_ids: list) -> dict`

## Architecture

Follows Seraphine LCU connector patterns with Fiddler network capture.
Network capture preferred over vision for zero hallucination.

## Usage

```python
from match_timeline_reconstructor import MatchTimelineReconstructor

obj = MatchTimelineReconstructor()
print(obj.get_state())  # "ready"
```
