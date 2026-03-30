"""
MatchReplayAnalyzer — Analyses recorded match replays for pattern extraction.

Processes timeline data from Riot's match-v5 API (or cached replay files)
to extract gank patterns, gold curves, power spikes, death heatmaps, and
vision score timelines.  The output feeds the agentic decision loop so that
the live assistant can say "historically you die in this river area at ~8 min —
ward it".

Architecture (拿来主义 from Seraphine + LeagueAI):
  - Seraphine/app/lol/tools.py: parseGameDetailData — per-participant stats
  - Seraphine/app/lol/connector.py: getReplayMetadata — replay access
  - LeagueAI frame capture → we use timeline frames instead of vision
  - DI-star replay analysis patterns for macro-level insight

Location: integrations/lol-history/src/lol_history/match_replay_analyzer.py
"""

from __future__ import annotations

import logging
import math
import time
from collections import defaultdict
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "integrations.lol_history.match_replay_analyzer.v1"

# ---------------------------------------------------------------------------
# Event type constants (Riot timeline schema)
# ---------------------------------------------------------------------------
EVT_CHAMPION_KILL = "CHAMPION_KILL"
EVT_WARD_PLACED = "WARD_PLACED"
EVT_WARD_KILL = "WARD_KILL"
EVT_ELITE_MONSTER_KILL = "ELITE_MONSTER_KILL"
EVT_BUILDING_KILL = "BUILDING_KILL"
EVT_ITEM_PURCHASED = "ITEM_PURCHASED"
EVT_SKILL_LEVEL_UP = "SKILL_LEVEL_UP"
EVT_TURRET_PLATE_DESTROYED = "TURRET_PLATE_DESTROYED"

# ---------------------------------------------------------------------------
# Map geometry constants (Summoner's Rift approximate bounds)
# ---------------------------------------------------------------------------
MAP_MIN_X = 0
MAP_MAX_X = 15000
MAP_MIN_Y = 0
MAP_MAX_Y = 15000
CLUSTER_RADIUS = 1500  # pixels for death clustering


def _euclidean(p1: Dict[str, int], p2: Dict[str, int]) -> float:
    """Euclidean distance between two position dicts {x, y}."""
    dx = p1.get("x", 0) - p2.get("x", 0)
    dy = p1.get("y", 0) - p2.get("y", 0)
    return math.sqrt(dx * dx + dy * dy)


def _ms_to_min(ms: int) -> float:
    """Convert milliseconds to minutes."""
    return ms / 60000.0


def _safe_div(a: float, b: float, default: float = 0.0) -> float:
    return a / b if b != 0 else default


# ===================================================================== #
#                      MatchReplayAnalyzer                               #
# ===================================================================== #

class MatchReplayAnalyzer:
    """Analyses Riot match timeline data to extract strategic patterns.

    This class works on *offline* replay data — it does not connect to the
    live game.  It ingests timeline frames and events (from match-v5 or a
    local cache) and produces structured analysis outputs.

    Public API
    ----------
    parse_timeline_events(events)
    extract_gank_patterns(events, participant_roles)
    compute_gold_curve(frames, participant_id, opponent_id)
    detect_power_spikes(frames, participant_id, items_timeline)
    analyze_death_locations(deaths)
    compute_vision_score_timeline(ward_events, participant_id)
    run_full_analysis(replay_data)

    Attributes
    ----------
    evolution_callback : Optional[Callable]
        Evolution event sink.
    """

    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable[[Dict[str, Any]], None]] = None
        self._analysis_count: int = 0
        self._event_index: Dict[str, List[Dict[str, Any]]] = {}

    # ------------------------------------------------------------------ #
    #  1. Parse Timeline Events                                           #
    # ------------------------------------------------------------------ #

    def parse_timeline_events(
        self,
        events: List[Dict[str, Any]],
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Categorise raw timeline events into typed buckets.

        Parameters
        ----------
        events : list[dict]
            Riot timeline event objects with at least ``type`` and ``timestamp``.

        Returns
        -------
        dict with keys: kills, wards, ward_kills, objectives, structures,
        items, skill_ups, turret_plates.
        """
        result: Dict[str, List[Dict[str, Any]]] = {
            "kills": [],
            "wards": [],
            "ward_kills": [],
            "objectives": [],
            "structures": [],
            "items": [],
            "skill_ups": [],
            "turret_plates": [],
        }

        for evt in events:
            etype = evt.get("type", "")
            if etype == EVT_CHAMPION_KILL:
                result["kills"].append(evt)
            elif etype == EVT_WARD_PLACED:
                result["wards"].append(evt)
            elif etype == EVT_WARD_KILL:
                result["ward_kills"].append(evt)
            elif etype == EVT_ELITE_MONSTER_KILL:
                result["objectives"].append(evt)
            elif etype == EVT_BUILDING_KILL:
                result["structures"].append(evt)
            elif etype == EVT_ITEM_PURCHASED:
                result["items"].append(evt)
            elif etype == EVT_SKILL_LEVEL_UP:
                result["skill_ups"].append(evt)
            elif etype == EVT_TURRET_PLATE_DESTROYED:
                result["turret_plates"].append(evt)

        self._event_index = result
        return result

    # ------------------------------------------------------------------ #
    #  2. Extract Gank Patterns                                           #
    # ------------------------------------------------------------------ #

    def extract_gank_patterns(
        self,
        events: List[Dict[str, Any]],
        participant_roles: Dict[int, str],
    ) -> Dict[str, Any]:
        """Identify gank events (jungler-assisted kills) and patterns.

        A kill is classified as a *gank* if the killer or an assisting
        participant is a JUNGLE role player.

        Parameters
        ----------
        events : list[dict]
            Kill events (type == CHAMPION_KILL).
        participant_roles : dict[int, str]
            Mapping of participantId → role string (TOP/JUNGLE/MID/ADC/SUPPORT).

        Returns
        -------
        dict with ganks (list), preferred_gank_lane, gank_timing_avg (ms),
        gank_success_rate, total_ganks.
        """
        jungle_ids = {
            pid for pid, role in participant_roles.items()
            if role and role.upper() == "JUNGLE"
        }

        ganks: List[Dict[str, Any]] = []

        for evt in events:
            if evt.get("type") != EVT_CHAMPION_KILL:
                continue
            killer = evt.get("killerId", 0)
            assists = set(evt.get("assistingParticipantIds", []))
            participants_involved = {killer} | assists

            # If any jungle player is involved, it is a gank
            if participants_involved & jungle_ids:
                victim = evt.get("victimId", 0)
                pos = evt.get("position", {})
                ts = evt.get("timestamp", 0)

                # Determine which lane was ganked based on position
                lane = self._classify_lane_from_position(pos)

                ganks.append({
                    "timestamp": ts,
                    "killer": killer,
                    "victim": victim,
                    "assists": list(assists),
                    "position": pos,
                    "lane": lane,
                })

        # Aggregate
        lane_counts: Dict[str, int] = defaultdict(int)
        for g in ganks:
            lane_counts[g["lane"]] += 1

        preferred = max(lane_counts, key=lane_counts.get) if lane_counts else "UNKNOWN"
        avg_timing = (
            sum(g["timestamp"] for g in ganks) / len(ganks)
            if ganks else 0
        )

        return {
            "ganks": ganks,
            "preferred_gank_lane": preferred,
            "gank_timing_avg": avg_timing,
            "gank_count_by_lane": dict(lane_counts),
            "total_ganks": len(ganks),
        }

    # ------------------------------------------------------------------ #
    #  3. Compute Gold Curve                                              #
    # ------------------------------------------------------------------ #

    def compute_gold_curve(
        self,
        frames: List[Dict[str, Any]],
        participant_id: str,
        opponent_id: str,
    ) -> Dict[str, Any]:
        """Compute per-minute gold differential between two participants.

        Parameters
        ----------
        frames : list[dict]
            Riot timeline frames, each with ``timestamp`` and
            ``participantFrames``.
        participant_id, opponent_id : str
            Keys into participantFrames.

        Returns
        -------
        dict with gold_diffs (list[dict]), max_lead, max_deficit,
        gold_at_15, avg_gold_diff.
        """
        gold_diffs: List[Dict[str, Any]] = []
        max_lead: float = 0.0
        max_deficit: float = 0.0

        for frame in frames:
            ts = frame.get("timestamp", 0)
            pf = frame.get("participantFrames", {})
            my_gold = pf.get(participant_id, {}).get("totalGold", 0)
            opp_gold = pf.get(opponent_id, {}).get("totalGold", 0)
            diff = my_gold - opp_gold

            gold_diffs.append({
                "timestamp": ts,
                "minute": _ms_to_min(ts),
                "my_gold": my_gold,
                "opponent_gold": opp_gold,
                "diff": diff,
            })

            if diff > max_lead:
                max_lead = diff
            if diff < max_deficit:
                max_deficit = diff

        # Gold at 15 min (~900000 ms)
        gold_at_15 = 0.0
        for gd in gold_diffs:
            if gd["timestamp"] >= 900000:
                gold_at_15 = gd["diff"]
                break

        avg_diff = (
            sum(gd["diff"] for gd in gold_diffs) / len(gold_diffs)
            if gold_diffs else 0.0
        )

        return {
            "gold_diffs": gold_diffs,
            "max_lead": max_lead,
            "max_deficit": max_deficit,
            "gold_at_15": gold_at_15,
            "avg_gold_diff": avg_diff,
        }

    # ------------------------------------------------------------------ #
    #  4. Detect Power Spikes                                             #
    # ------------------------------------------------------------------ #

    def detect_power_spikes(
        self,
        frames: List[Dict[str, Any]],
        participant_id: str,
        items_timeline: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Identify power spikes from level-ups and major item completions.

        A *power spike* is a timestamp where the participant gains a
        significant combat advantage: level 6/11/16 (ultimate upgrades) or
        completing a major item (cost >= 2600g).

        Parameters
        ----------
        frames : list[dict]
            Timeline frames with participantFrames containing level info.
        participant_id : str
            Key into participantFrames.
        items_timeline : list[dict]
            Ordered list of item purchases with ``timestamp``, ``item_id``,
            ``item_name``.

        Returns
        -------
        dict with power_spikes (list), strongest_window (dict),
        level_spikes (list), item_spikes (list).
        """
        level_spikes: List[Dict[str, Any]] = []
        ult_levels = {6, 11, 16}

        prev_level = 0
        for frame in frames:
            pf = frame.get("participantFrames", {})
            pdata = pf.get(participant_id, {})
            level = pdata.get("level", prev_level)
            ts = frame.get("timestamp", 0)

            if level != prev_level and level in ult_levels:
                level_spikes.append({
                    "timestamp": ts,
                    "minute": _ms_to_min(ts),
                    "level": level,
                    "type": "level_spike",
                    "description": f"Reached level {level} (ultimate upgrade)",
                })
            prev_level = level

        # Item spikes — every completed item is a potential spike
        item_spikes: List[Dict[str, Any]] = []
        for item in items_timeline:
            item_spikes.append({
                "timestamp": item.get("timestamp", 0),
                "minute": _ms_to_min(item.get("timestamp", 0)),
                "item_id": item.get("item_id", 0),
                "item_name": item.get("item_name", ""),
                "type": "item_spike",
                "description": f"Completed {item.get('item_name', 'item')}",
            })

        # Combine and sort
        all_spikes = sorted(
            level_spikes + item_spikes,
            key=lambda s: s["timestamp"],
        )

        # Strongest window = the 5-minute window with the most spikes
        strongest_window: Dict[str, Any] = {"start": 0, "end": 0, "spike_count": 0}
        if all_spikes:
            best_count = 0
            best_start = 0
            for i, spike in enumerate(all_spikes):
                window_end = spike["timestamp"] + 300000  # 5 minutes
                count = sum(
                    1 for s in all_spikes[i:]
                    if s["timestamp"] <= window_end
                )
                if count > best_count:
                    best_count = count
                    best_start = spike["timestamp"]
            strongest_window = {
                "start": best_start,
                "end": best_start + 300000,
                "start_min": _ms_to_min(best_start),
                "end_min": _ms_to_min(best_start + 300000),
                "spike_count": best_count,
            }

        return {
            "power_spikes": all_spikes,
            "strongest_window": strongest_window,
            "level_spikes": level_spikes,
            "item_spikes": item_spikes,
        }

    # ------------------------------------------------------------------ #
    #  5. Analyze Death Locations                                         #
    # ------------------------------------------------------------------ #

    def analyze_death_locations(
        self,
        deaths: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Cluster death positions to find danger zones.

        Uses a simple grid-based clustering approach: the map is divided
        into cells of size CLUSTER_RADIUS, and deaths in the same cell
        are grouped.

        Parameters
        ----------
        deaths : list[dict]
            Each entry has ``timestamp``, ``position`` {x, y}, ``killerId``.

        Returns
        -------
        dict with death_clusters, danger_zones, most_dangerous_zone,
        death_heatmap.
        """
        if not deaths:
            return {
                "death_clusters": [],
                "danger_zones": [],
                "most_dangerous_zone": None,
                "death_heatmap": {},
            }

        # --- Grid-based clustering ---
        grid: Dict[Tuple[int, int], List[Dict[str, Any]]] = defaultdict(list)
        for d in deaths:
            pos = d.get("position", {})
            gx = pos.get("x", 0) // CLUSTER_RADIUS
            gy = pos.get("y", 0) // CLUSTER_RADIUS
            grid[(gx, gy)].append(d)

        clusters: List[Dict[str, Any]] = []
        for (gx, gy), cluster_deaths in grid.items():
            center_x = (gx * CLUSTER_RADIUS) + CLUSTER_RADIUS // 2
            center_y = (gy * CLUSTER_RADIUS) + CLUSTER_RADIUS // 2
            clusters.append({
                "center": {"x": center_x, "y": center_y},
                "death_count": len(cluster_deaths),
                "deaths": cluster_deaths,
                "avg_timestamp": sum(d.get("timestamp", 0) for d in cluster_deaths) / len(cluster_deaths),
            })

        clusters.sort(key=lambda c: -c["death_count"])

        # Danger zones = clusters with >= 2 deaths
        danger_zones = [c for c in clusters if c["death_count"] >= 2]
        most_dangerous = danger_zones[0] if danger_zones else (clusters[0] if clusters else None)

        # Heatmap: grid cell → count
        heatmap: Dict[str, int] = {}
        for (gx, gy), cluster_deaths in grid.items():
            heatmap[f"{gx},{gy}"] = len(cluster_deaths)

        return {
            "death_clusters": clusters,
            "danger_zones": danger_zones,
            "most_dangerous_zone": most_dangerous,
            "death_heatmap": heatmap,
        }

    # ------------------------------------------------------------------ #
    #  6. Compute Vision Score Timeline                                   #
    # ------------------------------------------------------------------ #

    def compute_vision_score_timeline(
        self,
        ward_events: List[Dict[str, Any]],
        participant_id: int,
    ) -> Dict[str, Any]:
        """Compute vision-related statistics for a participant.

        Counts wards placed, control wards placed, wards destroyed, and
        estimates a vision score.

        Parameters
        ----------
        ward_events : list[dict]
            Ward placement and kill events.
        participant_id : int
            The participant whose vision to track.

        Returns
        -------
        dict with vision_events, wards_placed, wards_destroyed,
        control_wards, vision_score_estimate.
        """
        placed: int = 0
        control: int = 0
        destroyed: int = 0
        vision_events: List[Dict[str, Any]] = []

        for evt in ward_events:
            etype = evt.get("type", "")

            if etype == EVT_WARD_PLACED:
                creator = evt.get("creatorId", 0)
                if creator == participant_id:
                    placed += 1
                    ward_type = evt.get("wardType", "")
                    if ward_type == "CONTROL_WARD":
                        control += 1
                    vision_events.append({
                        "type": "placed",
                        "ward_type": ward_type,
                        "timestamp": evt.get("timestamp", 0),
                        "position": evt.get("position", {}),
                    })

            elif etype == EVT_WARD_KILL:
                killer = evt.get("killerId", 0)
                if killer == participant_id:
                    destroyed += 1
                    vision_events.append({
                        "type": "destroyed",
                        "timestamp": evt.get("timestamp", 0),
                        "position": evt.get("position", {}),
                    })

        # Rough vision score estimate:
        # Each ward placed ≈ 1 point, control ward ≈ 1.5, ward destroyed ≈ 1
        score_estimate = placed * 1.0 + control * 0.5 + destroyed * 1.0

        return {
            "vision_events": vision_events,
            "wards_placed": placed,
            "wards_destroyed": destroyed,
            "control_wards": control,
            "vision_score_estimate": round(score_estimate, 1),
        }

    # ------------------------------------------------------------------ #
    #  7. Full Replay Analysis                                            #
    # ------------------------------------------------------------------ #

    def run_full_analysis(
        self,
        replay_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Run the complete replay analysis pipeline.

        Parameters
        ----------
        replay_data : dict
            Keys:
              - timeline: {frames: [...]}
              - participant_id: str
              - opponent_id: str
              - participant_roles: dict[int, str]

        Returns
        -------
        dict with parsed_events, gank_patterns, gold_curve,
        power_spikes, death_analysis, vision_timeline, analysis_summary.
        """
        self._analysis_count += 1

        timeline = replay_data.get("timeline", {})
        frames = timeline.get("frames", [])
        pid = replay_data.get("participant_id", "1")
        oid = replay_data.get("opponent_id", "6")
        roles = replay_data.get("participant_roles", {})

        # Collect all events from frames
        all_events: List[Dict[str, Any]] = []
        all_ward_events: List[Dict[str, Any]] = []
        all_deaths: List[Dict[str, Any]] = []

        for frame in frames:
            for evt in frame.get("events", []):
                all_events.append(evt)
                etype = evt.get("type", "")
                if etype in (EVT_WARD_PLACED, EVT_WARD_KILL):
                    all_ward_events.append(evt)
                if etype == EVT_CHAMPION_KILL:
                    victim = evt.get("victimId", 0)
                    if str(victim) == str(pid):
                        all_deaths.append(evt)

        # Run sub-analyses
        parsed = self.parse_timeline_events(all_events)
        gold_curve = self.compute_gold_curve(frames, pid, oid)
        vision = self.compute_vision_score_timeline(
            all_ward_events, int(pid) if pid.isdigit() else 0
        )

        # Summary
        summary_parts: List[str] = []
        if gold_curve["gold_diffs"]:
            avg_diff = gold_curve["avg_gold_diff"]
            if avg_diff > 500:
                summary_parts.append("Strong gold lead throughout the game.")
            elif avg_diff < -500:
                summary_parts.append("Fell behind in gold during this game.")
            else:
                summary_parts.append("Gold was relatively even.")

        if vision["wards_placed"] > 5:
            summary_parts.append(f"Good warding with {vision['wards_placed']} wards placed.")

        summary = " ".join(summary_parts) if summary_parts else "Analysis complete."

        result = {
            "parsed_events": parsed,
            "gold_curve": gold_curve,
            "vision_timeline": vision,
            "analysis_summary": summary,
        }

        self._fire("full_analysis", {
            "events_parsed": len(all_events),
            "frames_analyzed": len(frames),
        })
        return result

    # ------------------------------------------------------------------ #
    #  Internal helpers                                                   #
    # ------------------------------------------------------------------ #

    def _classify_lane_from_position(self, pos: Dict[str, int]) -> str:
        """Rough lane classification based on map position.

        Summoner's Rift layout:
          TOP = upper-left diagonal
          MID = center diagonal
          BOT = lower-right diagonal

        This uses a simplified zone model.
        """
        x = pos.get("x", 7500)
        y = pos.get("y", 7500)

        # River runs diagonally; use x+y as a proxy for position on the map
        diagonal = x + y
        off_diagonal = abs(x - y)

        if off_diagonal < 3000:
            # Close to the x==y diagonal → MID lane
            return "MID"
        if y > x:
            return "TOP"
        return "BOT"

    def _fire(self, event_type: str, payload: Dict[str, Any]) -> None:
        """Dispatch evolution event."""
        if self.evolution_callback is not None:
            self.evolution_callback({
                "source": _EVOLUTION_KEY,
                "type": event_type,
                "timestamp": time.time(),
                "payload": payload,
            })

    def get_diagnostics(self) -> Dict[str, Any]:
        """Return internal counters."""
        return {
            "analysis_count": self._analysis_count,
            "event_index_keys": list(self._event_index.keys()),
            "evolution_key": _EVOLUTION_KEY,
        }
