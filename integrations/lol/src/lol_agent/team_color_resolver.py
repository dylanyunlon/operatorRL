"""
Team Color Resolver — Resolve team side (blue/red) and separate teams.
Location: integrations/lol/src/lol_agent/team_color_resolver.py
Reference: Seraphine/app/lol/tools.py: getTeamColor, separateTeams
"""
from __future__ import annotations
import logging, time
from typing import Any, Callable, Optional
logger = logging.getLogger(__name__)
_EVOLUTION_KEY: str = "integrations.lol.team_color_resolver.v1"

class TeamColorResolver:
    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def resolve_color(self, session: dict[str, Any], current_puuid: str) -> str:
        game_data = session.get("gameData", {})
        for p in game_data.get("teamOne", []):
            if p.get("puuid") == current_puuid:
                return "blue"
        for p in game_data.get("teamTwo", []):
            if p.get("puuid") == current_puuid:
                return "red"
        return "unknown"

    def separate_teams(self, participants: list[dict[str, Any]], current_puuid: str) -> dict[str, list[dict[str, Any]]]:
        my_team_id = None
        for p in participants:
            if p.get("puuid") == current_puuid:
                my_team_id = p.get("teamId")
                break
        if my_team_id is None:
            return {"allies": [], "enemies": []}
        return {"allies": [p for p in participants if p.get("teamId") == my_team_id],
                "enemies": [p for p in participants if p.get("teamId") != my_team_id]}

    def team_id_to_color(self, team_id: int) -> str:
        if team_id == 100:
            return "blue"
        elif team_id == 200:
            return "red"
        return "unknown"

    def is_ally(self, puuid_a: str, puuid_b: str, participants: list[dict[str, Any]]) -> bool:
        team_a = team_b = None
        for p in participants:
            if p.get("puuid") == puuid_a:
                team_a = p.get("teamId")
            if p.get("puuid") == puuid_b:
                team_b = p.get("teamId")
        if team_a is None or team_b is None:
            return False
        return team_a == team_b

    def _fire_evolution(self, data: dict[str, Any]) -> None:
        if self.evolution_callback is None:
            return
        enriched = {"module": "team_color_resolver", "timestamp": time.time(), **data}
        try:
            self.evolution_callback(enriched)
        except Exception as exc:
            logger.warning("Evolution callback error: %s", exc)
