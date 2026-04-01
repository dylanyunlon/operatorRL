#!/usr/bin/env python3
"""
M968: DraftSimulationEngine
===========================

Ban/Pick模拟引擎 — 基于历史英雄池+阵容原型的蒙特卡洛选人模拟，为BP阶段提供最优策略推荐序列

Dependencies: M906, M911, M918, M967

Architecture Pattern:
    查看 Seraphine/app/lol/connector.py 上现有 LCU API connector 的实现方式,
    理解其模式, 特别是 retry 装饰器和 PastRequest 是如何与 HTTP session 分离的。
    从 connector.needLcu + retry 这个好例子开始。
    遵循该模式实现 DraftSimulationEngine。

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

logger = logging.getLogger("M968.DraftSimulationEngine")

T = TypeVar("T")


# ============================================================
# 配置与常量
# ============================================================

SIMULATION_ROUNDS = 1000
BAN_PHASE_SIZE = 5
PICK_PHASE_SIZE = 5
UCB_EXPLORATION = 1.414
MAX_TREE_DEPTH = 10
CHAMPION_POOL_SIZE = 160
ROLE_ORDER = ["TOP", "JUNGLE", "MID", "ADC", "SUPPORT"]
DRAFT_TIMEOUT_SECONDS = 30.0


class DraftPhase(Enum):
    BAN_1 = auto()
    PICK_1 = auto()
    BAN_2 = auto()
    PICK_2 = auto()
    COMPLETE = auto()


class Side(Enum):
    BLUE = "blue"
    RED = "red"


@dataclass
class ChampionInfo:
    """英雄信息 — 对接Seraphine JsonManager的champion数据"""
    champion_id: int
    name: str
    roles: List[str]
    tier: float = 3.0
    pick_rate: float = 0.05
    ban_rate: float = 0.02
    winrate: float = 0.50
    difficulty: float = 5.0

    @property
    def priority_score(self) -> float:
        return self.tier * 0.4 + self.winrate * 0.3 + self.pick_rate * 0.3


@dataclass
class DraftState:
    """选英雄阶段状态"""
    phase: DraftPhase = DraftPhase.BAN_1
    blue_bans: List[int] = field(default_factory=list)
    red_bans: List[int] = field(default_factory=list)
    blue_picks: List[Tuple[int, str]] = field(default_factory=list)
    red_picks: List[Tuple[int, str]] = field(default_factory=list)
    current_side: Side = Side.BLUE
    turn_index: int = 0

    @property
    def all_banned(self) -> Set[int]:
        return set(self.blue_bans + self.red_bans)

    @property
    def all_picked(self) -> Set[int]:
        return {c for c, _ in self.blue_picks + self.red_picks}

    @property
    def unavailable(self) -> Set[int]:
        return self.all_banned | self.all_picked

    def copy(self) -> "DraftState":
        return DraftState(
            phase=self.phase,
            blue_bans=list(self.blue_bans),
            red_bans=list(self.red_bans),
            blue_picks=list(self.blue_picks),
            red_picks=list(self.red_picks),
            current_side=self.current_side,
            turn_index=self.turn_index,
        )

    @property
    def is_complete(self) -> bool:
        return (len(self.blue_picks) == PICK_PHASE_SIZE and
                len(self.red_picks) == PICK_PHASE_SIZE)


@dataclass
class DraftAction:
    """选英雄动作"""
    action_type: str  # "ban" or "pick"
    champion_id: int
    role: str = ""
    side: Side = Side.BLUE

    def __repr__(self):
        return f"{self.side.value}/{self.action_type}/{self.champion_id}@{self.role}"


@dataclass
class SimulationResult:
    """模拟结果"""
    recommended_action: DraftAction
    win_probability: float
    confidence: float
    alternatives: List[Tuple[DraftAction, float]]
    simulations_run: int
    elapsed_seconds: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "recommended": repr(self.recommended_action),
            "win_prob": round(self.win_probability, 4),
            "confidence": round(self.confidence, 4),
            "alternatives": [(repr(a), round(p, 4)) for a, p in self.alternatives[:5]],
            "simulations": self.simulations_run,
            "elapsed_s": round(self.elapsed_seconds, 3),
        }


class ChampionDatabase:
    """英雄数据库 — 对接Seraphine JsonManager"""

    def __init__(self):
        self._champions: Dict[int, ChampionInfo] = {}
        self._role_champions: Dict[str, List[int]] = defaultdict(list)

    def register(self, champ: ChampionInfo) -> None:
        self._champions[champ.champion_id] = champ
        for role in champ.roles:
            if champ.champion_id not in self._role_champions[role]:
                self._role_champions[role].append(champ.champion_id)

    def get(self, champion_id: int) -> Optional[ChampionInfo]:
        return self._champions.get(champion_id)

    def get_available_for_role(self, role: str, unavailable: Set[int]) -> List[ChampionInfo]:
        candidates = self._role_champions.get(role, [])
        return [self._champions[c] for c in candidates
                if c not in unavailable and c in self._champions]

    def get_ban_candidates(self, unavailable: Set[int], top_n: int = 20) -> List[ChampionInfo]:
        candidates = [c for c in self._champions.values()
                      if c.champion_id not in unavailable]
        candidates.sort(key=lambda c: c.priority_score, reverse=True)
        return candidates[:top_n]

    @property
    def size(self) -> int:
        return len(self._champions)


class OpponentModelAdapter:
    """对手模型适配器 — 对接M910 OpponentProfileBuilder + M911 ChampionPoolAnalyzer"""

    def __init__(self):
        self._opponent_pools: Dict[str, Dict[int, float]] = {}
        self._opponent_roles: Dict[str, str] = {}

    def register_opponent(self, puuid: str, champion_preferences: Dict[int, float],
                          primary_role: str) -> None:
        self._opponent_pools[puuid] = champion_preferences
        self._opponent_roles[puuid] = primary_role

    def get_opponent_pick_probability(self, puuid: str, champion_id: int) -> float:
        pool = self._opponent_pools.get(puuid, {})
        if not pool:
            return 0.05
        return pool.get(champion_id, 0.01)

    def get_likely_picks(self, puuid: str, unavailable: Set[int],
                         top_n: int = 5) -> List[Tuple[int, float]]:
        pool = self._opponent_pools.get(puuid, {})
        available = {k: v for k, v in pool.items() if k not in unavailable}
        sorted_picks = sorted(available.items(), key=lambda x: x[1], reverse=True)
        return sorted_picks[:top_n]


class MCTSNode:
    """蒙特卡洛树搜索节点"""

    def __init__(self, state: DraftState, action: Optional[DraftAction] = None,
                 parent: Optional["MCTSNode"] = None):
        self.state = state
        self.action = action
        self.parent = parent
        self.children: List["MCTSNode"] = []
        self.visits = 0
        self.value = 0.0
        self.untried_actions: Optional[List[DraftAction]] = None

    @property
    def ucb(self) -> float:
        if self.visits == 0:
            return float("inf")
        exploitation = self.value / self.visits
        exploration = UCB_EXPLORATION * (
            (2.0 * (self.parent.visits if self.parent else 1)) ** 0.5
            / (1 + self.visits)
        ) ** 0.5
        return exploitation + exploration

    def best_child(self) -> "MCTSNode":
        return max(self.children, key=lambda c: c.ucb)

    def best_action_child(self) -> "MCTSNode":
        return max(self.children, key=lambda c: c.visits)


class DraftEvaluator:
    """选英雄评估器 — 评估当前draft状态的胜率"""

    def __init__(self, champion_db: ChampionDatabase):
        self._db = champion_db

    def evaluate(self, state: DraftState, perspective: Side) -> float:
        """简化评估: 基于英雄胜率和优先级的加权评分"""
        blue_score = 0.0
        red_score = 0.0
        for champ_id, role in state.blue_picks:
            info = self._db.get(champ_id)
            if info:
                blue_score += info.winrate * 2.0 + info.tier * 0.5
        for champ_id, role in state.red_picks:
            info = self._db.get(champ_id)
            if info:
                red_score += info.winrate * 2.0 + info.tier * 0.5
        total = blue_score + red_score
        if total == 0:
            return 0.5
        my_score = blue_score if perspective == Side.BLUE else red_score
        return my_score / total


class DraftSimulationEngine:
    """
    Ban/Pick模拟引擎 — 核心类

    查看 Seraphine/app/lol/connector.py 上现有 getChampSelectSession 的实现方式,
    理解其模式, 特别是 selectChampion/banChampion 的 actionsId 参数设计。
    从 connector.getChampSelectSession 这个好例子开始。
    然后, 遵循该模式实现 DraftSimulationEngine,
    让 operatorRL 可以模拟最优BP序列,
    并能基于对手英雄池数据推荐ban/pick。
    """

    def __init__(self, champion_db: Optional[ChampionDatabase] = None):
        self._db = champion_db or ChampionDatabase()
        self._evaluator = DraftEvaluator(self._db)
        self._opponent_model = OpponentModelAdapter()
        self._simulation_count = 0
        self._lock = asyncio.Lock()
        logger.info("DraftSimulationEngine initialized")

    @property
    def champion_db(self) -> ChampionDatabase:
        return self._db

    @property
    def opponent_model(self) -> OpponentModelAdapter:
        return self._opponent_model

    def _get_legal_actions(self, state: DraftState) -> List[DraftAction]:
        """获取当前状态下的合法动作"""
        unavailable = state.unavailable
        actions = []
        if state.phase in (DraftPhase.BAN_1, DraftPhase.BAN_2):
            candidates = self._db.get_ban_candidates(unavailable, top_n=15)
            for c in candidates:
                actions.append(DraftAction(
                    action_type="ban",
                    champion_id=c.champion_id,
                    side=state.current_side,
                ))
        elif state.phase in (DraftPhase.PICK_1, DraftPhase.PICK_2):
            needed_roles = self._get_needed_roles(state)
            for role in needed_roles[:2]:
                candidates = self._db.get_available_for_role(role, unavailable)
                for c in candidates[:8]:
                    actions.append(DraftAction(
                        action_type="pick",
                        champion_id=c.champion_id,
                        role=role,
                        side=state.current_side,
                    ))
        return actions[:30]

    def _get_needed_roles(self, state: DraftState) -> List[str]:
        picks = state.blue_picks if state.current_side == Side.BLUE else state.red_picks
        filled_roles = {role for _, role in picks}
        return [r for r in ROLE_ORDER if r not in filled_roles]

    def _apply_action(self, state: DraftState, action: DraftAction) -> DraftState:
        new_state = state.copy()
        if action.action_type == "ban":
            if action.side == Side.BLUE:
                new_state.blue_bans.append(action.champion_id)
            else:
                new_state.red_bans.append(action.champion_id)
        else:
            if action.side == Side.BLUE:
                new_state.blue_picks.append((action.champion_id, action.role))
            else:
                new_state.red_picks.append((action.champion_id, action.role))
        new_state.turn_index += 1
        # 简化的阶段转换
        total_bans = len(new_state.blue_bans) + len(new_state.red_bans)
        total_picks = len(new_state.blue_picks) + len(new_state.red_picks)
        if total_bans < 6:
            new_state.phase = DraftPhase.BAN_1
        elif total_picks < 6:
            new_state.phase = DraftPhase.PICK_1
        elif total_bans < 10:
            new_state.phase = DraftPhase.BAN_2
        elif total_picks < 10:
            new_state.phase = DraftPhase.PICK_2
        else:
            new_state.phase = DraftPhase.COMPLETE
        new_state.current_side = (Side.RED if new_state.current_side == Side.BLUE
                                  else Side.BLUE)
        return new_state

    def _rollout(self, state: DraftState, perspective: Side) -> float:
        """随机模拟到结束"""
        current = state.copy()
        depth = 0
        while not current.is_complete and depth < MAX_TREE_DEPTH * 3:
            actions = self._get_legal_actions(current)
            if not actions:
                break
            import random
            action = random.choice(actions)
            current = self._apply_action(current, action)
            depth += 1
        return self._evaluator.evaluate(current, perspective)

    async def simulate(self, current_state: DraftState,
                       perspective: Side,
                       rounds: int = SIMULATION_ROUNDS) -> SimulationResult:
        """运行MCTS模拟推荐最优动作"""
        async with self._lock:
            self._simulation_count += 1
        start = time.monotonic()
        logger.info(f"Starting MCTS simulation: {rounds} rounds, perspective={perspective.value}")
        root = MCTSNode(state=current_state)
        root.untried_actions = self._get_legal_actions(current_state)
        if not root.untried_actions:
            return SimulationResult(
                recommended_action=DraftAction("pick", 0),
                win_probability=0.5, confidence=0.0,
                alternatives=[], simulations_run=0,
                elapsed_seconds=time.monotonic() - start,
            )
        for _ in range(min(rounds, len(root.untried_actions) * 50)):
            node = root
            # Selection
            while node.untried_actions is not None and not node.untried_actions and node.children:
                node = node.best_child()
            # Expansion
            if node.untried_actions:
                action = node.untried_actions.pop()
                new_state = self._apply_action(node.state, action)
                child = MCTSNode(state=new_state, action=action, parent=node)
                child.untried_actions = self._get_legal_actions(new_state)
                node.children.append(child)
                node = child
            # Simulation
            value = self._rollout(node.state, perspective)
            # Backpropagation
            while node is not None:
                node.visits += 1
                node.value += value
                node = node.parent
        # 收集结果
        children_results = []
        for child in root.children:
            if child.visits > 0:
                avg_value = child.value / child.visits
                children_results.append((child.action, avg_value))
        children_results.sort(key=lambda x: x[1], reverse=True)
        best_action, best_value = children_results[0] if children_results else (
            DraftAction("pick", 0), 0.5)
        elapsed = time.monotonic() - start
        total_visits = sum(c.visits for c in root.children)
        confidence = min(1.0, total_visits / rounds)
        result = SimulationResult(
            recommended_action=best_action,
            win_probability=best_value,
            confidence=confidence,
            alternatives=children_results[1:6],
            simulations_run=total_visits,
            elapsed_seconds=elapsed,
        )
        logger.info(f"MCTS complete: {repr(best_action)} -> {best_value:.3f} "
                    f"({total_visits} visits in {elapsed:.3f}s)")
        return result

    async def get_ban_recommendations(self, opponent_puuids: List[str],
                                       current_state: DraftState) -> List[Tuple[int, float, str]]:
        """
        获取ban推荐 — 基于对手英雄池和全局优先级

        Returns:
            List of (champion_id, priority_score, reason)
        """
        recommendations: List[Tuple[int, float, str]] = []
        unavailable = current_state.unavailable
        # Phase 1: 对手英雄池高优先ban
        for puuid in opponent_puuids:
            likely = self._opponent_model.get_likely_picks(puuid, unavailable, top_n=3)
            for champ_id, prob in likely:
                info = self._db.get(champ_id)
                if info:
                    score = prob * 0.6 + info.priority_score * 0.4
                    reason = f"对手{puuid[:6]}高频使用(p={prob:.2f}), 优先级={info.priority_score:.2f}"
                    recommendations.append((champ_id, score, reason))
        # Phase 2: 全局高优先ban
        global_candidates = self._db.get_ban_candidates(unavailable, top_n=10)
        for info in global_candidates:
            already_in = any(r[0] == info.champion_id for r in recommendations)
            if not already_in:
                score = info.priority_score * 0.8
                reason = f"全局高优先: tier={info.tier:.1f}, wr={info.winrate:.1%}"
                recommendations.append((info.champion_id, score, reason))
        recommendations.sort(key=lambda x: x[1], reverse=True)
        logger.info(f"Ban recommendations: {len(recommendations)} candidates")
        return recommendations[:10]

    async def get_pick_recommendations(self, side: Side,
                                        current_state: DraftState,
                                        team_preferences: Optional[Dict[str, List[int]]] = None
                                        ) -> List[Tuple[int, str, float, str]]:
        """
        获取pick推荐 — 基于阵容需求+英雄强度+对位优势

        Returns:
            List of (champion_id, role, score, reason)
        """
        recommendations: List[Tuple[int, str, float, str]] = []
        unavailable = current_state.unavailable
        needed_roles = self._get_needed_roles(current_state)
        for role in needed_roles:
            candidates = self._db.get_available_for_role(role, unavailable)
            for info in candidates[:10]:
                score = info.priority_score
                reason_parts = [f"tier={info.tier:.1f}", f"wr={info.winrate:.1%}"]
                # 加入队伍偏好加成
                if team_preferences and role in team_preferences:
                    if info.champion_id in team_preferences[role]:
                        score *= 1.2
                        reason_parts.append("队伍偏好")
                reason = ", ".join(reason_parts)
                recommendations.append((info.champion_id, role, score, reason))
        recommendations.sort(key=lambda x: x[2], reverse=True)
        return recommendations[:15]

    def get_diagnostics(self) -> Dict[str, Any]:
        return {
            "simulation_count": self._simulation_count,
            "champion_db_size": self._db.size,
        }


class DraftHistoryTracker:
    """选人历史追踪器 — 记录和分析历史BP数据用于改进推荐"""

    def __init__(self, max_history: int = 500):
        self._history: deque = deque(maxlen=max_history)
        self._champion_ban_rates: Dict[int, int] = defaultdict(int)
        self._champion_pick_rates: Dict[int, int] = defaultdict(int)
        self._champion_win_rates: Dict[int, List[bool]] = defaultdict(list)
        self._total_games = 0

    def record_draft(self, state: DraftState, blue_won: bool) -> None:
        """记录一次完整的BP结果"""
        self._total_games += 1
        for ban_id in state.blue_bans + state.red_bans:
            self._champion_ban_rates[ban_id] += 1
        for champ_id, role in state.blue_picks:
            self._champion_pick_rates[champ_id] += 1
            self._champion_win_rates[champ_id].append(blue_won)
        for champ_id, role in state.red_picks:
            self._champion_pick_rates[champ_id] += 1
            self._champion_win_rates[champ_id].append(not blue_won)
        self._history.append({
            "state": state,
            "blue_won": blue_won,
            "timestamp": time.time(),
        })

    def get_ban_rate(self, champion_id: int) -> float:
        if self._total_games == 0:
            return 0.0
        return self._champion_ban_rates.get(champion_id, 0) / self._total_games

    def get_pick_rate(self, champion_id: int) -> float:
        if self._total_games == 0:
            return 0.0
        return self._champion_pick_rates.get(champion_id, 0) / self._total_games

    def get_observed_winrate(self, champion_id: int) -> Optional[float]:
        results = self._champion_win_rates.get(champion_id, [])
        if len(results) < 3:
            return None
        return sum(results) / len(results)

    def get_trending_bans(self, top_n: int = 10) -> List[Tuple[int, float]]:
        """获取热门ban英雄"""
        rates = [(cid, self.get_ban_rate(cid))
                 for cid in self._champion_ban_rates]
        rates.sort(key=lambda x: x[1], reverse=True)
        return rates[:top_n]

    def get_trending_picks(self, top_n: int = 10) -> List[Tuple[int, float]]:
        """获取热门pick英雄"""
        rates = [(cid, self.get_pick_rate(cid))
                 for cid in self._champion_pick_rates]
        rates.sort(key=lambda x: x[1], reverse=True)
        return rates[:top_n]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_games": self._total_games,
            "unique_banned": len(self._champion_ban_rates),
            "unique_picked": len(self._champion_pick_rates),
            "trending_bans": self.get_trending_bans(5),
            "trending_picks": self.get_trending_picks(5),
        }


class BanPriorityCalculator:
    """Ban优先级计算器 — 综合多维度数据计算最优ban目标"""

    WEIGHT_OPPONENT_POOL = 0.35
    WEIGHT_GLOBAL_TIER = 0.25
    WEIGHT_WINRATE = 0.20
    WEIGHT_TREND = 0.20

    def __init__(self, champion_db: ChampionDatabase,
                 history_tracker: Optional[DraftHistoryTracker] = None):
        self._db = champion_db
        self._tracker = history_tracker or DraftHistoryTracker()

    def compute_priority(self, champion_id: int,
                         opponent_affinity: float = 0.0) -> float:
        """计算英雄ban优先级分数"""
        info = self._db.get(champion_id)
        if not info:
            return 0.0
        # 维度1: 对手亲和度
        pool_score = opponent_affinity * self.WEIGHT_OPPONENT_POOL
        # 维度2: 全局tier
        tier_score = (info.tier / 5.0) * self.WEIGHT_GLOBAL_TIER
        # 维度3: 胜率
        observed_wr = self._tracker.get_observed_winrate(champion_id)
        wr = observed_wr if observed_wr is not None else info.winrate
        wr_score = wr * self.WEIGHT_WINRATE
        # 维度4: 趋势
        pick_rate = self._tracker.get_pick_rate(champion_id)
        trend_score = min(1.0, pick_rate * 10) * self.WEIGHT_TREND
        total = pool_score + tier_score + wr_score + trend_score
        return round(total, 4)


async def _self_test():
    logger.info("Starting M968 DraftSimulationEngine self-test")
    engine = DraftSimulationEngine()
    for i in range(20):
        roles = [ROLE_ORDER[i % 5]]
        engine.champion_db.register(ChampionInfo(
            champion_id=100 + i, name=f"Champ{i}",
            roles=roles, tier=3.0 + (i % 3), winrate=0.48 + (i % 5) * 0.01,
        ))
    state = DraftState()
    result = await engine.simulate(state, Side.BLUE, rounds=50)
    logger.info(f"Simulation result: {json.dumps(result.to_dict(), indent=2)}")
    logger.info("M968 self-test PASSED")


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    asyncio.run(_self_test())
