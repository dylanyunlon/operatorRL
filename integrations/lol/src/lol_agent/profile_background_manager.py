"""
Profile Background Manager — Manage summoner profile background and status.
Location: integrations/lol/src/lol_agent/profile_background_manager.py
Reference: Seraphine/app/lol/connector.py: setProfileBackground, setOnlineStatus, setTierShowed
"""
from __future__ import annotations
import logging, time
from typing import Any, Callable, Optional
logger = logging.getLogger(__name__)
_EVOLUTION_KEY: str = "integrations.lol.profile_background_manager.v1"

class ProfileBackgroundManager:
    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def build_set_background_payload(self, skin_id: int) -> dict[str, Any]:
        return {"key": "backgroundSkinId", "value": skin_id}

    def build_set_status_payload(self, message: str) -> dict[str, Any]:
        return {"statusMessage": message}

    def build_set_tier_payload(self, queue: str, tier: str, division: str) -> dict[str, Any]:
        return {"lol": {"rankedLeagueQueue": queue, "rankedLeagueTier": tier, "rankedLeagueDivision": division}}

    def recommend_background(self, mastery: list[dict[str, Any]]) -> Optional[int]:
        if not mastery:
            return None
        best = max(mastery, key=lambda m: m.get("championPoints", 0))
        return best.get("championId", 0) * 1000

    def auto_status_message(self, tier: str, division: str, lp: int, streak: str = "") -> str:
        tier_display = tier.capitalize() if tier else "Unranked"
        msg = f"{tier_display} {division} | {lp} LP"
        if streak:
            msg += f" | {streak}"
        return msg

    def lcu_url_for_background(self) -> str:
        return "/lol-summoner/v1/current-summoner/summoner-profile"

    def lcu_url_for_status(self) -> str:
        return "/lol-chat/v1/me"

    def validate_skin_id(self, skin_id: int) -> bool:
        return isinstance(skin_id, int) and skin_id > 0

    def _fire_evolution(self, data: dict[str, Any]) -> None:
        if self.evolution_callback is None:
            return
        enriched = {"module": "profile_background_manager", "timestamp": time.time(), **data}
        try:
            self.evolution_callback(enriched)
        except Exception as exc:
            logger.warning("Evolution callback error: %s", exc)
