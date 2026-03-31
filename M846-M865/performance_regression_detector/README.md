# M863: PerformanceRegressionDetector

Detects performance regressions in player stats over time with alerting

## Dependencies

M846, M860

## Interfaces

- `detect_regressions(puuid: str, window: int) -> list`
- `get_kda_trend(puuid: str, recent_n: int) -> dict`
- `get_cs_trend(puuid: str, recent_n: int) -> dict`
- `get_vision_trend(puuid: str, recent_n: int) -> dict`
- `detect_tilt(puuid: str) -> dict`
- `get_performance_baseline(puuid: str) -> dict`
- `compare_to_baseline(puuid: str, match_id: str) -> dict`
- `get_improvement_suggestions(puuid: str) -> list`
- `set_regression_thresholds(thresholds: dict) -> bool`
- `export_performance_report(puuid: str) -> str`

## Architecture

Follows Seraphine LCU connector patterns with Fiddler network capture.
Network capture preferred over vision for zero hallucination.

## Usage

```python
from performance_regression_detector import PerformanceRegressionDetector

obj = PerformanceRegressionDetector()
print(obj.get_state())  # "ready"
```
