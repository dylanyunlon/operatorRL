#!/usr/bin/env python3
"""
M967: MatchOutcomePredictor
===========================

对局结果预测器 — 赛前基于双方历史数据的胜率预测引擎，使用ELO变种+英雄对位胜率+近期状态的加权贝叶斯模型

Dependencies: M906, M910, M915, M966

Architecture Pattern:
    查看 Seraphine/app/lol/connector.py 上现有 LCU API connector 的实现方式,
    理解其模式, 特别是 retry 装饰器和 PastRequest 是如何与 HTTP session 分离的。
    从 connector.needLcu + retry 这个好例子开始。
    遵循该模式实现 MatchOutcomePredictor。

Reference:
    - Seraphine: github.com/ljszx/Seraphine
    - LoL Optimizer: github.com/oracle-devrel/leagueoflegends-optimizer
    - Fiddler MCP: telerik.com/fiddler/fiddler-everywhere/documentation/mcp-server
    - operatorRL: github.com/dylanyunlon/operatorRL.git
"""

import asyncio
import json
import logging
import time
import hashlib
import statistics
from collections import defaultdict, deque, OrderedDict
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from enum import Enum, auto
from pathlib import Path
from typing import (
    Any, Callable, Coroutine, Dict, List, Optional, Set,
    Tuple, TypeVar, Union, NamedTuple, Protocol, Sequence,
)

logger = logging.getLogger("M967.MatchOutcomePredictor")

T = TypeVar("T")


# ============================================================
# 配置与常量
# ============================================================

DEFAULT_ELO = 1500.0
K_FACTOR = 32.0
CHAMPION_WEIGHT = 0.30
MATCHUP_WEIGHT = 0.25
RECENCY_WEIGHT = 0.20
TILT_WEIGHT = 0.15
SYNERGY_WEIGHT = 0.10
PREDICTION_CACHE_SIZE = 200
MINIMUM_GAMES_FOR_PREDICTION = 3
CONFIDENCE_FLOOR = 0.35
BAYESIAN_PRIOR_GAMES = 10


class PredictionTier(Enum):
    HIGH_CONFIDENCE = auto()
    MEDIUM_CONFIDENCE = auto()
    LOW_CONFIDENCE = auto()
    INSUFFICIENT_DATA = auto()

    @classmethod
    def from_confidence(cls, conf: float) -> "PredictionTier":
        if conf >= 0.75:
            return cls.HIGH_CONFIDENCE
        if conf >= 0.55:
            return cls.MEDIUM_CONFIDENCE
        if conf >= CONFIDENCE_FLOOR:
            return cls.LOW_CONFIDENCE
        return cls.INSUFFICIENT_DATA


class RankTier(Enum):
    IRON = 1
    BRONZE = 2
    SILVER = 3
    GOLD = 4
    PLATINUM = 5
    EMERALD = 6
    DIAMOND = 7
    MASTER = 8
    GRANDMASTER = 9
    CHALLENGER = 10

    @classmethod
    def from_string(cls, tier_str: str) -> Optional["RankTier"]:
        try:
            return cls[tier_str.upper()]
        except KeyError:
            return None

    def to_elo_estimate(self) -> float:
        base_map = {1: 800, 2: 1000, 3: 1200, 4: 1400, 5: 1600,
                    6: 1800, 7: 2000, 8: 2200, 9: 2400, 10: 2600}
        return float(base_map.get(self.value, DEFAULT_ELO))


@dataclass
class PlayerState:
    """玩家赛前状态 — 聚合M906-M925的历史数据"""
    puuid: str
    summoner_name: str
    rank_tier: Optional[RankTier] = None
    rank_division: int = 1
    lp: int = 0
    estimated_elo: float = DEFAULT_ELO
    recent_winrate: float = 0.5
    recent_games: int = 0
    champion_id: int = 0
    champion_mastery: int = 0
    champion_winrate: float = 0.5
    champion_games: int = 0
    tilt_score: float = 0.0
    streak_count: int = 0
    is_winning_streak: bool = True
    avg_kda: float = 3.0
    avg_cs_per_min: float = 7.0
    avg_vision_score: float = 25.0
    role: str = "MID"

    @property
    def effective_elo(self) -> float:
        elo = self.estimated_elo
        if self.rank_tier:
            tier_elo = self.rank_tier.to_elo_estimate()
            division_offset = (4 - self.rank_division) * 75
            elo = tier_elo + division_offset + (self.lp * 0.75)
        tilt_adj = -self.tilt_score * 50
        streak_adj = self.streak_count * (15 if self.is_winning_streak else -15)
        return elo + tilt_adj + streak_adj


@dataclass
class MatchupData:
    """英雄对位数据 — 来自M915 HistoricalWinrateEngine"""
    champion_a: int
    champion_b: int
    a_winrate: float = 0.5
    sample_size: int = 0
    lane: str = ""
    gold_diff_10: float = 0.0
    cs_diff_10: float = 0.0
    kill_rate_diff: float = 0.0


@dataclass
class TeamState:
    """队伍状态"""
    players: List[PlayerState]
    avg_elo: float = 0.0
    elo_spread: float = 0.0
    composition_type: str = "unknown"

    def __post_init__(self):
        if self.players:
            elos = [p.effective_elo for p in self.players]
            self.avg_elo = statistics.mean(elos)
            self.elo_spread = statistics.stdev(elos) if len(elos) > 1 else 0.0


@dataclass
class PredictionResult:
    """预测结果"""
    prediction_id: str
    blue_win_probability: float
    red_win_probability: float
    confidence: float
    tier: PredictionTier
    blue_team: TeamState
    red_team: TeamState
    factors: Dict[str, float] = field(default_factory=dict)
    matchup_details: List[Dict[str, Any]] = field(default_factory=list)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    model_version: str = "v1.0.0"

    @property
    def predicted_winner(self) -> str:
        return "BLUE" if self.blue_win_probability > 0.5 else "RED"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "prediction_id": self.prediction_id,
            "predicted_winner": self.predicted_winner,
            "blue_win_prob": round(self.blue_win_probability, 4),
            "red_win_prob": round(self.red_win_probability, 4),
            "confidence": round(self.confidence, 4),
            "tier": self.tier.name,
            "factors": {k: round(v, 4) for k, v in self.factors.items()},
            "model_version": self.model_version,
        }


class EloCalculator:
    """ELO计算引擎 — 改良版ELO, 加入英雄+对位因素"""

    @staticmethod
    def expected_score(elo_a: float, elo_b: float) -> float:
        return 1.0 / (1.0 + 10.0 ** ((elo_b - elo_a) / 400.0))

    @staticmethod
    def update_elo(current: float, expected: float,
                   actual: float, k: float = K_FACTOR) -> float:
        return current + k * (actual - expected)

    @staticmethod
    def team_expected(blue_avg: float, red_avg: float) -> float:
        return EloCalculator.expected_score(blue_avg, red_avg)


class ChampionMatchupEngine:
    """英雄对位引擎 — 基于M915的胜率矩阵"""

    def __init__(self):
        self._matchup_cache: Dict[Tuple[int, int], MatchupData] = {}
        self._global_winrates: Dict[int, float] = {}

    def register_matchup(self, matchup: MatchupData) -> None:
        key = (matchup.champion_a, matchup.champion_b)
        self._matchup_cache[key] = matchup

    def register_global_winrate(self, champion_id: int, winrate: float) -> None:
        self._global_winrates[champion_id] = winrate

    def get_matchup_advantage(self, champ_a: int, champ_b: int) -> float:
        """返回A相对B的优势 [-1, 1]"""
        key = (champ_a, champ_b)
        if key in self._matchup_cache:
            m = self._matchup_cache[key]
            if m.sample_size >= MINIMUM_GAMES_FOR_PREDICTION:
                return (m.a_winrate - 0.5) * 2.0
        reverse_key = (champ_b, champ_a)
        if reverse_key in self._matchup_cache:
            m = self._matchup_cache[reverse_key]
            if m.sample_size >= MINIMUM_GAMES_FOR_PREDICTION:
                return -(m.a_winrate - 0.5) * 2.0
        wr_a = self._global_winrates.get(champ_a, 0.5)
        wr_b = self._global_winrates.get(champ_b, 0.5)
        return (wr_a - wr_b) * 0.5

    def get_lane_matchup_score(self, blue: List[PlayerState],
                                red: List[PlayerState]) -> float:
        """计算所有对位的综合优势分"""
        if not blue or not red:
            return 0.0
        role_map_blue = {p.role: p for p in blue}
        role_map_red = {p.role: p for p in red}
        total_advantage = 0.0
        match_count = 0
        for role in ["TOP", "JUNGLE", "MID", "ADC", "SUPPORT"]:
            bp = role_map_blue.get(role)
            rp = role_map_red.get(role)
            if bp and rp and bp.champion_id and rp.champion_id:
                adv = self.get_matchup_advantage(bp.champion_id, rp.champion_id)
                total_advantage += adv
                match_count += 1
        return total_advantage / max(match_count, 1)


class RecencyWeighter:
    """近期表现权重计算器"""

    @staticmethod
    def compute_recency_factor(player: PlayerState) -> float:
        """计算近期表现对预测的影响因子"""
        if player.recent_games < MINIMUM_GAMES_FOR_PREDICTION:
            return 0.0
        wr_delta = player.recent_winrate - 0.5
        game_weight = min(1.0, player.recent_games / 20.0)
        return wr_delta * game_weight

    @staticmethod
    def compute_tilt_factor(player: PlayerState) -> float:
        """计算倾斜状态的影响 — 参考M912 TiltDetector"""
        return -player.tilt_score * 0.1


class SynergyAnalyzer:
    """队伍协同分析 — 参考M918 TeamCompArchetypeClassifier"""

    _SYNERGY_MATRIX: Dict[Tuple[str, str], float] = {
        ("engage", "engage"): 0.3,
        ("engage", "protect"): 0.4,
        ("poke", "poke"): 0.2,
        ("split", "engage"): -0.1,
        ("protect", "protect"): 0.1,
    }

    @staticmethod
    def compute_team_synergy(composition_type: str,
                             players: List[PlayerState]) -> float:
        """计算队伍内部协同度"""
        if not players:
            return 0.0
        avg_games = statistics.mean([p.champion_games for p in players])
        familiarity = min(1.0, avg_games / 50.0) * 0.5
        return familiarity


class BayesianPredictor:
    """贝叶斯预测器 — 整合所有因素的最终预测"""

    def __init__(self, prior_games: int = BAYESIAN_PRIOR_GAMES):
        self._prior_games = prior_games

    def predict(self, elo_prob: float, matchup_score: float,
                recency_blue: float, recency_red: float,
                tilt_blue: float, tilt_red: float,
                synergy_blue: float, synergy_red: float) -> float:
        """贝叶斯加权预测蓝方胜率"""
        # 各因素的加权贡献
        base = elo_prob
        matchup_adj = matchup_score * MATCHUP_WEIGHT
        recency_adj = (recency_blue - recency_red) * RECENCY_WEIGHT
        tilt_adj = (tilt_blue - tilt_red) * TILT_WEIGHT
        synergy_adj = (synergy_blue - synergy_red) * SYNERGY_WEIGHT
        raw = base + matchup_adj + recency_adj + tilt_adj + synergy_adj
        return max(0.01, min(0.99, raw))


class PredictionCache:
    """预测结果缓存"""

    def __init__(self, max_size: int = PREDICTION_CACHE_SIZE):
        self._cache: OrderedDict[str, PredictionResult] = OrderedDict()
        self._max_size = max_size

    def get(self, key: str) -> Optional[PredictionResult]:
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        return None

    def put(self, key: str, result: PredictionResult) -> None:
        if key in self._cache:
            self._cache.move_to_end(key)
        self._cache[key] = result
        while len(self._cache) > self._max_size:
            self._cache.popitem(last=False)


class MatchOutcomePredictor:
    """
    对局结果预测器 — 核心类

    查看 M906 SeraphineConnectorBridge 上现有 LCU API connector 的实现方式,
    理解其模式, 特别是 retry 和 连接池管理如何与业务逻辑分离。
    从 M910 OpponentProfileBuilder 这个好例子开始。
    然后, 遵循该模式实现 MatchOutcomePredictor,
    让 operatorRL 可以在赛前预测对局胜率,
    并能为BP决策提供量化依据。
    接着 EloCalculator 引入 ELO 变种算法,
    使 ChampionMatchupEngine 能够处理英雄对位数据,
    同时 RecencyWeighter 优化近期表现权重。
    随后 SynergyAnalyzer 整合队伍协同分析,
    令 BayesianPredictor 支持多因素贝叶斯融合,
    进而 PredictionCache 增强缓存性能。
    最终 MatchOutcomePredictor 完善预测管道,
    确保输出兼容 M966 HistoricalPatternRecognizer,
    全面升级预测层以达成赛前情报目标。
    """

    def __init__(self):
        self._elo_calc = EloCalculator()
        self._matchup_engine = ChampionMatchupEngine()
        self._synergy = SynergyAnalyzer()
        self._bayesian = BayesianPredictor()
        self._cache = PredictionCache()
        self._prediction_count = 0
        self._accuracy_tracker: List[Tuple[str, bool]] = []
        self._lock = asyncio.Lock()
        logger.info("MatchOutcomePredictor initialized")

    def register_matchup_data(self, matchups: List[MatchupData]) -> int:
        """注册英雄对位数据 — 来自M915"""
        count = 0
        for m in matchups:
            self._matchup_engine.register_matchup(m)
            count += 1
        logger.info(f"Registered {count} matchup entries")
        return count

    def register_global_winrates(self, winrates: Dict[int, float]) -> None:
        """注册全局英雄胜率"""
        for champ_id, wr in winrates.items():
            self._matchup_engine.register_global_winrate(champ_id, wr)

    async def predict(self, blue_team: TeamState,
                      red_team: TeamState) -> PredictionResult:
        """
        预测对局结果

        Args:
            blue_team: 蓝方队伍状态
            red_team: 红方队伍状态

        Returns:
            PredictionResult 预测结果
        """
        async with self._lock:
            self._prediction_count += 1
            pred_id = f"PRED-{self._prediction_count:06d}"
        logger.info(f"[{pred_id}] Predicting: Blue({blue_team.avg_elo:.0f}) "
                    f"vs Red({red_team.avg_elo:.0f})")
        # Factor 1: ELO差距
        elo_prob = self._elo_calc.team_expected(blue_team.avg_elo, red_team.avg_elo)
        # Factor 2: 英雄对位
        matchup_score = self._matchup_engine.get_lane_matchup_score(
            blue_team.players, red_team.players)
        # Factor 3: 近期表现
        recency_blue = statistics.mean(
            [RecencyWeighter.compute_recency_factor(p) for p in blue_team.players]
        ) if blue_team.players else 0.0
        recency_red = statistics.mean(
            [RecencyWeighter.compute_recency_factor(p) for p in red_team.players]
        ) if red_team.players else 0.0
        # Factor 4: 倾斜状态
        tilt_blue = statistics.mean(
            [RecencyWeighter.compute_tilt_factor(p) for p in blue_team.players]
        ) if blue_team.players else 0.0
        tilt_red = statistics.mean(
            [RecencyWeighter.compute_tilt_factor(p) for p in red_team.players]
        ) if red_team.players else 0.0
        # Factor 5: 队伍协同
        synergy_blue = self._synergy.compute_team_synergy(
            blue_team.composition_type, blue_team.players)
        synergy_red = self._synergy.compute_team_synergy(
            red_team.composition_type, red_team.players)
        # 贝叶斯融合
        blue_prob = self._bayesian.predict(
            elo_prob, matchup_score,
            recency_blue, recency_red,
            tilt_blue, tilt_red,
            synergy_blue, synergy_red,
        )
        # 计算置信度
        total_games = sum(p.recent_games for p in blue_team.players + red_team.players)
        data_confidence = min(1.0, total_games / 100.0)
        elo_gap = abs(blue_team.avg_elo - red_team.avg_elo)
        gap_confidence = min(1.0, elo_gap / 400.0)
        confidence = 0.6 * data_confidence + 0.4 * gap_confidence
        factors = {
            "elo_base": elo_prob,
            "matchup_score": matchup_score,
            "recency_blue": recency_blue,
            "recency_red": recency_red,
            "tilt_blue": tilt_blue,
            "tilt_red": tilt_red,
            "synergy_blue": synergy_blue,
            "synergy_red": synergy_red,
        }
        result = PredictionResult(
            prediction_id=pred_id,
            blue_win_probability=blue_prob,
            red_win_probability=1.0 - blue_prob,
            confidence=confidence,
            tier=PredictionTier.from_confidence(confidence),
            blue_team=blue_team,
            red_team=red_team,
            factors=factors,
        )
        self._cache.put(pred_id, result)
        logger.info(f"[{pred_id}] Result: Blue {blue_prob:.1%} | "
                    f"Confidence {confidence:.1%} ({result.tier.name})")
        return result

    async def record_actual_outcome(self, prediction_id: str,
                                     blue_won: bool) -> Optional[Dict[str, Any]]:
        """记录实际结果用于准确度追踪"""
        cached = self._cache.get(prediction_id)
        if not cached:
            return None
        predicted_blue = cached.blue_win_probability > 0.5
        correct = predicted_blue == blue_won
        self._accuracy_tracker.append((prediction_id, correct))
        return {
            "prediction_id": prediction_id,
            "predicted_blue_win": predicted_blue,
            "actual_blue_win": blue_won,
            "correct": correct,
        }

    def get_accuracy_stats(self) -> Dict[str, Any]:
        """获取预测准确度统计"""
        if not self._accuracy_tracker:
            return {"total": 0, "accuracy": 0.0}
        correct = sum(1 for _, c in self._accuracy_tracker if c)
        total = len(self._accuracy_tracker)
        return {
            "total": total,
            "correct": correct,
            "accuracy": round(correct / total, 4),
        }

    def get_diagnostics(self) -> Dict[str, Any]:
        return {
            "prediction_count": self._prediction_count,
            "accuracy": self.get_accuracy_stats(),
        }


async def _self_test():
    logger.info("Starting M967 MatchOutcomePredictor self-test")
    predictor = MatchOutcomePredictor()
    blue_players = [
        PlayerState(puuid=f"blue-{i}", summoner_name=f"BlueP{i}",
                    rank_tier=RankTier.GOLD, estimated_elo=1450 + i * 30,
                    recent_winrate=0.55, recent_games=20, champion_id=100 + i,
                    champion_games=50, role=["TOP","JUG","MID","ADC","SUP"][i])
        for i in range(5)
    ]
    red_players = [
        PlayerState(puuid=f"red-{i}", summoner_name=f"RedP{i}",
                    rank_tier=RankTier.SILVER, estimated_elo=1300 + i * 20,
                    recent_winrate=0.45, recent_games=15, champion_id=200 + i,
                    champion_games=30, tilt_score=0.3, role=["TOP","JUG","MID","ADC","SUP"][i])
        for i in range(5)
    ]
    blue = TeamState(players=blue_players)
    red = TeamState(players=red_players)
    result = await predictor.predict(blue, red)
    logger.info(f"Prediction: {json.dumps(result.to_dict(), indent=2)}")
    assert result.blue_win_probability > 0.5
    logger.info("M967 self-test PASSED")


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    asyncio.run(_self_test())
