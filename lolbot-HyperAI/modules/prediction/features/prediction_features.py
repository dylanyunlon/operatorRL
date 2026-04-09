"""PredictionFeatures — Claude25 extraction from prediction_component.py. Verbatim."""
from __future__ import annotations
from dataclasses import dataclass
from typing import List, Optional, TYPE_CHECKING
if TYPE_CHECKING:
    from modules.common.adapters.game_messages import GameSnapshot

@dataclass
class PredictionFeatures:
    game_time: float = 0.0; gold_diff: float = 0.0; kill_diff: int = 0
    tower_diff: int = 0; dragon_diff: int = 0; blue_barons: int = 0; red_barons: int = 0
    blue_avg_level: float = 0.0; red_avg_level: float = 0.0
    blue_alive: int = 5; red_alive: int = 5
    gold_diff_per_min: float = 0.0; kill_rate_blue: float = 0.0; kill_rate_red: float = 0.0
    level_advantage: float = 0.0; alive_advantage: int = 0
    gold_trend: float = 0.0; recent_kill_advantage: int = 0

    @staticmethod
    def from_snapshot(snapshot: "GameSnapshot", prev_snapshot: Optional["GameSnapshot"] = None) -> "PredictionFeatures":
        fd = snapshot.to_feature_dict()
        gt = max(fd["game_time"], 1.0); gm = gt / 60.0
        f = PredictionFeatures(
            game_time=gt, gold_diff=fd["gold_diff"], kill_diff=fd["kill_diff"],
            tower_diff=fd["tower_diff"], dragon_diff=fd["dragon_diff"],
            blue_barons=fd["blue_barons"], red_barons=fd["red_barons"],
            blue_avg_level=fd["blue_avg_level"], red_avg_level=fd["red_avg_level"],
            blue_alive=fd["blue_alive"], red_alive=fd["red_alive"],
            gold_diff_per_min=fd["gold_diff"]/gm, kill_rate_blue=fd["blue_kills"]/gm,
            kill_rate_red=fd["red_kills"]/gm,
            level_advantage=fd["blue_avg_level"]-fd["red_avg_level"],
            alive_advantage=fd["blue_alive"]-fd["red_alive"],
        )
        if prev_snapshot is not None:
            pfd = prev_snapshot.to_feature_dict()
            f = PredictionFeatures(**{k: getattr(f, k) for k in f.__dataclass_fields__ if k not in ("gold_trend","recent_kill_advantage")},
                gold_trend=fd["gold_diff"]-pfd["gold_diff"], recent_kill_advantage=fd["kill_diff"]-pfd["kill_diff"])
        return f

    def to_vector(self) -> List[float]:
        return [self.game_time/3600.0, self.gold_diff/10000.0, float(self.kill_diff)/20.0,
                float(self.tower_diff)/11.0, float(self.dragon_diff)/4.0, float(self.blue_barons)/3.0,
                float(self.red_barons)/3.0, self.blue_avg_level/18.0, self.red_avg_level/18.0,
                float(self.blue_alive)/5.0, float(self.red_alive)/5.0, self.gold_diff_per_min/1000.0,
                self.level_advantage/5.0, float(self.alive_advantage)/5.0,
                self.gold_trend/5000.0, float(self.recent_kill_advantage)/10.0]
