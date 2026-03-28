"""
Game State Snapshot - Immutable, timestamped game state for analysis pipelines.

Unlike LiveGameState which mutates as new data arrives, a Snapshot is a
frozen point-in-time capture suitable for:
  - Storing in replay buffers for RL training
  - Diffing successive snapshots to detect state changes
  - Serializing to disk for post-game analysis
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from pydantic import BaseModel, Field

from lol_fiddler_agent.network.live_client_data import (
    ActivePlayer,
    GameData,
    GameEvent,
    GamePhase,
    LiveGameState,
    Player,
    Scores,
    Team,
)


class TeamSnapshot(BaseModel):
    """Frozen team-level aggregate stats."""
    team: Team
    total_kills: int = 0
    total_deaths: int = 0
    total_assists: int = 0
    total_gold_estimate: int = 0
    alive_count: int = 0
    dead_count: int = 0
    avg_level: float = 0.0
    completed_items: int = 0
    dragon_count: int = 0
    has_baron: bool = False

    @property
    def team_kda(self) -> float:
        if self.total_deaths == 0:
            return float(self.total_kills + self.total_assists)
        return (self.total_kills + self.total_assists) / self.total_deaths


class PlayerSnapshot(BaseModel):
    """Frozen per-player state."""
    summoner_name: str = ""
    champion_name: str = ""
    team: str = "UNKNOWN"
    position: str = "UNKNOWN"
    level: int = 1
    kills: int = 0
    deaths: int = 0
    assists: int = 0
    creep_score: int = 0
    gold_estimate: int = 0
    completed_items: int = 0
    is_dead: bool = False
    respawn_timer: float = 0.0
    has_flash: bool = True

    @property
    def kda(self) -> float:
        if self.deaths == 0:
            return float(self.kills + self.assists)
        return (self.kills + self.assists) / self.deaths


class GameSnapshot(BaseModel):
    """Immutable point-in-time game state.

    Once created, the snapshot is frozen and hashable. Two snapshots
    with identical hashes represent the same game state.
    """
    # Metadata
    snapshot_id: str = ""
    captured_at: float = Field(default_factory=time.time)
    game_time: float = 0.0
    game_phase: str = GamePhase.EARLY.value
    game_mode: str = "CLASSIC"

    # Active player
    my_champion: str = ""
    my_team: str = Team.ORDER.value
    my_level: int = 1
    my_gold: float = 0.0
    my_health_pct: float = 100.0
    my_resource_pct: float = 100.0

    # Player lists
    players: list[PlayerSnapshot] = Field(default_factory=list)

    # Team aggregates
    ally_team: Optional[TeamSnapshot] = None
    enemy_team: Optional[TeamSnapshot] = None

    # Objectives
    recent_events: list[dict[str, Any]] = Field(default_factory=list)

    # Performance features (from optimizer)
    f1_deaths_per_min: float = 0.0
    f2_ka_per_min: float = 0.0
    f3_level_per_min: float = 0.0

    # Derived
    gold_difference: int = 0
    kill_difference: int = 0

    @classmethod
    def from_live_state(cls, state: LiveGameState) -> "GameSnapshot":
        """Create an immutable snapshot from a mutable LiveGameState."""
        # Extract basic info
        game_time = state.game_data.game_time if state.game_data else 0.0
        game_phase = state.game_data.game_phase.value if state.game_data else GamePhase.EARLY.value
        game_mode = state.game_data.game_mode if state.game_data else "CLASSIC"

        my_team = state.get_my_team()
        enemy_team_enum = Team.CHAOS if my_team == Team.ORDER else Team.ORDER

        # Build player snapshots
        player_snaps = []
        for p in state.all_players:
            ps = PlayerSnapshot(
                summoner_name=p.summoner_name or p.riot_id,
                champion_name=p.champion_name,
                team=p.team,
                position=p.position,
                level=p.level,
                kills=p.scores.kills,
                deaths=p.scores.deaths,
                assists=p.scores.assists,
                creep_score=p.scores.creep_score,
                gold_estimate=p.get_total_gold_estimate(),
                completed_items=p.get_completed_items_count(),
                is_dead=p.is_dead,
                respawn_timer=p.respawn_timer,
                has_flash=p.summoner_spells.has_flash() if p.summoner_spells else True,
            )
            player_snaps.append(ps)

        # Team aggregates
        ally_snap = _build_team_snapshot(state, my_team)
        enemy_snap = _build_team_snapshot(state, enemy_team_enum)

        # Active player info
        my_champion = state.get_my_champion_name()
        my_level = state.active_player.level if state.active_player else 1
        my_gold = state.active_player.current_gold if state.active_player else 0.0

        my_health = 100.0
        my_resource = 100.0
        if state.active_player and state.active_player.champion_stats:
            my_health = state.active_player.champion_stats.health_percent
            my_resource = state.active_player.champion_stats.resource_percent

        # Performance features
        features = state.calculate_performance_features()

        # Recent events (last 30 s)
        recent = state.get_recent_events(30.0)
        event_dicts = [
            {"name": e.event_name, "time": e.event_time, "killer": e.killer_name}
            for e in recent
        ]

        snap = cls(
            captured_at=time.time(),
            game_time=game_time,
            game_phase=game_phase,
            game_mode=game_mode,
            my_champion=my_champion,
            my_team=my_team.value,
            my_level=my_level,
            my_gold=my_gold,
            my_health_pct=my_health,
            my_resource_pct=my_resource,
            players=player_snaps,
            ally_team=ally_snap,
            enemy_team=enemy_snap,
            recent_events=event_dicts,
            f1_deaths_per_min=features.get("f1", 0.0),
            f2_ka_per_min=features.get("f2", 0.0),
            f3_level_per_min=features.get("f3", 0.0),
            gold_difference=state.get_gold_difference(),
            kill_difference=state.get_kill_difference(),
        )
        snap.snapshot_id = snap.compute_hash()
        return snap

    def compute_hash(self) -> str:
        """Compute a content hash for deduplication."""
        key_fields = f"{self.game_time:.1f}:{self.gold_difference}:{self.kill_difference}"
        for p in self.players:
            key_fields += f":{p.champion_name}:{p.kills}:{p.deaths}:{p.level}"
        return hashlib.md5(key_fields.encode()).hexdigest()[:12]

    def to_feature_vector(self) -> list[float]:
        """Convert to a flat feature vector for ML models."""
        features = [
            self.game_time,
            float(self.gold_difference),
            float(self.kill_difference),
            self.f1_deaths_per_min,
            self.f2_ka_per_min,
            self.f3_level_per_min,
            self.my_health_pct / 100.0,
            self.my_resource_pct / 100.0,
            float(self.my_level),
            self.my_gold,
        ]
        if self.ally_team:
            features.extend([
                float(self.ally_team.alive_count),
                self.ally_team.avg_level,
                float(self.ally_team.completed_items),
            ])
        if self.enemy_team:
            features.extend([
                float(self.enemy_team.alive_count),
                self.enemy_team.avg_level,
                float(self.enemy_team.completed_items),
            ])
        return features

    def to_json(self) -> str:
        return self.model_dump_json(indent=2)

    @classmethod
    def from_json(cls, data: str) -> "GameSnapshot":
        return cls.model_validate_json(data)

    def diff(self, other: "GameSnapshot") -> dict[str, Any]:
        """Compute differences between two snapshots."""
        changes: dict[str, Any] = {}
        if self.game_time != other.game_time:
            changes["game_time"] = {"from": self.game_time, "to": other.game_time}
        if self.gold_difference != other.gold_difference:
            changes["gold_diff"] = {"from": self.gold_difference, "to": other.gold_difference}
        if self.kill_difference != other.kill_difference:
            changes["kill_diff"] = {"from": self.kill_difference, "to": other.kill_difference}
        if self.game_phase != other.game_phase:
            changes["game_phase"] = {"from": self.game_phase, "to": other.game_phase}

        # Player death changes
        for sp, op in zip(self.players, other.players):
            if sp.champion_name == op.champion_name:
                if sp.is_dead != op.is_dead:
                    changes[f"player_{sp.champion_name}_dead"] = {
                        "from": sp.is_dead, "to": op.is_dead,
                    }

        return changes


def _build_team_snapshot(state: LiveGameState, team: Team) -> TeamSnapshot:
    """Build team aggregate from game state."""
    players = [p for p in state.all_players if p.team_enum == team]

    total_kills = sum(p.scores.kills for p in players)
    total_deaths = sum(p.scores.deaths for p in players)
    total_assists = sum(p.scores.assists for p in players)
    total_gold = sum(p.get_total_gold_estimate() for p in players)
    alive = sum(1 for p in players if not p.is_dead)
    dead = sum(1 for p in players if p.is_dead)
    avg_level = sum(p.level for p in players) / max(len(players), 1)
    items = sum(p.get_completed_items_count() for p in players)

    return TeamSnapshot(
        team=team,
        total_kills=total_kills,
        total_deaths=total_deaths,
        total_assists=total_assists,
        total_gold_estimate=total_gold,
        alive_count=alive,
        dead_count=dead,
        avg_level=avg_level,
        completed_items=items,
        dragon_count=state.get_dragon_count(team),
        has_baron=state.has_baron_buff(team),
    )


# ── Evolution Integration (M284 — appended, 不增不删原有函数) ─────────────
_EVOLUTION_KEY = 'game_snapshot'


class EvolutionMetadata:
    """Metadata attached to snapshots for evolution tracking.

    Carries generation, model version, and reward signal so that
    every snapshot in the training pipeline knows which model
    generation produced/evaluated it.
    """

    __slots__ = ('generation', 'model_version', 'reward_signal',
                 'policy_version', 'timestamp')

    def __init__(
        self, generation: int = 0, model_version: str = 'v0',
        reward_signal: float = 0.0, policy_version: str = '',
    ) -> None:
        import time as _time
        self.generation = generation
        self.model_version = model_version
        self.reward_signal = reward_signal
        self.policy_version = policy_version
        self.timestamp = _time.time()

    def to_dict(self) -> dict:
        return {
            'generation': self.generation,
            'model_version': self.model_version,
            'reward_signal': self.reward_signal,
            'policy_version': self.policy_version,
            'timestamp': self.timestamp,
        }


def attach_evolution_metadata(
    snapshot: 'GameSnapshot', metadata: EvolutionMetadata,
) -> dict:
    """Attach evolution metadata to a snapshot for training export.

    Returns a dict combining snapshot data with evolution tracking info.
    Does NOT modify the original snapshot object (immutable pattern).
    """
    result = {
        'snapshot_id': snapshot.snapshot_id,
        'game_time': snapshot.game_time,
        'evolution': metadata.to_dict(),
    }
    return result
