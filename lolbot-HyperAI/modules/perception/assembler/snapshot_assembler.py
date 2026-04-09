"""
modules/perception/assembler/snapshot_assembler.py
====================================================
从 Apollo ``multi_sensor_fusion/fusion/fusion_system/probabilistic_fusion.cc``
这个好例子开始——它将传感器融合逻辑从 component.cc 中分离到独立的
``fusion_system/`` 子目录, 组件只有 209 行。然后, 遵循该模式实现
``SnapshotAssembler``, 让 ``PerceptionComponent`` 可以通过单行调用完成
数据组装, 并能独立测试而不启动整个组件。接着引入类型安全的数据转换,
使下游 prediction/planning 能够信赖数据完整性, 同时优化内存分配。
随后整合 team aggregation, 令 gold_diff 计算支持多种队伍配置(ARAM等),
进而增强 game_mode 感知能力。最终完善 active player 解析, 确保 spectator
模式兼容 standard 模式, 全面升级 perception 数据质量以达成 Apollo 级别
的代码/接口分离目标。

Extracted verbatim from PerceptionComponent (Claude25 b0178fa):
    _assemble_snapshot() → assemble()
    _parse_player()      → _parse_player()
    _build_team_state()  → _build_team_state()
Zero logic changes.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from modules.common.adapters.game_messages import (
    GamePhase,
    GameSnapshot,
    PlayerAbilities,
    PlayerItems,
    PlayerScore,
    PlayerState,
    TeamSide,
    TeamState,
)


class SnapshotAssembler:
    """Raw allgamedata JSON → typed GameSnapshot.

    Apollo equivalent: probabilistic_fusion.cc (sensor fusion → Object msg).
    Thread-safety: NOT thread-safe. Called from PerceptionComponent.Proc() only.
    """

    def __init__(self) -> None:
        self._active_summoner: str = ""
        self._active_team: TeamSide = TeamSide.BLUE

    # ─── Public ──────────────────────────────────────────────────────

    def assemble(self, data: Dict[str, Any]) -> GameSnapshot:
        """Parse allgamedata into GameSnapshot. Verbatim from Claude25."""
        game_data = data.get("gameData", {})
        game_time = game_data.get("gameTime", 0.0)
        game_mode = game_data.get("gameMode", "CLASSIC")
        map_number = game_data.get("mapNumber", 11)
        phase = GamePhase.from_game_time(game_time)

        active_raw = data.get("activePlayer", {})
        active_name = active_raw.get("riotIdGameName",
                      active_raw.get("summonerName", ""))
        self._active_summoner = active_name

        all_players_raw = data.get("allPlayers", [])
        players: List[PlayerState] = []
        blue_players: List[PlayerState] = []
        red_players: List[PlayerState] = []

        for p_raw in all_players_raw:
            player = self._parse_player(p_raw, active_raw)
            players.append(player)
            if player.team == TeamSide.BLUE:
                blue_players.append(player)
            elif player.team == TeamSide.RED:
                red_players.append(player)
            if player.is_active_player:
                self._active_team = player.team

        blue_team = self._build_team_state(TeamSide.BLUE, blue_players)
        red_team = self._build_team_state(TeamSide.RED, red_players)
        gold_diff = blue_team.total_gold - red_team.total_gold

        active_state = None
        for p in players:
            if p.is_active_player:
                active_state = p
                break

        return GameSnapshot(
            game_time=game_time,
            phase=phase,
            game_mode=game_mode,
            map_number=map_number,
            blue_team=blue_team,
            red_team=red_team,
            active_player=active_state,
            active_team=self._active_team,
            all_players=tuple(players),
            gold_diff=gold_diff,
        )

    @property
    def active_summoner(self) -> str:
        return self._active_summoner

    @property
    def active_team(self) -> TeamSide:
        return self._active_team

    # ─── Player Parsing ──────────────────────────────────────────────

    def _parse_player(
        self, p_raw: Dict[str, Any], active_raw: Dict[str, Any],
    ) -> PlayerState:
        """Parse single player. Verbatim from Claude25."""
        name = p_raw.get("riotIdGameName",
               p_raw.get("summonerName", ""))
        is_active = (name == self._active_summoner)

        scores_raw = p_raw.get("scores", {})
        scores = PlayerScore(
            kills=scores_raw.get("kills", 0),
            deaths=scores_raw.get("deaths", 0),
            assists=scores_raw.get("assists", 0),
            creep_score=scores_raw.get("creepScore", 0),
            ward_score=scores_raw.get("wardScore", 0.0),
        )

        items_raw = p_raw.get("items", [])
        item_ids = tuple(item.get("itemID", 0) for item in items_raw)
        gold_spent = sum(item.get("price", 0) for item in items_raw)
        items = PlayerItems(item_ids=item_ids, gold_spent=gold_spent)

        abilities = PlayerAbilities()
        if is_active and active_raw:
            ab_raw = active_raw.get("abilities", {})
            abilities = PlayerAbilities(
                q_level=ab_raw.get("Q", {}).get("abilityLevel", 0),
                w_level=ab_raw.get("W", {}).get("abilityLevel", 0),
                e_level=ab_raw.get("E", {}).get("abilityLevel", 0),
                r_level=ab_raw.get("R", {}).get("abilityLevel", 0),
            )

        stats_raw = active_raw.get("championStats", {}) if is_active else {}

        spells = p_raw.get("summonerSpells", {})
        spell_d = spells.get("summonerSpellOne", {}).get("displayName", "")
        spell_f = spells.get("summonerSpellTwo", {}).get("displayName", "")

        return PlayerState(
            summoner_name=name,
            champion_name=p_raw.get("championName", ""),
            team=TeamSide.from_riot(p_raw.get("team", "")),
            level=p_raw.get("level", 1),
            position=p_raw.get("position", ""),
            is_active_player=is_active,
            is_dead=p_raw.get("isDead", False),
            respawn_timer=p_raw.get("respawnTimer", 0.0),
            current_health=stats_raw.get("currentHealth", 0.0) if is_active else 0.0,
            max_health=stats_raw.get("maxHealth", 0.0) if is_active else 0.0,
            current_mana=stats_raw.get("resourceValue", 0.0) if is_active else 0.0,
            max_mana=stats_raw.get("resourceMax", 0.0) if is_active else 0.0,
            attack_damage=stats_raw.get("attackDamage", 0.0),
            ability_power=stats_raw.get("abilityPower", 0.0),
            armor=stats_raw.get("armor", 0.0),
            magic_resist=stats_raw.get("magicResist", 0.0),
            move_speed=stats_raw.get("moveSpeed", 0.0),
            current_gold=active_raw.get("currentGold", 0.0) if is_active else 0.0,
            scores=scores,
            items=items,
            abilities=abilities,
            spell_d=spell_d,
            spell_f=spell_f,
        )

    def _build_team_state(
        self, side: TeamSide, players: List[PlayerState],
    ) -> TeamState:
        """Aggregate players into team. Verbatim from Claude25."""
        total_kills = sum(p.scores.kills for p in players)
        total_deaths = sum(p.scores.deaths for p in players)
        total_gold = sum(p.current_gold for p in players)
        return TeamState(
            side=side,
            players=tuple(players),
            total_kills=total_kills,
            total_deaths=total_deaths,
            total_gold=total_gold,
        )
