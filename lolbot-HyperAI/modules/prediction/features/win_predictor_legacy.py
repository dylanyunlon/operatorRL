"""WinPredictor — Claude25 extraction from prediction_component.py. Verbatim."""
from __future__ import annotations
import math
from typing import Dict, List, Tuple
from modules.prediction.features.prediction_features import PredictionFeatures

class WinPredictor:
    _WEIGHTS: Dict[str, float] = {"gold_diff_norm":2.5,"kill_diff_norm":1.2,"tower_diff_norm":3.0,
        "dragon_diff_norm":1.5,"baron_diff":2.0,"level_advantage_norm":0.8,
        "alive_advantage_norm":1.5,"gold_trend_norm":0.6,"recent_kills_norm":0.4}
    def __init__(self, model_version: str = "heuristic-v1") -> None: self._version = model_version
    def predict(self, features: PredictionFeatures) -> float:
        s=0.0; w=self._WEIGHTS
        s+=w["gold_diff_norm"]*(features.gold_diff/10000.0); s+=w["kill_diff_norm"]*(features.kill_diff/20.0)
        s+=w["tower_diff_norm"]*(features.tower_diff/11.0); s+=w["dragon_diff_norm"]*(features.dragon_diff/4.0)
        s+=w["baron_diff"]*((features.blue_barons-features.red_barons)/3.0)
        s+=w["level_advantage_norm"]*(features.level_advantage/5.0)
        s+=w["alive_advantage_norm"]*(features.alive_advantage/5.0)
        s+=w["gold_trend_norm"]*(features.gold_trend/5000.0)
        s+=w["recent_kills_norm"]*(features.recent_kill_advantage/10.0)
        return max(0.01,min(0.99,1.0/(1.0+math.exp(-s))))
    def feature_importance(self, features: PredictionFeatures) -> List[Tuple[str, float]]:
        c=[("gold_diff",self._WEIGHTS["gold_diff_norm"]*features.gold_diff/10000.0),
           ("kill_diff",self._WEIGHTS["kill_diff_norm"]*features.kill_diff/20.0),
           ("tower_diff",self._WEIGHTS["tower_diff_norm"]*features.tower_diff/11.0),
           ("dragon_diff",self._WEIGHTS["dragon_diff_norm"]*features.dragon_diff/4.0),
           ("alive_advantage",self._WEIGHTS["alive_advantage_norm"]*features.alive_advantage/5.0)]
        c.sort(key=lambda x: abs(x[1]), reverse=True); return c
    @property
    def version(self) -> str: return self._version
