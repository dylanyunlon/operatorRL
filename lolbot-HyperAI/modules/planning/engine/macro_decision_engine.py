"""MacroDecisionEngine — Claude25 extraction from planning_component.py. Verbatim."""
from __future__ import annotations
import time
from typing import Optional
from modules.common.adapters.game_messages import GamePhase, GameSnapshot, StrategyAdvice, TeamSide, WinPrediction

class MacroDecisionEngine:
    def __init__(self) -> None: self._last_advice_time: float = 0.0; self._cooldown_sec: float = 5.0
    def decide(self, snapshot: GameSnapshot, win_pred: Optional[WinPrediction] = None) -> Optional[StrategyAdvice]:
        now = time.monotonic()
        if now - self._last_advice_time < self._cooldown_sec: return None
        p = snapshot.phase
        if p == GamePhase.EARLY: a = self._early(snapshot, win_pred)
        elif p == GamePhase.MID: a = self._mid(snapshot, win_pred)
        elif p in (GamePhase.LATE, GamePhase.ENDING): a = self._late(snapshot, win_pred)
        else: return None
        if a is not None: self._last_advice_time = now
        return a
    def _early(self, s: GameSnapshot, wp: Optional[WinPrediction]) -> Optional[StrategyAdvice]:
        our = s.blue_team if s.active_team==TeamSide.BLUE else s.red_team
        their = s.red_team if s.active_team==TeamSide.BLUE else s.blue_team
        kd = our.total_kills - their.total_kills
        if kd<=-3: return self._mk("play_safe","We're behind in kills. Focus on safe farming and vision.",0.7,s.game_time)
        if kd>=3: return self._mk("press_advantage","Kill lead — look for aggressive plays and invades.",0.6,s.game_time)
        return None
    def _mid(self, s: GameSnapshot, wp: Optional[WinPrediction]) -> Optional[StrategyAdvice]:
        if wp and wp.blue_win_prob is not None:
            p = wp.blue_win_prob; p = 1.0-p if s.active_team==TeamSide.RED else p
            if p<0.35: return self._mk("defend_and_scale","We're behind. Avoid fights, farm safely, wait for power spikes.",0.8,s.game_time)
            if p>0.65: return self._mk("force_objectives","We're ahead. Group for dragon/baron and force fights.",0.7,s.game_time)
        return None
    def _late(self, s: GameSnapshot, wp: Optional[WinPrediction]) -> Optional[StrategyAdvice]:
        their = s.red_team if s.active_team==TeamSide.BLUE else s.blue_team
        td = sum(1 for p in their.players if p.is_dead)
        if td>=2: return self._mk("push_advantage",f"{td} enemies dead — take baron or push for inhibitor!",0.9,s.game_time)
        return None
    def _mk(self, rec: str, txt: str, conf: float, gt: float) -> StrategyAdvice:
        return StrategyAdvice(primary_action=rec, reasoning=txt, confidence=conf, urgency=0.8 if conf>0.7 else 0.4, game_time=gt)
