"""
prediction/evaluator/evaluator_manager.py — 评估器管理
=======================================================

查看 Apollo modules/prediction/evaluator/ 上现有 EvaluatorManager 的实现方式,
理解其模式, 特别是不同评估器是如何通过 Manager 统一调度的。从 Apollo
EvaluatorManager::Run() 这个好例子开始。然后遵循该模式实现我们的
EvaluatorManager, 让 prediction 可以注册多个评估器 (WinProbability,
TeamFight, Objective, Draft), 并能根据游戏阶段和可用数据选择性激活。

位置: lolbot-HyperAI/modules/prediction/evaluator/evaluator_manager.py
"""

from __future__ import annotations

import abc
import logging
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Type

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Game phase enum (for evaluator activation)
# ---------------------------------------------------------------------------

class GamePhaseCategory(Enum):
    """游戏阶段分类, 用于决定激活哪些评估器."""
    PRE_GAME = auto()       # 选人阶段
    EARLY_GAME = auto()     # 0-14 分钟
    MID_GAME = auto()       # 14-25 分钟
    LATE_GAME = auto()      # 25+ 分钟
    ALL_PHASES = auto()     # 全阶段激活


def classify_game_phase(game_time_s: float) -> GamePhaseCategory:
    """根据游戏时间判断阶段."""
    if game_time_s <= 0:
        return GamePhaseCategory.PRE_GAME
    if game_time_s < 840:   # 14 min
        return GamePhaseCategory.EARLY_GAME
    if game_time_s < 1500:  # 25 min
        return GamePhaseCategory.MID_GAME
    return GamePhaseCategory.LATE_GAME


# ---------------------------------------------------------------------------
# Evaluator result
# ---------------------------------------------------------------------------

@dataclass
class EvaluatorResult:
    """单个评估器的输出.

    Attributes:
        evaluator_name: 评估器名称.
        confidence: 置信度 [0, 1].
        value: 评估值 (语义取决于评估器).
        details: 详细信息.
        latency_ms: 评估耗时.
    """
    evaluator_name: str = ""
    confidence: float = 0.0
    value: float = 0.0
    details: Dict[str, Any] = field(default_factory=dict)
    latency_ms: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "evaluator": self.evaluator_name,
            "confidence": round(self.confidence, 4),
            "value": round(self.value, 4),
            "details": self.details,
            "latency_ms": round(self.latency_ms, 2),
        }


# ---------------------------------------------------------------------------
# Abstract evaluator (= Apollo Evaluator base)
# ---------------------------------------------------------------------------

class Evaluator(abc.ABC):
    """评估器抽象基类.

    Apollo prediction 中每种评估器 (CruiseEvaluator, JunctionEvaluator 等)
    实现不同的预测逻辑。我们的评估器对应:
    - WinProbabilityEvaluator: 胜率预测
    - TeamFightEvaluator: 团战预测
    - ObjectiveEvaluator: 资源争夺预测
    - DraftEvaluator: 选人阶段预测

    子类实现:
    - name: 评估器名称
    - active_phases: 激活的游戏阶段
    - evaluate(): 执行评估
    """

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """评估器名称."""
        ...

    @property
    def active_phases(self) -> Set[GamePhaseCategory]:
        """此评估器在哪些游戏阶段激活.

        默认全阶段. 子类可覆盖.
        """
        return {GamePhaseCategory.ALL_PHASES}

    def should_activate(self, phase: GamePhaseCategory) -> bool:
        """判断是否应该在当前阶段激活."""
        if GamePhaseCategory.ALL_PHASES in self.active_phases:
            return True
        return phase in self.active_phases

    @abc.abstractmethod
    def evaluate(
        self,
        game_state: Dict[str, Any],
        features: Dict[str, Any],
    ) -> EvaluatorResult:
        """执行一次评估.

        Args:
            game_state: 当前游戏状态 (来自 perception).
            features: 提取的特征 (来自 feature_pipeline).

        Returns:
            评估结果.
        """
        ...

    def init(self) -> bool:
        """初始化 (可选). 返回 True 表示成功."""
        return True

    def shutdown(self) -> None:
        """关闭 (可选)."""
        pass

    def stats(self) -> Dict[str, Any]:
        """状态信息 (可选)."""
        return {"name": self.name}


# ---------------------------------------------------------------------------
# Built-in evaluators
# ---------------------------------------------------------------------------

class WinProbabilityEvaluator(Evaluator):
    """胜率预测评估器.

    使用基于特征的逻辑回归模型 (无外部依赖).
    """

    @property
    def name(self) -> str:
        return "win_probability"

    @property
    def active_phases(self) -> Set[GamePhaseCategory]:
        return {
            GamePhaseCategory.EARLY_GAME,
            GamePhaseCategory.MID_GAME,
            GamePhaseCategory.LATE_GAME,
        }

    def __init__(self) -> None:
        self._weights: Dict[str, float] = {
            "gold_diff_norm": 0.35,
            "kill_diff_norm": 0.20,
            "tower_diff_norm": 0.15,
            "dragon_diff_norm": 0.15,
            "level_diff_norm": 0.10,
            "cs_diff_norm": 0.05,
        }
        self._bias: float = 0.0
        self._eval_count: int = 0

    def evaluate(
        self,
        game_state: Dict[str, Any],
        features: Dict[str, Any],
    ) -> EvaluatorResult:
        self._eval_count += 1

        # 计算加权和
        score = self._bias
        for feat_name, weight in self._weights.items():
            feat_val = features.get(feat_name, 0.0)
            score += weight * feat_val

        # Sigmoid
        import math
        try:
            prob = 1.0 / (1.0 + math.exp(-score))
        except OverflowError:
            prob = 0.0 if score < 0 else 1.0

        return EvaluatorResult(
            evaluator_name=self.name,
            confidence=0.7,
            value=prob,
            details={
                "raw_score": round(score, 4),
                "features_used": list(self._weights.keys()),
            },
        )

    def set_weights(self, weights: Dict[str, float], bias: float = 0.0) -> None:
        """从 evolution 更新模型权重."""
        self._weights.update(weights)
        self._bias = bias

    def stats(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "eval_count": self._eval_count,
            "weight_count": len(self._weights),
        }


class TeamFightEvaluator(Evaluator):
    """团战预测评估器."""

    @property
    def name(self) -> str:
        return "team_fight"

    @property
    def active_phases(self) -> Set[GamePhaseCategory]:
        return {
            GamePhaseCategory.MID_GAME,
            GamePhaseCategory.LATE_GAME,
        }

    def __init__(self) -> None:
        self._eval_count: int = 0

    def evaluate(
        self,
        game_state: Dict[str, Any],
        features: Dict[str, Any],
    ) -> EvaluatorResult:
        self._eval_count += 1

        # 简单的团战胜率估算
        alive_allies = features.get("alive_allies", 5)
        alive_enemies = features.get("alive_enemies", 5)
        gold_diff = features.get("gold_diff_norm", 0.0)
        level_diff = features.get("level_diff_norm", 0.0)

        # 人数优势 + 经济优势
        numbers_advantage = (alive_allies - alive_enemies) * 0.15
        econ_advantage = gold_diff * 0.3 + level_diff * 0.1

        import math
        score = numbers_advantage + econ_advantage
        try:
            prob = 1.0 / (1.0 + math.exp(-score * 3))
        except OverflowError:
            prob = 0.0 if score < 0 else 1.0

        # 置信度取决于特征完整度
        confidence = 0.5
        if alive_allies + alive_enemies < 10:
            confidence = 0.7  # 有人死了, 信息更明确

        return EvaluatorResult(
            evaluator_name=self.name,
            confidence=confidence,
            value=prob,
            details={
                "alive_allies": alive_allies,
                "alive_enemies": alive_enemies,
                "recommend_fight": prob > 0.55 and alive_allies >= alive_enemies,
            },
        )


class ObjectiveEvaluator(Evaluator):
    """资源争夺预测评估器 (龙/男爵/先驱者)."""

    @property
    def name(self) -> str:
        return "objective"

    @property
    def active_phases(self) -> Set[GamePhaseCategory]:
        return {
            GamePhaseCategory.EARLY_GAME,
            GamePhaseCategory.MID_GAME,
            GamePhaseCategory.LATE_GAME,
        }

    def __init__(self) -> None:
        self._eval_count: int = 0

    def evaluate(
        self,
        game_state: Dict[str, Any],
        features: Dict[str, Any],
    ) -> EvaluatorResult:
        self._eval_count += 1

        game_time = features.get("game_time_s", 0.0)
        alive_allies = features.get("alive_allies", 5)
        alive_enemies = features.get("alive_enemies", 5)
        jungler_alive = features.get("jungler_alive", True)

        # 资源可用性
        objectives: List[str] = []
        if game_time >= 300:  # 5 min: 小龙
            objectives.append("dragon")
        if game_time >= 480:  # 8 min: 先驱者
            objectives.append("herald")
        if game_time >= 1200:  # 20 min: 男爵
            objectives.append("baron")

        # 是否应该争夺
        can_contest = (
            alive_allies >= alive_enemies
            and jungler_alive
        )

        priority = "none"
        if objectives:
            if "baron" in objectives and game_time >= 1200:
                priority = "baron"
            elif "dragon" in objectives:
                priority = "dragon"
            elif "herald" in objectives and game_time < 1200:
                priority = "herald"

        return EvaluatorResult(
            evaluator_name=self.name,
            confidence=0.6 if can_contest else 0.4,
            value=1.0 if can_contest and priority != "none" else 0.0,
            details={
                "available_objectives": objectives,
                "priority": priority,
                "can_contest": can_contest,
            },
        )


class DraftEvaluator(Evaluator):
    """选人阶段评估器."""

    @property
    def name(self) -> str:
        return "draft"

    @property
    def active_phases(self) -> Set[GamePhaseCategory]:
        return {GamePhaseCategory.PRE_GAME}

    def evaluate(
        self,
        game_state: Dict[str, Any],
        features: Dict[str, Any],
    ) -> EvaluatorResult:
        # 选人阶段评估: 阵容协同度
        team_champions = features.get("team_champions", [])
        enemy_champions = features.get("enemy_champions", [])

        # 简单的阵容评分 (占位实现)
        score = 0.5  # 基线

        return EvaluatorResult(
            evaluator_name=self.name,
            confidence=0.4,
            value=score,
            details={
                "team": team_champions,
                "enemy": enemy_champions,
            },
        )


# ---------------------------------------------------------------------------
# EvaluatorManager
# ---------------------------------------------------------------------------

class EvaluatorManager:
    """评估器管理器.

    Apollo EvaluatorManager 的等价物: 统一管理多个评估器的生命周期和调度.

    Usage::

        manager = EvaluatorManager()
        manager.register(WinProbabilityEvaluator())
        manager.register(TeamFightEvaluator())
        manager.register(ObjectiveEvaluator())
        manager.init_all()

        # 在 Proc() 中:
        results = manager.run(game_state, features, game_time_s)
    """

    def __init__(self) -> None:
        self._evaluators: Dict[str, Evaluator] = {}
        self._eval_order: List[str] = []
        self._results_cache: Dict[str, EvaluatorResult] = {}
        self._total_runs: int = 0
        self._total_latency_ms: float = 0.0

    def register(self, evaluator: Evaluator) -> None:
        """注册评估器."""
        name = evaluator.name
        self._evaluators[name] = evaluator
        if name not in self._eval_order:
            self._eval_order.append(name)
        logger.debug("注册评估器: %s", name)

    def unregister(self, name: str) -> None:
        """取消注册."""
        self._evaluators.pop(name, None)
        if name in self._eval_order:
            self._eval_order.remove(name)

    def init_all(self) -> bool:
        """初始化所有评估器."""
        all_ok = True
        for name, evaluator in self._evaluators.items():
            try:
                if not evaluator.init():
                    logger.error("评估器初始化失败: %s", name)
                    all_ok = False
            except Exception as exc:
                logger.error("评估器初始化异常: %s: %s", name, exc)
                all_ok = False
        return all_ok

    def run(
        self,
        game_state: Dict[str, Any],
        features: Dict[str, Any],
        game_time_s: float = 0.0,
    ) -> Dict[str, EvaluatorResult]:
        """执行所有激活的评估器.

        根据 game_time_s 判断当前阶段, 只运行激活的评估器.

        Args:
            game_state: 游戏状态.
            features: 特征.
            game_time_s: 游戏时间 (秒).

        Returns:
            {evaluator_name: EvaluatorResult}
        """
        self._total_runs += 1
        phase = classify_game_phase(game_time_s)
        results: Dict[str, EvaluatorResult] = {}
        total_start = time.monotonic()

        for name in self._eval_order:
            evaluator = self._evaluators.get(name)
            if evaluator is None:
                continue

            if not evaluator.should_activate(phase):
                continue

            t0 = time.monotonic()
            try:
                result = evaluator.evaluate(game_state, features)
                result = EvaluatorResult(
                    evaluator_name=result.evaluator_name,
                    confidence=result.confidence,
                    value=result.value,
                    details=result.details,
                    latency_ms=(time.monotonic() - t0) * 1000,
                )
                results[name] = result
            except Exception as exc:
                logger.error(
                    "评估器 %s 执行失败: %s", name, exc,
                )
                results[name] = EvaluatorResult(
                    evaluator_name=name,
                    confidence=0.0,
                    value=0.0,
                    details={"error": str(exc)},
                    latency_ms=(time.monotonic() - t0) * 1000,
                )

        self._results_cache = results
        self._total_latency_ms += (time.monotonic() - total_start) * 1000
        return results

    def shutdown_all(self) -> None:
        """关闭所有评估器."""
        for name, evaluator in self._evaluators.items():
            try:
                evaluator.shutdown()
            except Exception as exc:
                logger.error("评估器关闭异常: %s: %s", name, exc)

    @property
    def evaluator_names(self) -> List[str]:
        return list(self._eval_order)

    @property
    def last_results(self) -> Dict[str, EvaluatorResult]:
        return dict(self._results_cache)

    def get_evaluator(self, name: str) -> Optional[Evaluator]:
        return self._evaluators.get(name)

    def stats(self) -> Dict[str, Any]:
        return {
            "evaluator_count": len(self._evaluators),
            "evaluators": self._eval_order,
            "total_runs": self._total_runs,
            "avg_latency_ms": round(
                self._total_latency_ms / max(1, self._total_runs), 2
            ),
            "per_evaluator": {
                name: ev.stats()
                for name, ev in self._evaluators.items()
            },
        }
