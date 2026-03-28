# LoL History — Historical Battle Data for Pre-Game Intelligence

Retrieves and analyzes historical match data from LoL's LCU API and SGP endpoints,
inspired by [Seraphine](https://github.com/ljszx/Seraphine)'s connector architecture.

## Why Historical Data Matters

> Historical battle data of opponents is critical for live game decision-making.
> Knowing an opponent's preferred champions, playstyle, and weaknesses enables
> adaptive strategy that a purely real-time system cannot achieve.

## Architecture

```
LCU API (/lol-match-history/v1/...)
    ↕ HTTP/JSON
HistoryClient (URL builders + response parsers)
    ↕ normalized dicts
MatchAnalyzer (winrate, KDA, streaks, roles)
    ↕ statistics
PlayerProfiler (threat level, weaknesses, playstyle)
    ↕ profile
Pre-Game Report → Strategy Agent
```

## Seraphine Endpoints Used

| Endpoint | Method | Data |
|----------|--------|------|
| `/lol-match-history/v1/products/lol/{puuid}/matches` | Match list | Recent games |
| `/lol-match-history/v1/games/{gameId}` | Game detail | Full stats |
| `/lol-ranked/v1/ranked-stats/{puuid}` | Ranked stats | Tier/LP |
| `/match-history-query/v1/products/lol/player/{puuid}/SUMMARY` | SGP matches | Extended history |

## Quick Start

```python
from lol_history import HistoryClient
from lol_history.match_analyzer import MatchAnalyzer
from lol_history.player_profiler import PlayerProfiler

client = HistoryClient()
analyzer = MatchAnalyzer()
profiler = PlayerProfiler()

# Parse raw API response
matches = client.parse_match_list(raw_response)
profile = profiler.build_profile(puuid="abc-123", matches=matches)
report = profiler.to_pre_game_report(profile)
```
