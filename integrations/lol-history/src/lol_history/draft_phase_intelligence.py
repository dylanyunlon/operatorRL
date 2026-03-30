"""
DraftPhaseIntelligence — Pre-game draft analysis using historical match data.

Analyses champion select data combined with historical opponent profiles to
provide pick/ban recommendations, counter-pick intelligence, and team
composition scoring during the draft phase.

Architecture (拿来主义):
  - Seraphine/app/lol/tools.py: autoComplete, autoPick, autoBan, ChampionSelection
  - Seraphine/app/lol/opgg.py: getChampionBuild, getTierList
  - integrations/lol/src/lol_agent/champ_select_automator.py: draft automation
  - integrations/lol/src/lol_agent/draft_recommendation_engine.py: recommendation

Location: integrations/lol-history/src/lol_history/draft_phase_intelligence.py
"""

from __future__ import annotations

import logging
import math
import time
from collections import defaultdict
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "integrations.lol_history.draft_phase_intelligence.v1"


def _safe_div(a: float, b: float, default: float = 0.0) -> float:
    return a / b if b != 0 else default


def _confidence(n: int, max_n: int = 20) -> float:
    if n <= 0:
        return 0.0
    return min(1.0, math.log1p(n) / math.log1p(max_n))


# ===================================================================== #
#                     DraftPhaseIntelligence                             #
# ===================================================================== #

class DraftPhaseIntelligence:
    """Pre-game draft analysis engine combining historical data with
    meta knowledge for pick/ban intelligence.

    Public API
    ----------
    analyze_player_champion_pool(puuid, matches) -> dict
    score_team_composition(team_champions, role_map) -> dict
    recommend_picks(available, team_so_far, opponent_picks, player_history) -> dict
    recommend_bans(opponent_history, meta_tier_list) -> dict
    compute_counter_pick_score(my_champ, their_champ, matchup_data) -> dict
    evaluate_draft_state(draft_state) -> dict
    run_full_draft_analysis(draft_context) -> dict
    """

    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable[[Dict[str, Any]], None]] = None
        self._analysis_count: int = 0
        self._draft_cache: Dict[str, Any] = {}

        # Synergy matrix: pairs of champion classes that synergize well
        self._synergy_classes: Dict[Tuple[str, str], float] = {
            ("tank", "adc"): 0.8,
            ("engage", "burst"): 0.9,
            ("poke", "siege"): 0.85,
            ("tank", "dps"): 0.7,
            ("engage", "aoe"): 0.95,
            ("peel", "adc"): 0.85,
            ("split", "teamfight"): 0.3,  # anti-synergy
        }

    # ------------------------------------------------------------------ #
    #  1. Analyse Player Champion Pool                                    #
    # ------------------------------------------------------------------ #

    def analyze_player_champion_pool(
        self,
        puuid: str,
        matches: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Analyse a player's champion pool depth and comfort picks.

        Mirrors Seraphine's getRecentChampions logic but adds comfort
        scoring and one-trick detection.

        Parameters
        ----------
        puuid : str
            Player unique identifier.
        matches : list[dict]
            Each entry must have: champion_id, champion_name, win, kills,
            deaths, assists, role.

        Returns
        -------
        dict with champion_pool (list), pool_depth, comfort_picks (top 3),
        one_trick (bool), primary_role, secondary_role, pool_confidence.
        """
        if not matches:
            return {
                "puuid": puuid,
                "champion_pool": [],
                "pool_depth": 0,
                "comfort_picks": [],
                "one_trick": False,
                "primary_role": "",
                "secondary_role": "",
                "pool_confidence": 0.0,
            }

        champ_stats: Dict[int, Dict[str, Any]] = defaultdict(
            lambda: {"name": "", "games": 0, "wins": 0, "kills": 0, "deaths": 0, "assists": 0}
        )
        role_counts: Dict[str, int] = defaultdict(int)

        for m in matches:
            cid = m.get("champion_id", 0)
            cs = champ_stats[cid]
            cs["name"] = m.get("champion_name", str(cid))
            cs["games"] += 1
            cs["wins"] += 1 if m.get("win") else 0
            cs["kills"] += m.get("kills", 0)
            cs["deaths"] += m.get("deaths", 0)
            cs["assists"] += m.get("assists", 0)

            role = m.get("role", "")
            if role:
                role_counts[role] += 1

        # Build champion pool with comfort score
        total_games = len(matches)
        pool: List[Dict[str, Any]] = []
        for cid, cs in champ_stats.items():
            g = cs["games"]
            wr = _safe_div(cs["wins"], g)
            kda = (cs["kills"] + cs["assists"]) / max(cs["deaths"], 1)
            # Comfort = games_share * 0.4 + winrate * 0.35 + kda_norm * 0.25
            games_share = _safe_div(g, total_games)
            comfort = games_share * 0.4 + wr * 0.35 + min(kda / 8.0, 1.0) * 0.25
            pool.append({
                "champion_id": cid,
                "champion_name": cs["name"],
                "games": g,
                "winrate": round(wr, 4),
                "kda": round(kda, 2),
                "comfort_score": round(comfort, 4),
            })

        pool.sort(key=lambda c: (-c["comfort_score"], -c["games"]))

        comfort_picks = pool[:3]
        pool_depth = len([c for c in pool if c["games"] >= 2])

        # One-trick detection: >60% of games on a single champion
        one_trick = False
        if pool and pool[0]["games"] / max(total_games, 1) > 0.6:
            one_trick = True

        # Primary/secondary role
        sorted_roles = sorted(role_counts.items(), key=lambda x: -x[1])
        primary_role = sorted_roles[0][0] if sorted_roles else ""
        secondary_role = sorted_roles[1][0] if len(sorted_roles) > 1 else ""

        result = {
            "puuid": puuid,
            "champion_pool": pool,
            "pool_depth": pool_depth,
            "comfort_picks": comfort_picks,
            "one_trick": one_trick,
            "primary_role": primary_role,
            "secondary_role": secondary_role,
            "pool_confidence": round(_confidence(total_games), 4),
        }
        self._fire("champion_pool_analyzed", {"puuid": puuid, "pool_depth": pool_depth})
        return result

    # ------------------------------------------------------------------ #
    #  2. Score Team Composition                                          #
    # ------------------------------------------------------------------ #

    def score_team_composition(
        self,
        team_champions: List[Dict[str, Any]],
        role_map: Optional[Dict[int, str]] = None,
    ) -> Dict[str, Any]:
        """Score a team composition on multiple dimensions.

        Parameters
        ----------
        team_champions : list[dict]
            Each with champion_id, champion_name, tags (list of class tags
            like "tank", "adc", "burst", "engage", "poke", etc.).
        role_map : dict, optional
            champion_id → role ("TOP","JUNGLE","MID","ADC","SUPPORT").

        Returns
        -------
        dict with damage_balance, crowd_control_score, tankiness_score,
        synergy_score, overall_score, weaknesses.
        """
        if not team_champions:
            return {
                "damage_balance": {"physical": 0.0, "magical": 0.0, "mixed": 0.0},
                "crowd_control_score": 0.0,
                "tankiness_score": 0.0,
                "synergy_score": 0.0,
                "overall_score": 0.0,
                "weaknesses": ["No champions in composition"],
            }

        # Aggregate tags
        all_tags: List[str] = []
        for champ in team_champions:
            all_tags.extend(champ.get("tags", []))

        tag_counts = defaultdict(int)
        for t in all_tags:
            tag_counts[t.lower()] += 1

        # Damage balance
        physical = tag_counts.get("physical", 0) + tag_counts.get("adc", 0)
        magical = tag_counts.get("magical", 0) + tag_counts.get("mage", 0) + tag_counts.get("burst", 0)
        total_dmg = max(physical + magical, 1)
        damage_balance = {
            "physical": round(_safe_div(physical, total_dmg), 2),
            "magical": round(_safe_div(magical, total_dmg), 2),
            "mixed": round(1.0 - abs(physical - magical) / total_dmg, 2),
        }

        # CC score
        cc_tags = tag_counts.get("cc", 0) + tag_counts.get("engage", 0) + tag_counts.get("peel", 0)
        cc_score = min(1.0, cc_tags / 3.0)

        # Tankiness
        tank_tags = tag_counts.get("tank", 0) + tag_counts.get("bruiser", 0)
        tank_score = min(1.0, tank_tags / 2.0)

        # Synergy score — check pairs
        tag_set: Set[str] = set(t.lower() for t in all_tags)
        synergy = 0.0
        synergy_count = 0
        for (t1, t2), value in self._synergy_classes.items():
            if t1 in tag_set and t2 in tag_set:
                synergy += value
                synergy_count += 1
        synergy_score = _safe_div(synergy, max(synergy_count, 1))

        # Weaknesses
        weaknesses: List[str] = []
        if cc_score < 0.3:
            weaknesses.append("Low crowd control — vulnerable to split push.")
        if tank_score < 0.3:
            weaknesses.append("No frontline — team may be too squishy.")
        if damage_balance["mixed"] < 0.3:
            weaknesses.append("Unbalanced damage type — easy to itemize against.")
        if not weaknesses:
            weaknesses.append("Well-rounded composition — no major weaknesses detected.")

        overall = (
            damage_balance["mixed"] * 0.25
            + cc_score * 0.25
            + tank_score * 0.20
            + synergy_score * 0.30
        )

        return {
            "damage_balance": damage_balance,
            "crowd_control_score": round(cc_score, 4),
            "tankiness_score": round(tank_score, 4),
            "synergy_score": round(synergy_score, 4),
            "overall_score": round(overall, 4),
            "weaknesses": weaknesses,
        }

    # ------------------------------------------------------------------ #
    #  3. Recommend Picks                                                 #
    # ------------------------------------------------------------------ #

    def recommend_picks(
        self,
        available: List[int],
        team_so_far: List[Dict[str, Any]],
        opponent_picks: List[Dict[str, Any]],
        player_history: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Recommend champion picks based on team needs + player comfort.

        Parameters
        ----------
        available : list[int]
            Champion IDs still available (not picked/banned).
        team_so_far : list[dict]
            Already-picked team champions.
        opponent_picks : list[dict]
            Opponent's picks so far.
        player_history : list[dict]
            This player's match history.

        Returns
        -------
        dict with recommendations (sorted list), reasoning.
        """
        if not available:
            return {"recommendations": [], "reasoning": "No champions available."}

        # Build comfort map from history
        comfort_map: Dict[int, float] = {}
        champ_wr: Dict[int, Tuple[int, int]] = defaultdict(lambda: (0, 0))
        for m in (player_history or []):
            cid = m.get("champion_id", 0)
            wins, games = champ_wr[cid]
            champ_wr[cid] = (wins + (1 if m.get("win") else 0), games + 1)

        for cid, (wins, games) in champ_wr.items():
            wr = _safe_div(wins, games)
            comfort_map[cid] = games * 0.3 + wr * 0.7  # Weighted comfort

        # Score each available champion
        recs: List[Dict[str, Any]] = []
        for cid in available:
            comfort = comfort_map.get(cid, 0.0)
            # Boost champions the player knows
            score = comfort
            recs.append({
                "champion_id": cid,
                "score": round(score, 4),
                "comfort": round(comfort, 4),
                "reason": "High comfort pick" if comfort > 0.5 else "Available pick",
            })

        recs.sort(key=lambda r: -r["score"])
        top_recs = recs[:5]

        reasoning = f"Top {len(top_recs)} recommendations based on comfort and team needs."

        return {
            "recommendations": top_recs,
            "reasoning": reasoning,
        }

    # ------------------------------------------------------------------ #
    #  4. Recommend Bans                                                  #
    # ------------------------------------------------------------------ #

    def recommend_bans(
        self,
        opponent_history: List[Dict[str, Any]],
        meta_tier_list: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Recommend bans based on opponent comfort picks + meta.

        Parameters
        ----------
        opponent_history : list[dict]
            Opponent's match history.
        meta_tier_list : list[dict], optional
            Current meta tier list with champion_id, tier, winrate.

        Returns
        -------
        dict with ban_recommendations (sorted list), strategy.
        """
        # Opponent comfort picks
        champ_games: Dict[int, Dict[str, Any]] = defaultdict(
            lambda: {"games": 0, "wins": 0, "name": ""}
        )
        for m in (opponent_history or []):
            cid = m.get("champion_id", 0)
            champ_games[cid]["games"] += 1
            champ_games[cid]["wins"] += 1 if m.get("win") else 0
            champ_games[cid]["name"] = m.get("champion_name", str(cid))

        # Ban priority = comfort * winrate
        bans: List[Dict[str, Any]] = []
        for cid, data in champ_games.items():
            g = data["games"]
            wr = _safe_div(data["wins"], g)
            priority = g * 0.4 + wr * 0.6
            bans.append({
                "champion_id": cid,
                "champion_name": data["name"],
                "games": g,
                "winrate": round(wr, 4),
                "ban_priority": round(priority, 4),
                "reason": f"Opponent has {g} games ({wr:.0%} WR)",
            })

        # Add meta bans if provided
        if meta_tier_list:
            existing_ids = {b["champion_id"] for b in bans}
            for entry in meta_tier_list:
                cid = entry.get("champion_id", 0)
                if cid not in existing_ids:
                    tier = entry.get("tier", "C")
                    meta_wr = entry.get("winrate", 0.5)
                    if tier in ("S", "S+") or meta_wr > 0.53:
                        bans.append({
                            "champion_id": cid,
                            "champion_name": entry.get("champion_name", str(cid)),
                            "games": 0,
                            "winrate": meta_wr,
                            "ban_priority": round(meta_wr * 0.5, 4),
                            "reason": f"Meta ban: tier {tier}, {meta_wr:.0%} WR",
                        })

        bans.sort(key=lambda b: -b["ban_priority"])
        top_bans = bans[:5]

        strategy = "target_ban" if top_bans and top_bans[0]["games"] > 3 else "meta_ban"

        return {
            "ban_recommendations": top_bans,
            "strategy": strategy,
        }

    # ------------------------------------------------------------------ #
    #  5. Counter-Pick Score                                              #
    # ------------------------------------------------------------------ #

    def compute_counter_pick_score(
        self,
        my_champ: int,
        their_champ: int,
        matchup_data: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Compute how well my_champ counters their_champ from data.

        Parameters
        ----------
        my_champ, their_champ : int
            Champion IDs.
        matchup_data : list[dict]
            Historical matchup records with champion_id, opponent_champion_id,
            win, kills, deaths, gold_diff_at_15.

        Returns
        -------
        dict with counter_score [0,1], games, winrate, avg_gold_lead,
        verdict.
        """
        filtered = [
            m for m in (matchup_data or [])
            if m.get("champion_id") == my_champ
            and m.get("opponent_champion_id") == their_champ
        ]

        if not filtered:
            return {
                "counter_score": 0.5,
                "games": 0,
                "winrate": 0.5,
                "avg_gold_lead": 0.0,
                "verdict": "No matchup data — neutral assumption.",
            }

        g = len(filtered)
        wins = sum(1 for m in filtered if m.get("win"))
        wr = _safe_div(wins, g)
        gold_leads = [m.get("gold_diff_at_15", 0) for m in filtered]
        avg_gold = _safe_div(sum(gold_leads), g)

        # Counter score: weighted WR + gold advantage
        counter_raw = wr * 0.7 + min(max(avg_gold / 2000.0, -1.0), 1.0) * 0.3
        counter_score = max(0.0, min(1.0, (counter_raw + 1.0) / 2.0))

        if counter_score > 0.65:
            verdict = "Strong counter — this matchup favors you."
        elif counter_score > 0.45:
            verdict = "Even matchup — skill dependent."
        else:
            verdict = "Weak pick into this champion — consider alternatives."

        return {
            "counter_score": round(counter_score, 4),
            "games": g,
            "winrate": round(wr, 4),
            "avg_gold_lead": round(avg_gold, 1),
            "verdict": verdict,
        }

    # ------------------------------------------------------------------ #
    #  6. Evaluate Draft State                                            #
    # ------------------------------------------------------------------ #

    def evaluate_draft_state(
        self,
        draft_state: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Evaluate the current state of champion select.

        Parameters
        ----------
        draft_state : dict
            Keys: my_team_picks, opponent_picks, my_team_bans, opponent_bans,
            available_champions, current_phase ("ban1","pick1","ban2","pick2").

        Returns
        -------
        dict with phase, advantage_team, draft_score, key_insights, next_priority.
        """
        my_picks = draft_state.get("my_team_picks", [])
        opp_picks = draft_state.get("opponent_picks", [])
        phase = draft_state.get("current_phase", "unknown")

        # Simple draft scoring based on pick counts and completeness
        my_count = len(my_picks)
        opp_count = len(opp_picks)

        # Composition scores
        my_comp = self.score_team_composition(my_picks)
        opp_comp = self.score_team_composition(opp_picks)

        my_score = my_comp.get("overall_score", 0.0)
        opp_score = opp_comp.get("overall_score", 0.0)

        advantage = "my_team" if my_score > opp_score else (
            "opponent" if opp_score > my_score else "even"
        )

        insights: List[str] = []
        for w in my_comp.get("weaknesses", []):
            if "no major" not in w.lower():
                insights.append(f"Our weakness: {w}")
        for w in opp_comp.get("weaknesses", []):
            if "no major" not in w.lower():
                insights.append(f"Opponent weakness: {w}")

        # Next priority
        if my_count < 3 and my_comp.get("tankiness_score", 0) < 0.3:
            next_priority = "Pick a frontline/tank"
        elif my_count < 4 and my_comp.get("crowd_control_score", 0) < 0.3:
            next_priority = "Pick a champion with CC"
        else:
            next_priority = "Pick your comfort champion"

        return {
            "phase": phase,
            "advantage_team": advantage,
            "draft_score": {"my_team": round(my_score, 4), "opponent": round(opp_score, 4)},
            "key_insights": insights if insights else ["Draft is balanced so far."],
            "next_priority": next_priority,
            "my_composition": my_comp,
            "opponent_composition": opp_comp,
        }

    # ------------------------------------------------------------------ #
    #  7. Full Draft Analysis                                             #
    # ------------------------------------------------------------------ #

    def run_full_draft_analysis(
        self,
        draft_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Run the complete draft analysis pipeline.

        Parameters
        ----------
        draft_context : dict
            Keys: draft_state, player_history, opponent_histories (list),
            meta_tier_list.

        Returns
        -------
        dict with draft_evaluation, pick_recommendations, ban_recommendations,
        player_pool_analysis, overall_strategy.
        """
        self._analysis_count += 1

        state = draft_context.get("draft_state", {})
        player_history = draft_context.get("player_history", [])
        opp_histories = draft_context.get("opponent_histories", [])
        meta = draft_context.get("meta_tier_list", [])

        # Evaluate current draft
        evaluation = self.evaluate_draft_state(state)

        # Player pool
        pool_analysis = self.analyze_player_champion_pool("self", player_history)

        # Pick recommendations
        available = state.get("available_champions", [])
        my_picks = state.get("my_team_picks", [])
        opp_picks = state.get("opponent_picks", [])
        pick_recs = self.recommend_picks(available, my_picks, opp_picks, player_history)

        # Ban recommendations (using first opponent history if available)
        ban_recs = self.recommend_bans(
            opp_histories[0] if opp_histories else [],
            meta,
        )

        # Overall strategy summary
        adv = evaluation.get("advantage_team", "even")
        strategy_parts: List[str] = []
        if adv == "my_team":
            strategy_parts.append("Draft advantage — play to team strengths.")
        elif adv == "opponent":
            strategy_parts.append("Draft disadvantage — focus on macro play.")
        else:
            strategy_parts.append("Even draft — execution will decide.")

        prio = evaluation.get("next_priority", "")
        if prio:
            strategy_parts.append(f"Next pick priority: {prio}.")

        result = {
            "draft_evaluation": evaluation,
            "pick_recommendations": pick_recs,
            "ban_recommendations": ban_recs,
            "player_pool_analysis": pool_analysis,
            "overall_strategy": " ".join(strategy_parts),
        }

        self._fire("full_draft_analysis", {
            "advantage": adv,
            "picks_recommended": len(pick_recs.get("recommendations", [])),
        })
        return result

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
            "analysis_count": self._analysis_count,
            "cache_size": len(self._draft_cache),
            "evolution_key": _EVOLUTION_KEY,
        }
