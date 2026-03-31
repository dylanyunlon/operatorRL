# M847: HistoricalMatchCrawler

Crawls historical match data via Riot API and LCU, following Seraphine connector patterns

## Dependencies

M846

## Interfaces

- `connect_lcu(port: int, token: str) -> bool`
- `fetch_match_history(puuid: str, count: int, queue_id: int) -> list`
- `fetch_match_detail(match_id: str) -> dict`
- `fetch_match_timeline(match_id: str) -> dict`
- `batch_crawl(puuids: list, depth: int) -> dict`
- `store_matches(matches: list, storage_path: str) -> int`
- `get_crawl_progress() -> dict`
- `resume_crawl(checkpoint: str) -> bool`
- `validate_api_key(api_key: str) -> bool`
- `get_rate_limit_status() -> dict`

## Architecture

Follows Seraphine LCU connector patterns with Fiddler network capture.
Network capture preferred over vision for zero hallucination.

## Usage

```python
from historical_match_crawler import HistoricalMatchCrawler

obj = HistoricalMatchCrawler()
print(obj.get_state())  # "ready"
```
