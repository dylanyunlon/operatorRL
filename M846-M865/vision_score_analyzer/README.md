# M857: VisionScoreAnalyzer

Analyzes ward placement patterns, vision score, and map control from historical data

## Dependencies

M846, M849

## Interfaces

- `analyze_vision_score(puuid: str, match_id: str) -> dict`
- `get_ward_placement_heatmap(puuid: str, recent_n: int) -> dict`
- `get_vision_denial_rate(puuid: str) -> float`
- `compare_vision_control(match_id: str) -> dict`
- `get_optimal_ward_spots(role: str, game_time: int) -> list`
- `detect_vision_gaps(match_id: str, team_id: int) -> list`
- `get_control_ward_efficiency(puuid: str) -> dict`
- `analyze_face_check_deaths(puuid: str) -> dict`
- `get_vision_score_percentile(puuid: str, role: str) -> float`
- `export_vision_report(puuid: str) -> str`

## Architecture

Follows Seraphine LCU connector patterns with Fiddler network capture.
Network capture preferred over vision for zero hallucination.

## Usage

```python
from vision_score_analyzer import VisionScoreAnalyzer

obj = VisionScoreAnalyzer()
print(obj.get_state())  # "ready"
```
