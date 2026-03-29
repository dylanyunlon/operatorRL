"""
Meta Shift Tracker — Track patch-level meta shifts in history data.
Location: integrations/lol-history/src/lol_history/meta_shift_tracker.py
Reference: leagueoflegends-optimizer patch data, Seraphine match history
"""
from __future__ import annotations
import logging, time
from collections import defaultdict, OrderedDict
from typing import Any, Callable, Optional
logger = logging.getLogger(__name__)
_EVOLUTION_KEY: str = "lol_history.meta_shift_tracker.v1"

class MetaShiftTracker:
    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def track(self, matches: list[dict[str, Any]]) -> dict[str, Any]:
        if not matches:
            self._fire("track_empty", {})
            return {"shifts": [], "patch_stats": {}, "trending_up": [], "trending_down": []}

        # Group by patch
        by_patch: dict[str, list[dict]] = defaultdict(list)
        for m in matches:
            by_patch[m.get("patch", "unknown")].append(m)

        # Sort patches
        sorted_patches = sorted(by_patch.keys())
        patch_stats: OrderedDict[str, dict[str, Any]] = OrderedDict()

        for patch in sorted_patches:
            pms = by_patch[patch]
            champ_stats: dict[str, dict] = defaultdict(lambda: {"games": 0, "wins": 0})
            for m in pms:
                c = m.get("champion", "unknown")
                champ_stats[c]["games"] += 1
                if m.get("win"):
                    champ_stats[c]["wins"] += 1
            for c in champ_stats:
                g = champ_stats[c]["games"]
                champ_stats[c]["winrate"] = champ_stats[c]["wins"] / g if g > 0 else 0
                champ_stats[c]["pickrate"] = g / len(pms) if pms else 0
            patch_stats[patch] = {"total_games": len(pms), "champion_stats": dict(champ_stats)}

        # Detect shifts between consecutive patches
        shifts = []
        patches_list = list(sorted_patches)
        if len(patches_list) >= 2:
            for i in range(1, len(patches_list)):
                prev_p, curr_p = patches_list[i - 1], patches_list[i]
                prev_cs = patch_stats[prev_p]["champion_stats"]
                curr_cs = patch_stats[curr_p]["champion_stats"]
                all_champs = set(prev_cs.keys()) | set(curr_cs.keys())
                for c in all_champs:
                    prev_wr = prev_cs.get(c, {}).get("winrate", 0)
                    curr_wr = curr_cs.get(c, {}).get("winrate", 0)
                    prev_pr = prev_cs.get(c, {}).get("pickrate", 0)
                    curr_pr = curr_cs.get(c, {}).get("pickrate", 0)
                    wr_delta = curr_wr - prev_wr
                    pr_delta = curr_pr - prev_pr
                    if abs(wr_delta) > 0.05 or abs(pr_delta) > 0.1:
                        shifts.append({"champion": c, "from_patch": prev_p, "to_patch": curr_p,
                                       "winrate_delta": wr_delta, "pickrate_delta": pr_delta})

        # Trending analysis: compare game count growth across patches
        trending_up, trending_down = [], []
        if len(patches_list) >= 2:
            first_cs = patch_stats[patches_list[0]]["champion_stats"]
            last_cs = patch_stats[patches_list[-1]]["champion_stats"]
            all_c = set(first_cs.keys()) | set(last_cs.keys())
            for c in all_c:
                first_games = first_cs.get(c, {}).get("games", 0)
                last_games = last_cs.get(c, {}).get("games", 0)
                first_pr = first_cs.get(c, {}).get("pickrate", 0)
                last_pr = last_cs.get(c, {}).get("pickrate", 0)
                # Trending up: pickrate increase OR game count increase (2x+)
                if last_pr > first_pr + 0.1 or (first_games > 0 and last_games >= first_games * 2):
                    trending_up.append(c)
                elif first_pr > last_pr + 0.1 or (last_games == 0 and first_games > 0) or (first_games > 0 and last_games > 0 and last_games <= first_games * 0.5):
                    trending_down.append(c)

        self._fire("track_complete", {"patches": len(sorted_patches), "shifts": len(shifts)})
        return {"shifts": shifts, "patch_stats": dict(patch_stats),
                "trending_up": trending_up, "trending_down": trending_down}

    def _fire(self, t: str, d: dict) -> None:
        if self.evolution_callback:
            self.evolution_callback({"type": t, "key": _EVOLUTION_KEY, "timestamp": time.time(), **d})
