"""TeamfightAnalyzer — Claude25 extraction from prediction_component.py. Verbatim."""
from __future__ import annotations
import math
from modules.common.adapters.game_messages import GamePhase, GameSnapshot, TeamfightPrediction, TeamSide

class TeamfightAnalyzer:
    def analyze(self, snapshot: GameSnapshot) -> TeamfightPrediction:
        blue, red = snapshot.blue_team, snapshot.red_team
        base_likelihood = (blue.alive_count + red.alive_count) / 10.0
        phase_mult = {GamePhase.LOADING:0.0,GamePhase.EARLY:0.3,GamePhase.MID:0.7,
                      GamePhase.LATE:0.9,GamePhase.ENDING:1.0,GamePhase.POST_GAME:0.0}.get(snapshot.phase, 0.5)
        kill_boost = min(0.3, len([e for e in snapshot.new_events if e.event_type.value=="ChampionKill"])*0.1)
        likelihood = min(1.0, base_likelihood * phase_mult + kill_boost)
        fight_score = (blue.alive_count-red.alive_count)*0.15 + (blue.avg_level-red.avg_level)*0.05
        bwf = 1.0/(1.0+math.exp(-fight_score))
        if snapshot.active_player is None: action="hold"
        elif snapshot.active_team==TeamSide.BLUE:
            action="engage" if bwf>0.6 else ("disengage" if bwf<0.4 else "hold")
        else: action="engage" if bwf<0.4 else ("disengage" if bwf>0.6 else "hold")
        return TeamfightPrediction(likelihood=likelihood, blue_win_if_fight=bwf,
            recommended_action=action, reasoning=f"Alive: {blue.alive_count}v{red.alive_count}, Phase: {snapshot.phase.name}",
            game_time=snapshot.game_time)
