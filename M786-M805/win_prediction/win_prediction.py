#!/usr/bin/env python3
"""
M793: Win Prediction
====================
查看现有胜率预测模型的实现方式,理解其模式。
从 logistic regression baseline 开始。

Reference: operatorRL agentic system / Seraphine LCU patterns
"""

import os, sys, json, time, math, hashlib, sqlite3, threading, logging, struct, re
from enum import Enum
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple, Union, Set, Callable
from dataclasses import dataclass, field, asdict
from collections import defaultdict, Counter, OrderedDict, deque

sys.path.insert(0, str(Path(__file__).parent.parent))
try:
    from logging_system.core_logger import get_logger, EventCategory
except ImportError:
    get_logger = lambda x: logging.getLogger(x)
    EventCategory = type('E', (), dict(SYSTEM='system', DATA='data',
        NETWORK='network', PERF='performance'))()


# Constants
FEATURE_NAMES = [
    "gold_diff","cs_diff","kda_diff","vision_diff","level_diff",
    "tower_diff","dragon_diff","baron_diff","kill_diff","death_diff",
    "assist_diff","damage_diff","objective_score_diff",
    "team_comp_score","historical_winrate","tilt_score",
]
PREDICTION_INTERVAL_SEC = 30
CONFIDENCE_CALIBRATION_BINS = 10
MIN_FEATURES_REQUIRED = 5

class PredictionResult:
    def __init__(self, win_prob=0.5, confidence=0.5, factors=None, timestamp=0):
        self.win_probability = win_prob
        self.confidence = confidence
        self.key_factors = factors or []
        self.timestamp = timestamp
        self.prediction_id = hashlib.sha256(f"{timestamp}:{win_prob}".encode()).hexdigest()[:12]

    def to_dict(self):
        return {"win_probability": self.win_probability, "confidence": self.confidence,
                "key_factors": self.key_factors, "timestamp": self.timestamp,
                "prediction_id": self.prediction_id}

class FeatureExtractor:
    """Extracts prediction features from game state data."""
    def __init__(self, logger=None):
        self._logger = logger
        self._feature_cache = {}

    def extract(self, game_state: Dict) -> Dict[str, float]:
        features = {}
        player = game_state.get("player", {})
        opponent = game_state.get("opponent", {})
        team = game_state.get("team", {})
        enemy_team = game_state.get("enemy_team", {})

        features["gold_diff"] = player.get("gold", 0) - opponent.get("gold", 0)
        features["cs_diff"] = player.get("cs", 0) - opponent.get("cs", 0)
        features["level_diff"] = player.get("level", 1) - opponent.get("level", 1)
        features["kda_diff"] = (player.get("kda_ratio", 2) - opponent.get("kda_ratio", 2))

        features["kill_diff"] = team.get("kills", 0) - enemy_team.get("kills", 0)
        features["tower_diff"] = team.get("towers", 0) - enemy_team.get("towers", 0)
        features["dragon_diff"] = team.get("dragons", 0) - enemy_team.get("dragons", 0)
        features["baron_diff"] = team.get("barons", 0) - enemy_team.get("barons", 0)

        features["team_comp_score"] = game_state.get("comp_score", 50)
        features["historical_winrate"] = game_state.get("historical_wr", 50)
        features["tilt_score"] = game_state.get("tilt_score", 0)

        features["vision_diff"] = (team.get("vision_score", 0) -
                                   enemy_team.get("vision_score", 0))
        features["damage_diff"] = (team.get("total_damage", 0) -
                                   enemy_team.get("total_damage", 0))
        features["objective_score_diff"] = (team.get("obj_score", 0) -
                                            enemy_team.get("obj_score", 0))
        return features

    def normalize(self, features: Dict[str, float]) -> Dict[str, float]:
        norms = {"gold_diff": 10000, "cs_diff": 100, "level_diff": 5,
                 "kda_diff": 5, "kill_diff": 20, "tower_diff": 11,
                 "dragon_diff": 4, "baron_diff": 2, "vision_diff": 50,
                 "damage_diff": 50000, "objective_score_diff": 20,
                 "team_comp_score": 100, "historical_winrate": 100, "tilt_score": 10}
        result = {}
        for k, v in features.items():
            norm = norms.get(k, 1)
            result[k] = max(-1, min(1, v / norm))
        return result

class LogisticPredictor:
    """Logistic regression model for win prediction."""
    def __init__(self):
        self._weights = {
            "gold_diff": 0.25, "cs_diff": 0.08, "kda_diff": 0.15,
            "vision_diff": 0.05, "level_diff": 0.12, "tower_diff": 0.20,
            "dragon_diff": 0.18, "baron_diff": 0.22, "kill_diff": 0.10,
            "damage_diff": 0.06, "objective_score_diff": 0.15,
            "team_comp_score": 0.10, "historical_winrate": 0.12, "tilt_score": -0.08,
        }
        self._bias = 0.0

    def predict(self, features: Dict[str, float]) -> float:
        z = self._bias
        for k, w in self._weights.items():
            z += w * features.get(k, 0)
        prob = 1 / (1 + math.exp(-z))
        return round(prob, 4)

    def update_weights(self, new_weights: Dict[str, float]):
        self._weights.update(new_weights)

class ConfidenceCalibrator:
    """Calibrates prediction confidence based on feature completeness."""
    def __init__(self):
        self._history: List[Tuple[float, bool]] = []

    def calibrate(self, raw_prob: float, features: Dict[str, float]) -> float:
        populated = sum(1 for v in features.values() if v != 0)
        completeness = populated / max(len(features), 1)
        if completeness < 0.3:
            return 0.2
        base_conf = min(completeness, 0.9)
        extremity = abs(raw_prob - 0.5) * 2
        return round(base_conf * (0.5 + 0.5 * extremity), 3)

    def add_outcome(self, predicted_prob: float, actual_win: bool):
        self._history.append((predicted_prob, actual_win))

    def get_calibration_stats(self) -> Dict:
        if not self._history:
            return {"calibrated": False}
        bins = [[] for _ in range(CONFIDENCE_CALIBRATION_BINS)]
        for prob, win in self._history:
            idx = min(int(prob * CONFIDENCE_CALIBRATION_BINS), CONFIDENCE_CALIBRATION_BINS - 1)
            bins[idx].append(1 if win else 0)
        return {
            "calibrated": True,
            "total_predictions": len(self._history),
            "bins": [{
                "range": f"{i/CONFIDENCE_CALIBRATION_BINS:.1f}-{(i+1)/CONFIDENCE_CALIBRATION_BINS:.1f}",
                "count": len(b),
                "actual_winrate": round(sum(b)/max(len(b),1)*100, 1),
            } for i, b in enumerate(bins) if b],
        }

class PredictionExplainer:
    """Explains which factors most influence the prediction."""
    def __init__(self):
        self._factor_names = {
            "gold_diff": "Gold advantage",
            "tower_diff": "Tower control",
            "dragon_diff": "Dragon control",
            "baron_diff": "Baron control",
            "kda_diff": "KDA differential",
            "cs_diff": "CS differential",
            "level_diff": "Level advantage",
            "kill_diff": "Kill advantage",
            "team_comp_score": "Team composition quality",
            "historical_winrate": "Historical performance",
            "tilt_score": "Player mental state",
        }

    def explain(self, features: Dict[str, float],
                weights: Dict[str, float] = None) -> List[Dict]:
        if weights is None:
            weights = {"gold_diff":0.25,"tower_diff":0.20,"dragon_diff":0.18,
                       "baron_diff":0.22,"kda_diff":0.15,"cs_diff":0.08,
                       "level_diff":0.12,"kill_diff":0.10}
        contributions = []
        for k, v in features.items():
            w = weights.get(k, 0.05)
            impact = v * w
            if abs(impact) > 0.01:
                contributions.append({
                    "factor": self._factor_names.get(k, k),
                    "value": round(v, 3),
                    "impact": round(impact, 4),
                    "direction": "positive" if impact > 0 else "negative",
                })
        return sorted(contributions, key=lambda c: abs(c["impact"]), reverse=True)[:5]

class TrendPredictor:
    """Predicts game outcome trends over time."""
    def __init__(self, logger=None):
        self._logger = logger
        self._history: List[PredictionResult] = []
        self._max_history = 100

    def add_prediction(self, result: PredictionResult):
        self._history.append(result)
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history:]

    def get_trend(self, window: int = 5) -> Dict:
        if len(self._history) < 2:
            return {"trend": "insufficient_data", "direction": "neutral"}
        recent = self._history[-window:]
        first = recent[0].win_probability
        last = recent[-1].win_probability
        change = last - first
        avg = sum(p.win_probability for p in recent) / len(recent)
        direction = "improving" if change > 0.05 else ("declining" if change < -0.05 else "stable")
        return {
            "trend": direction,
            "current_prob": round(last, 3),
            "avg_prob": round(avg, 3),
            "change": round(change, 3),
            "samples": len(recent),
            "momentum": "strong" if abs(change) > 0.15 else ("moderate" if abs(change) > 0.08 else "weak"),
        }

    def generate_voice_summary(self, trend: Dict) -> str:
        prob_pct = round(trend.get("current_prob", 0.5) * 100)
        direction = trend.get("trend", "stable")
        if prob_pct > 70:
            return f"We're looking strong at {prob_pct}% win probability. Keep it up!"
        elif prob_pct > 55:
            return f"Slightly ahead at {prob_pct}%. Maintain pressure."
        elif prob_pct > 45:
            return f"Game is close at {prob_pct}%. Focus on objectives."
        elif prob_pct > 30:
            return f"We're behind at {prob_pct}%. Play safe and look for picks."
        else:
            return f"Tough situation at {prob_pct}%. Focus on not falling further behind."

class WinPredictionEngine:
    """Primary win prediction engine coordinating all sub-components."""
    def __init__(self, logger=None):
        self._logger = logger or (get_logger("M793") if callable(get_logger)
                                  else logging.getLogger("M793"))
        self._feature_extractor = FeatureExtractor(self._logger)
        self._predictor = LogisticPredictor()
        self._calibrator = ConfidenceCalibrator()
        self._explainer = PredictionExplainer()
        self._trend_predictor = TrendPredictor(self._logger)
        self._prediction_count = 0
        self._lock = threading.Lock()

    def predict(self, game_state: Dict) -> PredictionResult:
        with self._lock:
            self._prediction_count += 1
        features = self._feature_extractor.extract(game_state)
        normalized = self._feature_extractor.normalize(features)
        raw_prob = self._predictor.predict(normalized)
        confidence = self._calibrator.calibrate(raw_prob, normalized)
        factors = self._explainer.explain(normalized)
        result = PredictionResult(
            win_prob=raw_prob, confidence=confidence,
            factors=factors, timestamp=int(time.time()))
        self._trend_predictor.add_prediction(result)
        return result

    def get_trend(self) -> Dict:
        return self._trend_predictor.get_trend()

    def get_voice_summary(self) -> str:
        trend = self.get_trend()
        return self._trend_predictor.generate_voice_summary(trend)

    def record_outcome(self, predicted_prob: float, won: bool):
        self._calibrator.add_outcome(predicted_prob, won)

    def get_calibration(self) -> Dict:
        return self._calibrator.get_calibration_stats()

    @property
    def prediction_count(self): return self._prediction_count

    def get_full_report(self, game_state: Dict) -> Dict:
        result = self.predict(game_state)
        trend = self.get_trend()
        return {
            "prediction": result.to_dict(),
            "trend": trend,
            "voice_summary": self._trend_predictor.generate_voice_summary(trend),
            "calibration": self.get_calibration(),
            "total_predictions": self._prediction_count,
        }


# ============================================================================
# Momentum Tracker
# ============================================================================

class MomentumTracker:
    """Tracks game momentum shifts over time windows."""

    def __init__(self, window_size: int = 5, logger=None):
        self._logger = logger
        self._window = window_size
        self._snapshots: deque = deque(maxlen=200)

    def add_snapshot(self, gold_diff: int, kill_diff: int,
                     objective_diff: int, timestamp: float):
        self._snapshots.append({
            "gold_diff": gold_diff,
            "kill_diff": kill_diff,
            "obj_diff": objective_diff,
            "timestamp": timestamp,
            "composite": gold_diff * 0.4 + kill_diff * 500 * 0.3 + objective_diff * 1000 * 0.3,
        })

    def get_momentum(self) -> Dict:
        if len(self._snapshots) < 2:
            return {"momentum": "neutral", "score": 0, "direction": "flat"}

        recent = list(self._snapshots)[-self._window:]
        older = list(self._snapshots)[-self._window * 2:-self._window]
        if not older:
            older = [self._snapshots[0]]

        recent_avg = sum(s["composite"] for s in recent) / len(recent)
        older_avg = sum(s["composite"] for s in older) / len(older)
        delta = recent_avg - older_avg

        if delta > 2000:
            momentum = "strong_positive"
        elif delta > 500:
            momentum = "positive"
        elif delta > -500:
            momentum = "neutral"
        elif delta > -2000:
            momentum = "negative"
        else:
            momentum = "strong_negative"

        consecutive_positive = 0
        consecutive_negative = 0
        for i in range(len(recent) - 1, 0, -1):
            diff = recent[i]["composite"] - recent[i - 1]["composite"]
            if diff > 0:
                consecutive_positive += 1
            elif diff < 0:
                consecutive_negative += 1
            else:
                break

        return {
            "momentum": momentum,
            "score": round(delta, 0),
            "direction": "improving" if delta > 0 else ("declining" if delta < 0 else "flat"),
            "consecutive_gains": consecutive_positive,
            "consecutive_losses": consecutive_negative,
            "recent_composite": round(recent_avg, 0),
        }

    def detect_shift(self) -> Optional[Dict]:
        if len(self._snapshots) < 6:
            return None
        recent_3 = list(self._snapshots)[-3:]
        older_3 = list(self._snapshots)[-6:-3]
        r_avg = sum(s["composite"] for s in recent_3) / 3
        o_avg = sum(s["composite"] for s in older_3) / 3
        shift = r_avg - o_avg
        if abs(shift) > 3000:
            return {
                "shift_detected": True,
                "magnitude": round(abs(shift), 0),
                "direction": "positive_shift" if shift > 0 else "negative_shift",
                "at_timestamp": recent_3[-1]["timestamp"],
            }
        return None


# ============================================================================
# Comeback Detector
# ============================================================================

class ComebackDetector:
    """Detects comeback potential based on team composition and game state."""

    def __init__(self, logger=None):
        self._logger = logger

    def assess_comeback(self, game_state: Dict,
                        comp_scaling: str = "mid",
                        current_minute: int = 15) -> Dict:
        gold_diff = (game_state.get("team", {}).get("gold", 0) -
                     game_state.get("enemy_team", {}).get("gold", 0))
        kill_diff = (game_state.get("team", {}).get("kills", 0) -
                     game_state.get("enemy_team", {}).get("kills", 0))
        tower_diff = (game_state.get("team", {}).get("towers", 0) -
                      game_state.get("enemy_team", {}).get("towers", 0))

        if gold_diff >= 0:
            return {"comeback_needed": False, "status": "winning_or_even"}

        deficit = abs(gold_diff)
        comeback_score = 50.0

        if comp_scaling == "late" and current_minute < 25:
            comeback_score += 20
        elif comp_scaling == "late" and current_minute < 35:
            comeback_score += 10
        elif comp_scaling == "early" and current_minute > 25:
            comeback_score -= 15

        if deficit < 3000:
            comeback_score += 15
        elif deficit < 5000:
            comeback_score += 5
        elif deficit < 8000:
            comeback_score -= 10
        else:
            comeback_score -= 25

        if tower_diff >= -2:
            comeback_score += 5
        elif tower_diff <= -5:
            comeback_score -= 15

        baron_available = current_minute >= 20
        if baron_available:
            comeback_score += 10

        comeback_score = max(0, min(100, comeback_score))
        strategies = []
        if baron_available:
            strategies.append("Look for a Baron steal or sneak")
        if comp_scaling == "late":
            strategies.append("Stall and farm safely until item spikes")
        if deficit < 5000:
            strategies.append("Win one teamfight to close the gap")
        strategies.append("Catch overextending enemies in your jungle")

        return {
            "comeback_needed": True,
            "deficit_gold": deficit,
            "comeback_score": round(comeback_score, 1),
            "feasibility": "high" if comeback_score > 65 else (
                "medium" if comeback_score > 40 else "low"),
            "strategies": strategies,
            "game_minute": current_minute,
        }


# ============================================================================
# Prediction History DB
# ============================================================================

class PredictionHistoryDB:
    """Stores prediction history for model improvement."""

    def __init__(self, db_path: Optional[Path] = None, logger=None):
        self._logger = logger
        self._db_path = db_path or Path(__file__).parent / "predictions.db"
        self._init_db()

    def _init_db(self):
        os.makedirs(self._db_path.parent, exist_ok=True)
        conn = sqlite3.connect(str(self._db_path))
        conn.execute("""CREATE TABLE IF NOT EXISTS predictions (
            prediction_id TEXT PRIMARY KEY,
            game_id TEXT,
            win_probability REAL,
            confidence REAL,
            factors_json TEXT,
            actual_outcome INTEGER DEFAULT -1,
            timestamp REAL,
            game_minute INTEGER DEFAULT 0,
            created_at TEXT)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS model_performance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            accuracy REAL, brier_score REAL,
            total_predictions INTEGER,
            evaluated_at TEXT)""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pred_game ON predictions(game_id)")
        conn.commit(); conn.close()

    def store_prediction(self, result: PredictionResult,
                         game_id: str = "", game_minute: int = 0):
        try:
            conn = sqlite3.connect(str(self._db_path))
            conn.execute("INSERT OR REPLACE INTO predictions VALUES (?,?,?,?,?,?,?,?,?)",
                (result.prediction_id, game_id, result.win_probability,
                 result.confidence, json.dumps(result.key_factors),
                 -1, result.timestamp, game_minute,
                 datetime.now(timezone.utc).isoformat()))
            conn.commit(); conn.close()
        except Exception as e:
            if self._logger: self._logger.error(f"Prediction store error: {e}")

    def record_outcome(self, game_id: str, won: bool):
        try:
            conn = sqlite3.connect(str(self._db_path))
            conn.execute("UPDATE predictions SET actual_outcome = ? WHERE game_id = ?",
                (1 if won else 0, game_id))
            conn.commit(); conn.close()
        except Exception as e:
            if self._logger: self._logger.error(f"Outcome record error: {e}")

    def evaluate_model(self) -> Dict:
        conn = sqlite3.connect(str(self._db_path))
        rows = conn.execute(
            "SELECT win_probability, actual_outcome FROM predictions WHERE actual_outcome >= 0"
        ).fetchall()
        conn.close()
        if not rows:
            return {"evaluated": False, "total": 0}

        correct = 0
        brier_sum = 0.0
        for prob, outcome in rows:
            predicted_win = prob >= 0.5
            actual_win = outcome == 1
            if predicted_win == actual_win:
                correct += 1
            brier_sum += (prob - outcome) ** 2

        n = len(rows)
        accuracy = round(correct / n * 100, 1)
        brier = round(brier_sum / n, 4)

        return {
            "evaluated": True,
            "total_predictions": n,
            "accuracy_pct": accuracy,
            "brier_score": brier,
            "correct": correct,
            "incorrect": n - correct,
        }


# ============================================================================
# Game Phase Predictor
# ============================================================================

class GamePhasePredictor:
    """Predicts which game phase favors each team."""

    def __init__(self, logger=None):
        self._logger = logger

    def predict_phase_advantage(self, our_scaling: str,
                                 enemy_scaling: str,
                                 current_minute: int) -> Dict:
        phase_map = {"early": 0, "mid": 1, "late": 2}
        our_val = phase_map.get(our_scaling, 1)
        enemy_val = phase_map.get(enemy_scaling, 1)

        current_phase = "early" if current_minute < 14 else (
            "mid" if current_minute < 25 else "late")
        current_val = phase_map.get(current_phase, 1)

        our_advantage_now = abs(our_val - current_val) <= abs(enemy_val - current_val)
        urgency = "low"
        if our_scaling == "early" and current_minute > 20:
            urgency = "high"
        elif our_scaling == "late" and current_minute < 15:
            urgency = "low"
        elif our_val < current_val:
            urgency = "medium"

        return {
            "current_phase": current_phase,
            "our_scaling": our_scaling,
            "enemy_scaling": enemy_scaling,
            "we_are_favored_now": our_advantage_now,
            "urgency": urgency,
            "advice": self._phase_advice(our_scaling, current_phase),
            "time_remaining_estimate": max(0, 35 - current_minute),
        }

    def _phase_advice(self, scaling: str, current: str) -> str:
        if scaling == "early" and current == "early":
            return "This is your window. Fight for every objective."
        elif scaling == "early" and current != "early":
            return "Your advantage is fading. Force a decisive fight now."
        elif scaling == "late" and current == "early":
            return "Survive and farm. Avoid unnecessary fights."
        elif scaling == "late" and current == "late":
            return "You're at full power. Group and fight for objectives."
        return "Play to your strengths. Look for favorable trades."


def _self_test():
    print("[M793] WinPredictionEngine self-test...")
    engine = WinPredictionEngine()
    state = {
        "player": {"gold": 8000, "cs": 150, "level": 11, "kda_ratio": 4.0},
        "opponent": {"gold": 6500, "cs": 120, "level": 10, "kda_ratio": 2.0},
        "team": {"kills": 15, "towers": 3, "dragons": 2, "barons": 0,
                 "vision_score": 80, "total_damage": 120000, "obj_score": 12},
        "enemy_team": {"kills": 8, "towers": 1, "dragons": 1, "barons": 0,
                       "vision_score": 55, "total_damage": 85000, "obj_score": 5},
        "comp_score": 65, "historical_wr": 55, "tilt_score": 0,
    }
    result = engine.predict(state)
    assert 0 < result.win_probability < 1
    assert len(result.key_factors) > 0
    voice = engine.get_voice_summary()
    assert len(voice) > 0
    print(f"  Win prob: {result.win_probability*100:.1f}%")
    print(f"  Confidence: {result.confidence}")
    print(f"  Top factor: {result.key_factors[0]['factor']}")
    print(f"  Voice: {voice}")
    print("[M793] All tests passed.\n")
    return True

if __name__ == "__main__":
    _self_test()
