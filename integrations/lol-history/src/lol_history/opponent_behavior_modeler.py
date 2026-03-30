"""
OpponentBehaviorModeler — Models opponent behavior patterns from historical
match data for real-time prediction during live games.

Builds a behavioral profile for each opponent: playstyle classification
(aggressive/passive/farming/roaming), tilt detection, early-game tendencies,
and action prediction.  This transforms Seraphine's raw match history into
*predictive intelligence*.

Architecture (拿来主义):
  - Seraphine/app/lol/tools.py: parseGameData, parseSummonerData — raw stats
  - Seraphine/app/lol/opgg.py: OpggDataParser — tier/meta context
  - integrations/lol-history/src/lol_history/player_profiler.py: profiling
  - DI-star/distar/agent/: opponent modeling concepts

Location: integrations/lol-history/src/lol_history/opponent_behavior_modeler.py
"""

from __future__ import annotations

import logging
import math
import time
from collections import defaultdict
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "integrations.lol_history.opponent_behavior_modeler.v1"

# ---------------------------------------------------------------------------
# Playstyle thresholds
# ---------------------------------------------------------------------------
AGGRESSION_HIGH = 0.65
AGGRESSION_LOW = 0.35
CS_FOCUS_HIGH = 0.65
ROAMING_THRESHOLD = 0.5
TILT_RECENT_WINDOW = 5
SHORT_GAME_THRESHOLD = 1200  # 20 min = likely a stomp or surrender


def _safe_div(a: float, b: float, default: float = 0.0) -> float:
    return a / b if b != 0 else default


def _kda(k: int, d: int, a: int) -> float:
    return (k + a) / max(d, 1)


def _confidence_from_games(n: int, max_n: int = 20) -> float:
    if n <= 0:
        return 0.0
    return min(1.0, math.log1p(n) / math.log1p(max_n))


# ===================================================================== #
#                     OpponentBehaviorModeler                            #
# ===================================================================== #

class OpponentBehaviorModeler:
    """Models opponent behavior from historical match data.

    Combines multiple analytical dimensions — playstyle, early behavior,
    tilt indicators — into a unified behavior model that can predict
    opponent actions during a live game.

    Public API
    ----------
    classify_playstyle(matches) -> dict
    predict_early_behavior(matches) -> dict
    detect_tilt_indicators(matches) -> dict
    build_behavior_model(puuid, matches) -> dict
    predict_next_action(model, game_state) -> dict
    compare_opponents(model_a, model_b) -> dict
    """

    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable[[Dict[str, Any]], None]] = None
        self._model_count: int = 0
        self._behavior_cache: Dict[str, Dict[str, Any]] = {}

    # ------------------------------------------------------------------ #
    #  1. Classify Playstyle                                              #
    # ------------------------------------------------------------------ #

    def classify_playstyle(
        self,
        matches: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Classify a player's playstyle from recent matches.

        Dimensions:
          - aggression_score: kills + damage_dealt weighted
          - cs_focus_score: CS/min normalized
          - vision_score: wards_placed/game normalized
          - roaming_score: assists-to-kills ratio (high = roaming)

        Parameters
        ----------
        matches : list[dict]
            Each with kills, deaths, assists, cs, wards_placed,
            game_duration, damage_dealt, damage_taken.

        Returns
        -------
        dict with primary_style, aggression_score, cs_focus_score,
        vision_score, roaming_score, damage_profile, style_confidence.
        """
        if not matches:
            return {
                "primary_style": "unknown",
                "aggression_score": 0.0,
                "cs_focus_score": 0.0,
                "vision_score": 0.0,
                "roaming_score": 0.0,
                "damage_profile": {"dealt_per_min": 0.0, "taken_per_min": 0.0, "ratio": 0.0},
                "style_confidence": 0.0,
            }

        n = len(matches)

        # --- Aggregate raw stats ---
        total_kills = sum(m.get("kills", 0) for m in matches)
        total_deaths = sum(m.get("deaths", 0) for m in matches)
        total_assists = sum(m.get("assists", 0) for m in matches)
        total_cs = sum(m.get("cs", 0) for m in matches)
        total_wards = sum(m.get("wards_placed", 0) for m in matches)
        total_duration_min = sum(m.get("game_duration", 1) for m in matches) / 60.0
        total_dmg_dealt = sum(m.get("damage_dealt", 0) for m in matches)
        total_dmg_taken = sum(m.get("damage_taken", 0) for m in matches)

        # --- Compute scores (all in [0, 1]) ---
        kills_per_min = _safe_div(total_kills, total_duration_min)
        deaths_per_min = _safe_div(total_deaths, total_duration_min)
        assists_per_min = _safe_div(total_assists, total_duration_min)
        cs_per_min = _safe_div(total_cs, total_duration_min)
        wards_per_game = _safe_div(total_wards, n)
        dmg_dealt_per_min = _safe_div(total_dmg_dealt, total_duration_min)
        dmg_taken_per_min = _safe_div(total_dmg_taken, total_duration_min)

        # Aggression: high kills + high damage dealt + high damage taken
        aggression_raw = (
            min(kills_per_min / 0.5, 1.0) * 0.4
            + min(dmg_dealt_per_min / 800.0, 1.0) * 0.3
            + min(dmg_taken_per_min / 600.0, 1.0) * 0.2
            + min(deaths_per_min / 0.4, 1.0) * 0.1
        )
        aggression_score = max(0.0, min(1.0, aggression_raw))

        # CS focus: high CS/min
        cs_focus_raw = min(cs_per_min / 8.0, 1.0)
        cs_focus_score = max(0.0, min(1.0, cs_focus_raw))

        # Vision: wards per game
        vision_raw = min(wards_per_game / 15.0, 1.0)
        vision_score = max(0.0, min(1.0, vision_raw))

        # Roaming: assist-heavy players tend to roam
        roaming_raw = _safe_div(total_assists, max(total_kills, 1))
        roaming_score = max(0.0, min(1.0, roaming_raw / 3.0))

        # --- Classify primary style ---
        if aggression_score >= AGGRESSION_HIGH:
            primary = "aggressive"
        elif cs_focus_score >= CS_FOCUS_HIGH and aggression_score < AGGRESSION_LOW:
            primary = "farming"
        elif roaming_score >= ROAMING_THRESHOLD:
            primary = "roaming"
        elif aggression_score < AGGRESSION_LOW:
            primary = "passive"
        else:
            primary = "balanced"

        # Damage profile
        dmg_ratio = _safe_div(dmg_dealt_per_min, max(dmg_taken_per_min, 1))

        confidence = _confidence_from_games(n)

        result = {
            "primary_style": primary,
            "aggression_score": round(aggression_score, 4),
            "cs_focus_score": round(cs_focus_score, 4),
            "vision_score": round(vision_score, 4),
            "roaming_score": round(roaming_score, 4),
            "damage_profile": {
                "dealt_per_min": round(dmg_dealt_per_min, 1),
                "taken_per_min": round(dmg_taken_per_min, 1),
                "ratio": round(dmg_ratio, 2),
            },
            "style_confidence": round(confidence, 4),
        }
        return result

    # ------------------------------------------------------------------ #
    #  2. Predict Early Behavior                                          #
    # ------------------------------------------------------------------ #

    def predict_early_behavior(
        self,
        matches: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Predict opponent's early-game behavior from history.

        Parameters
        ----------
        matches : list[dict]
            Each with early_kills, early_deaths, first_blood, first_ward_time,
            first_back_time, early_cs.

        Returns
        -------
        dict with first_blood_rate, avg_first_ward_time, avg_first_back_time,
        early_aggression, early_cs_per_min, early_prediction.
        """
        if not matches:
            return {
                "first_blood_rate": 0.0,
                "avg_first_ward_time": 0.0,
                "avg_first_back_time": 0.0,
                "early_aggression": 0.0,
                "early_cs_per_min": 0.0,
                "early_prediction": "No data available.",
            }

        n = len(matches)
        fb_count = sum(1 for m in matches if m.get("first_blood"))
        fb_rate = _safe_div(fb_count, n)

        ward_times = [m.get("first_ward_time", 0) for m in matches if m.get("first_ward_time", 0) > 0]
        avg_ward_time = _safe_div(sum(ward_times), len(ward_times)) if ward_times else 0.0

        back_times = [m.get("first_back_time", 0) for m in matches if m.get("first_back_time", 0) > 0]
        avg_back_time = _safe_div(sum(back_times), len(back_times)) if back_times else 0.0

        total_early_kills = sum(m.get("early_kills", 0) for m in matches)
        total_early_deaths = sum(m.get("early_deaths", 0) for m in matches)
        early_aggression = min(1.0, _safe_div(total_early_kills, max(total_early_kills + total_early_deaths, 1)))

        total_early_cs = sum(m.get("early_cs", 0) for m in matches)
        early_cs_per_min = _safe_div(total_early_cs, n * 15.0)  # 15 min early phase

        # Generate prediction
        predictions: List[str] = []
        if fb_rate >= 0.4:
            predictions.append("Likely to attempt first blood — be cautious level 1-3.")
        if early_aggression >= 0.6:
            predictions.append("Aggressive early player — expect frequent trades.")
        if early_aggression < 0.3:
            predictions.append("Passive early — opportunity to zone and pressure.")
        if avg_ward_time > 0 and avg_ward_time < 150:
            predictions.append("Quick warder — may invade or track your jungler early.")
        if avg_back_time > 0 and avg_back_time < 300:
            predictions.append("Backs early — look for plate pressure when they recall.")

        prediction = " ".join(predictions) if predictions else "Standard early game expected."

        return {
            "first_blood_rate": round(fb_rate, 4),
            "avg_first_ward_time": round(avg_ward_time, 1),
            "avg_first_back_time": round(avg_back_time, 1),
            "early_aggression": round(early_aggression, 4),
            "early_cs_per_min": round(early_cs_per_min, 2),
            "early_prediction": prediction,
        }

    # ------------------------------------------------------------------ #
    #  3. Detect Tilt Indicators                                          #
    # ------------------------------------------------------------------ #

    def detect_tilt_indicators(
        self,
        matches: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Detect signs of tilt from recent match history.

        Tilt indicators:
          - High death counts in losses
          - Surrenders
          - Short games (stomps)
          - Loss streaks

        Parameters
        ----------
        matches : list[dict]
            Sorted most-recent-first, each with win, deaths, game_duration,
            surrender, game_timestamp.

        Returns
        -------
        dict with tilt_score [0,1], surrender_rate, avg_deaths_in_losses,
        short_game_loss_rate, recent_loss_streak, tilt_level.
        """
        if not matches:
            return {
                "tilt_score": 0.0,
                "surrender_rate": 0.0,
                "avg_deaths_in_losses": 0.0,
                "short_game_loss_rate": 0.0,
                "recent_loss_streak": 0,
                "tilt_level": "none",
            }

        recent = matches[:TILT_RECENT_WINDOW]
        n = len(recent)

        losses = [m for m in recent if not m.get("win")]
        loss_count = len(losses)
        surrender_count = sum(1 for m in losses if m.get("surrender"))
        surrender_rate = _safe_div(surrender_count, n)

        avg_deaths_in_losses = (
            _safe_div(sum(m.get("deaths", 0) for m in losses), loss_count)
            if losses else 0.0
        )

        short_game_losses = sum(
            1 for m in losses if m.get("game_duration", 9999) < SHORT_GAME_THRESHOLD
        )
        short_game_loss_rate = _safe_div(short_game_losses, n)

        # Recent loss streak (from end)
        streak = 0
        for m in recent:
            if not m.get("win"):
                streak += 1
            else:
                break

        # Composite tilt score
        tilt_raw = (
            _safe_div(loss_count, n) * 0.30
            + surrender_rate * 0.25
            + min(avg_deaths_in_losses / 10.0, 1.0) * 0.20
            + short_game_loss_rate * 0.15
            + min(streak / 5.0, 1.0) * 0.10
        )
        tilt_score = max(0.0, min(1.0, tilt_raw))

        # Tilt level classification
        if tilt_score >= 0.7:
            tilt_level = "high"
        elif tilt_score >= 0.4:
            tilt_level = "moderate"
        elif tilt_score >= 0.15:
            tilt_level = "low"
        else:
            tilt_level = "none"

        return {
            "tilt_score": round(tilt_score, 4),
            "surrender_rate": round(surrender_rate, 4),
            "avg_deaths_in_losses": round(avg_deaths_in_losses, 2),
            "short_game_loss_rate": round(short_game_loss_rate, 4),
            "recent_loss_streak": streak,
            "tilt_level": tilt_level,
        }

    # ------------------------------------------------------------------ #
    #  4. Build Complete Behavior Model                                   #
    # ------------------------------------------------------------------ #

    def build_behavior_model(
        self,
        puuid: str,
        matches: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Build a comprehensive behavior model for a player.

        Combines playstyle classification, early behavior prediction,
        and tilt detection into a single model dict.

        Parameters
        ----------
        puuid : str
            Player unique ID.
        matches : list[dict]
            Full match records.

        Returns
        -------
        dict with puuid, playstyle, early_behavior, tilt_indicators,
        model_confidence, champion_preferences, summary.
        """
        self._model_count += 1

        playstyle = self.classify_playstyle(matches)
        early = self.predict_early_behavior(matches)
        tilt = self.detect_tilt_indicators(matches)

        # Champion preferences
        champ_counts: Dict[int, int] = defaultdict(int)
        role_counts: Dict[str, int] = defaultdict(int)
        for m in matches:
            cid = m.get("champion_id", 0)
            if cid:
                champ_counts[cid] += 1
            role = m.get("role", "")
            if role:
                role_counts[role] += 1

        top_champs = sorted(champ_counts.items(), key=lambda x: -x[1])[:5]
        top_roles = sorted(role_counts.items(), key=lambda x: -x[1])[:3]

        # Model confidence = weighted average of sub-model confidences
        n = len(matches)
        model_conf = _confidence_from_games(n)

        # Summary
        parts: List[str] = []
        parts.append(f"Playstyle: {playstyle['primary_style']} (aggression={playstyle['aggression_score']:.0%}).")
        if early.get("early_aggression", 0) > 0.5:
            parts.append("Tends to be aggressive in early game.")
        if tilt.get("tilt_level") in ("moderate", "high"):
            parts.append(f"Shows {tilt['tilt_level']} tilt indicators — may be vulnerable to pressure.")
        if top_champs:
            champ_str = ", ".join(str(c[0]) for c in top_champs[:3])
            parts.append(f"Favorite champions: {champ_str}.")

        result = {
            "puuid": puuid,
            "playstyle": playstyle,
            "early_behavior": early,
            "tilt_indicators": tilt,
            "champion_preferences": {
                "top_champions": [{"id": c[0], "games": c[1]} for c in top_champs],
                "top_roles": [{"role": r[0], "games": r[1]} for r in top_roles],
            },
            "model_confidence": round(model_conf, 4),
            "summary": " ".join(parts),
        }

        self._behavior_cache[puuid] = result
        self._fire("model_built", {
            "puuid": puuid,
            "games": n,
            "style": playstyle["primary_style"],
            "confidence": model_conf,
        })
        return result

    # ------------------------------------------------------------------ #
    #  5. Predict Next Action                                             #
    # ------------------------------------------------------------------ #

    def predict_next_action(
        self,
        model: Dict[str, Any],
        game_state: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Predict the opponent's likely next action given current game state.

        Uses the behavior model plus current game context (time, level, items)
        to generate a prediction.

        Parameters
        ----------
        model : dict
            Output of build_behavior_model().
        game_state : dict
            Current state: game_time, opponent_level, opponent_cs, opponent_items.

        Returns
        -------
        dict with predicted_action, confidence, reasoning, alternative_actions.
        """
        playstyle = model.get("playstyle", {})
        early = model.get("early_behavior", {})
        aggression = playstyle.get("aggression_score", 0.5)

        game_time = game_state.get("game_time", 0)
        level = game_state.get("opponent_level", 1)
        cs = game_state.get("opponent_cs", 0)
        items = game_state.get("opponent_items", [])

        # Decision tree based on game time and playstyle
        action = "farm"
        reasoning_parts: List[str] = []
        alternatives: List[str] = []
        conf = 0.5

        if game_time < 180:  # < 3 min
            if early.get("early_aggression", 0) > 0.6:
                action = "aggressive_trade"
                reasoning_parts.append("Historically aggressive early — expect level 2/3 all-in.")
                conf = 0.65
                alternatives = ["cheese_invade", "early_roam"]
            else:
                action = "farm_safely"
                reasoning_parts.append("Passive early player — will likely farm.")
                conf = 0.6
                alternatives = ["freeze_lane"]

        elif game_time < 900:  # 3-15 min (laning)
            if aggression >= AGGRESSION_HIGH:
                action = "look_for_kill"
                reasoning_parts.append("Aggressive playstyle — will trade and all-in frequently.")
                conf = 0.6
                alternatives = ["roam", "dive"]
            elif playstyle.get("primary_style") == "roaming":
                action = "roam_to_sidelane"
                reasoning_parts.append("Roaming playstyle — expect them to leave lane.")
                conf = 0.55
                alternatives = ["look_for_kill", "farm"]
            else:
                action = "farm"
                reasoning_parts.append("Standard farming pattern expected.")
                conf = 0.5
                alternatives = ["trade", "recall"]

        else:  # > 15 min (mid/late)
            if aggression >= AGGRESSION_HIGH:
                action = "force_fight"
                reasoning_parts.append("Aggressive player in mid/late — will seek teamfights.")
                conf = 0.55
                alternatives = ["split_push", "pick"]
            else:
                action = "group_and_push"
                reasoning_parts.append("Non-aggressive player will group with team.")
                conf = 0.5
                alternatives = ["farm_sidelane", "take_objective"]

        # Tilt adjustment
        tilt = model.get("tilt_indicators", {})
        if tilt.get("tilt_level") in ("moderate", "high"):
            reasoning_parts.append("Player may be tilted — more likely to make mistakes.")
            conf = min(1.0, conf + 0.1)

        return {
            "predicted_action": action,
            "confidence": round(conf, 4),
            "reasoning": " ".join(reasoning_parts),
            "alternative_actions": alternatives,
        }

    # ------------------------------------------------------------------ #
    #  6. Compare Opponents                                               #
    # ------------------------------------------------------------------ #

    def compare_opponents(
        self,
        model_a: Dict[str, Any],
        model_b: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Compare two opponent behavior models to determine who is more
        dangerous and highlight key differences.

        Parameters
        ----------
        model_a, model_b : dict
            Behavior models (or sub-dicts with playstyle and tilt_indicators).

        Returns
        -------
        dict with more_dangerous ("A" or "B"), key_differences (list[str]),
        danger_scores.
        """
        def _danger_score(model: Dict[str, Any]) -> float:
            ps = model.get("playstyle", {})
            ti = model.get("tilt_indicators", {})
            conf = model.get("model_confidence", 0.5)
            agg = ps.get("aggression_score", 0.5)
            tilt = ti.get("tilt_score", 0)
            # Higher aggression + lower tilt = more dangerous
            return agg * 0.6 + (1.0 - tilt) * 0.3 + conf * 0.1

        score_a = _danger_score(model_a)
        score_b = _danger_score(model_b)

        more_dangerous = "A" if score_a >= score_b else "B"

        diffs: List[str] = []
        agg_a = model_a.get("playstyle", {}).get("aggression_score", 0)
        agg_b = model_b.get("playstyle", {}).get("aggression_score", 0)
        if abs(agg_a - agg_b) > 0.2:
            diffs.append(f"Aggression: A={agg_a:.0%} vs B={agg_b:.0%}")

        cs_a = model_a.get("playstyle", {}).get("cs_focus_score", 0)
        cs_b = model_b.get("playstyle", {}).get("cs_focus_score", 0)
        if abs(cs_a - cs_b) > 0.2:
            diffs.append(f"CS Focus: A={cs_a:.0%} vs B={cs_b:.0%}")

        tilt_a = model_a.get("tilt_indicators", {}).get("tilt_score", 0)
        tilt_b = model_b.get("tilt_indicators", {}).get("tilt_score", 0)
        if abs(tilt_a - tilt_b) > 0.2:
            diffs.append(f"Tilt: A={tilt_a:.0%} vs B={tilt_b:.0%}")

        if not diffs:
            diffs.append("Similar profiles — no major differences detected.")

        return {
            "more_dangerous": more_dangerous,
            "key_differences": diffs,
            "danger_scores": {"A": round(score_a, 4), "B": round(score_b, 4)},
        }

    # ------------------------------------------------------------------ #
    #  Internal                                                           #
    # ------------------------------------------------------------------ #

    def _fire(self, event_type: str, payload: Dict[str, Any]) -> None:
        if self.evolution_callback is not None:
            self.evolution_callback({
                "source": _EVOLUTION_KEY,
                "type": event_type,
                "timestamp": time.time(),
                "payload": payload,
            })

    def get_diagnostics(self) -> Dict[str, Any]:
        return {
            "model_count": self._model_count,
            "cache_size": len(self._behavior_cache),
            "evolution_key": _EVOLUTION_KEY,
        }
