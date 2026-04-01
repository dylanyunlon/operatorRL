"""
ReplaySimulator — 录像回放模拟器 (离线测试工具)
=================================================
查看 tests/test_integration.py 上现有 make_synthetic_allgamedata()
合成数据生成器的实现方式, 理解其模式, 特别是 JSON 结构
和 _make_player 工厂函数 是如何与测试断言分离的。
可以从 M1008 MatchTimelineDeserializer 的时间线事件解析 这个好例子开始。
然后, 遵循该模式实现一个新的 ReplaySimulator,
让开发者 可以 从保存的 allgamedata JSON 文件按时间序列回放游戏,
并能 以可配置的速度 (1x/2x/4x) 驱动整条管线运行。
从头开始构建, 除了代码库中已有的库之外, 不要使用其他库。

Architecture position:
    scripts/replay_simulator.py   ← YOU ARE HERE
    ├─ Reads: saved JSON snapshots from data/ directory
    ├─ Publishes: /lol/raw_lcu (RawLCUData) — mimics canbus
    ├─ Drives: the full pipeline without a live LoL game
    └─ Used for: offline testing, demo, algorithm tuning

Usage:
    python -m scripts.replay_simulator --file data/replay_001.json
    python -m scripts.replay_simulator --generate --duration 1800
    python -m scripts.replay_simulator --generate --speed 4
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from cyber.node.node import CyberNode, Writer
from cyber.logger.cyber_logger import LogConfig, configure, get_logger
from modules.common.adapters.game_messages import RawLCUData

logger = get_logger("replay")


# ─── Synthetic Game Generator ────────────────────────────────────────────────

class SyntheticGameGenerator:
    """Generates realistic synthetic allgamedata snapshots.

    Simulates a ~30 minute LoL game with:
    - Linearly increasing levels and CS
    - Random kills with realistic frequency
    - Gold accumulation with momentum
    - Objective events (dragons, baron, towers)
    """

    _BLUE_PLAYERS = [
        ("Player1", "Jinx", "BOTTOM"),
        ("Player2", "Thresh", "UTILITY"),
        ("Player3", "Ahri", "MIDDLE"),
        ("Player4", "Darius", "TOP"),
        ("Player5", "LeeSin", "JUNGLE"),
    ]
    _RED_PLAYERS = [
        ("Enemy1", "Caitlyn", "BOTTOM"),
        ("Enemy2", "Lulu", "UTILITY"),
        ("Enemy3", "Zed", "MIDDLE"),
        ("Enemy4", "Garen", "TOP"),
        ("Enemy5", "Elise", "JUNGLE"),
    ]

    def __init__(self, seed: int = 42) -> None:
        self._rng = random.Random(seed)
        self._event_id = 0
        self._kills: Dict[str, Dict[str, int]] = {}

        # Initialize kill counters
        for name, _, _ in self._BLUE_PLAYERS + self._RED_PLAYERS:
            self._kills[name] = {"kills": 0, "deaths": 0, "assists": 0}

    def generate_snapshot(self, game_time: float) -> Dict[str, Any]:
        """Generate a single allgamedata snapshot at the given time."""
        game_min = game_time / 60.0

        # Generate events that happened since start
        events = self._generate_events_until(game_time)

        # Build players
        all_players = []
        for name, champ, pos in self._BLUE_PLAYERS:
            all_players.append(self._make_player(
                name, champ, "ORDER", pos, game_time,
            ))
        for name, champ, pos in self._RED_PLAYERS:
            all_players.append(self._make_player(
                name, champ, "CHAOS", pos, game_time,
            ))

        # Active player stats
        active_level = min(18, int(1 + game_min * 0.6))
        active_stats = {
            "currentHealth": 600 + active_level * 80,
            "maxHealth": 800 + active_level * 90,
            "resourceValue": 300 + active_level * 20,
            "resourceMax": 400 + active_level * 25,
            "attackDamage": 60 + active_level * 8,
            "abilityPower": 0.0,
            "armor": 30 + active_level * 4,
            "magicResist": 30 + active_level * 2,
            "moveSpeed": 345.0,
        }

        return {
            "gameData": {
                "gameTime": game_time,
                "gameMode": "CLASSIC",
                "mapNumber": 11,
                "mapName": "Map11",
            },
            "activePlayer": {
                "riotIdGameName": "Player1",
                "summonerName": "Player1",
                "level": active_level,
                "currentGold": 500 + game_min * 300 + self._rng.uniform(-200, 200),
                "championStats": active_stats,
                "abilities": {
                    "Q": {"abilityLevel": min(5, max(1, int(active_level / 3)))},
                    "W": {"abilityLevel": min(5, max(0, int((active_level - 2) / 3)))},
                    "E": {"abilityLevel": min(5, max(0, int((active_level - 1) / 3)))},
                    "R": {"abilityLevel": min(3, max(0, int((active_level - 5) / 5)))},
                },
            },
            "allPlayers": all_players,
            "events": {"Events": events},
        }

    def _make_player(
        self,
        name: str, champion: str, team: str, position: str,
        game_time: float,
    ) -> Dict[str, Any]:
        game_min = game_time / 60.0
        level = min(18, int(1 + game_min * 0.55 + self._rng.uniform(-0.5, 0.5)))
        cs = int(game_min * 7 + self._rng.uniform(-10, 10))
        stats = self._kills.get(name, {"kills": 0, "deaths": 0, "assists": 0})

        return {
            "riotIdGameName": name,
            "summonerName": name,
            "championName": champion,
            "team": team,
            "level": max(1, level),
            "position": position,
            "isDead": False,
            "respawnTimer": 0.0,
            "scores": {
                "kills": stats["kills"],
                "deaths": stats["deaths"],
                "assists": stats["assists"],
                "creepScore": max(0, cs),
                "wardScore": game_min * 0.3,
            },
            "items": [
                {"itemID": 3031, "displayName": "Item", "price": int(game_min * 200)},
            ],
            "summonerSpells": {
                "summonerSpellOne": {"displayName": "Flash"},
                "summonerSpellTwo": {"displayName": "Heal"},
            },
        }

    def _generate_events_until(self, game_time: float) -> List[Dict[str, Any]]:
        """Generate all events from game start to current time."""
        events = [
            {"EventID": 1, "EventName": "GameStart", "EventTime": 0.0},
        ]

        # Seed the RNG consistently so events are reproducible
        rng = random.Random(42)
        self._event_id = 1

        # Generate kills roughly every 2-3 minutes
        all_names = [n for n, _, _ in self._BLUE_PLAYERS + self._RED_PLAYERS]
        t = 120.0  # first kill around 2 min
        while t < game_time:
            self._event_id += 1
            killer = rng.choice(all_names)
            victim = rng.choice([n for n in all_names if n != killer])

            self._kills.setdefault(killer, {"kills": 0, "deaths": 0, "assists": 0})
            self._kills.setdefault(victim, {"kills": 0, "deaths": 0, "assists": 0})
            self._kills[killer]["kills"] += 1
            self._kills[victim]["deaths"] += 1

            # Random assister from same team
            assisters = []
            killer_team = [n for n, _, _ in self._BLUE_PLAYERS] if killer in [n for n, _, _ in self._BLUE_PLAYERS] else [n for n, _, _ in self._RED_PLAYERS]
            possible = [n for n in killer_team if n != killer]
            if possible and rng.random() > 0.3:
                a = rng.choice(possible)
                assisters.append(a)
                self._kills[a]["assists"] += 1

            events.append({
                "EventID": self._event_id,
                "EventName": "ChampionKill",
                "EventTime": t,
                "KillerName": killer,
                "VictimName": victim,
                "Assisters": assisters,
            })

            t += rng.uniform(30, 180)  # 30s to 3 min between kills

        # Dragon kills (every ~5 min starting at 5 min)
        dragon_t = 300.0
        while dragon_t < game_time:
            self._event_id += 1
            events.append({
                "EventID": self._event_id,
                "EventName": "DragonKill",
                "EventTime": dragon_t,
                "KillerName": rng.choice([n for n, _, _ in self._BLUE_PLAYERS]),
                "Assisters": [],
            })
            dragon_t += rng.uniform(300, 420)

        events.sort(key=lambda e: e.get("EventTime", 0))
        return events

    def generate_full_game(
        self, duration_s: float = 1800.0, interval_s: float = 1.0,
    ) -> List[Dict[str, Any]]:
        """Generate a complete game's worth of snapshots.

        Args:
            duration_s: Game duration in seconds (default 30 min).
            interval_s: Time between snapshots (default 1s).

        Returns:
            List of allgamedata dicts.
        """
        snapshots = []
        t = 0.0
        while t <= duration_s:
            snapshots.append(self.generate_snapshot(t))
            t += interval_s
        return snapshots


# ─── Replay Simulator ───────────────────────────────────────────────────────

class ReplaySimulator:
    """Drives the lolbot-HyperAI pipeline from saved/generated data.

    Either loads JSON snapshots from a file or generates synthetic
    data, then publishes them on /lol/raw_lcu at configurable speed.

    Usage::

        sim = ReplaySimulator(speed=2.0)
        sim.load_generated(duration=1800)
        sim.run()  # blocks, publishing at 2x speed
    """

    def __init__(self, speed: float = 1.0) -> None:
        self._speed = max(0.1, speed)
        self._snapshots: List[Dict[str, Any]] = []
        self._node: Optional[CyberNode] = None
        self._writer: Optional[Writer] = None
        self._running: bool = False
        self._published_count: int = 0

    def load_file(self, path: str) -> int:
        """Load snapshots from a JSON file.

        File format: JSON array of allgamedata dicts.

        Returns:
            Number of snapshots loaded.
        """
        with open(path, "r") as f:
            data = json.load(f)

        if isinstance(data, list):
            self._snapshots = data
        elif isinstance(data, dict) and "snapshots" in data:
            self._snapshots = data["snapshots"]
        else:
            self._snapshots = [data]

        logger.info("Loaded %d snapshots from %s", len(self._snapshots), path)
        return len(self._snapshots)

    def load_generated(self, duration: float = 1800.0) -> int:
        """Generate synthetic game data.

        Args:
            duration: Game duration in seconds.

        Returns:
            Number of snapshots generated.
        """
        gen = SyntheticGameGenerator()
        self._snapshots = gen.generate_full_game(duration, interval_s=1.0)
        logger.info("Generated %d synthetic snapshots (%.0fs game)",
                     len(self._snapshots), duration)
        return len(self._snapshots)

    def save_generated(self, path: str) -> None:
        """Save generated snapshots to a JSON file for later replay."""
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(self._snapshots, f)
        logger.info("Saved %d snapshots to %s", len(self._snapshots), path)

    def run(self) -> None:
        """Run the replay, publishing snapshots to /lol/raw_lcu.

        Blocks until all snapshots are published. Uses speed multiplier
        to control playback rate.
        """
        if not self._snapshots:
            logger.error("No snapshots loaded. Call load_file() or load_generated() first.")
            return

        self._node = CyberNode("replay_simulator")
        self._writer = self._node.CreateWriter("/lol/raw_lcu", RawLCUData)

        self._running = True
        self._published_count = 0
        logger.info(
            "Starting replay: %d snapshots at %.1fx speed",
            len(self._snapshots), self._speed,
        )

        prev_game_time = 0.0
        for i, snapshot in enumerate(self._snapshots):
            if not self._running:
                break

            game_time = snapshot.get("gameData", {}).get("gameTime", 0.0)

            # Sleep for the appropriate duration (scaled by speed)
            if i > 0 and game_time > prev_game_time:
                sleep_s = (game_time - prev_game_time) / self._speed
                time.sleep(max(0.01, sleep_s))

            # Publish
            raw = RawLCUData(
                allgamedata=snapshot,
                timestamp=time.time(),
                lcu_latency_ms=1.0,
                http_status=200,
                source="replay",
            )
            self._writer.Write(raw)
            self._published_count += 1
            prev_game_time = game_time

            # Progress logging every 60 game-seconds
            if i % 60 == 0:
                logger.info(
                    "Replay progress: %.0f / %.0f game-seconds (%d/%d snapshots)",
                    game_time,
                    self._snapshots[-1].get("gameData", {}).get("gameTime", 0),
                    i + 1, len(self._snapshots),
                )

        self._running = False
        logger.info(
            "Replay complete: published %d snapshots", self._published_count
        )

        if self._node:
            self._node.shutdown()

    def stop(self) -> None:
        self._running = False


# ─── CLI Entry Point ─────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="lolbot-HyperAI Replay Simulator",
    )
    parser.add_argument(
        "--file", type=str, help="Path to JSON snapshot file to replay",
    )
    parser.add_argument(
        "--generate", action="store_true",
        help="Generate synthetic game data instead of loading a file",
    )
    parser.add_argument(
        "--duration", type=float, default=1800.0,
        help="Generated game duration in seconds (default: 1800)",
    )
    parser.add_argument(
        "--speed", type=float, default=1.0,
        help="Playback speed multiplier (default: 1.0)",
    )
    parser.add_argument(
        "--save", type=str, help="Save generated data to file",
    )

    args = parser.parse_args()

    configure(LogConfig(console_output=True, json_file_output=False))

    sim = ReplaySimulator(speed=args.speed)

    if args.file:
        sim.load_file(args.file)
    elif args.generate:
        sim.load_generated(duration=args.duration)
        if args.save:
            sim.save_generated(args.save)
    else:
        parser.error("Specify --file or --generate")
        return 1

    try:
        sim.run()
    except KeyboardInterrupt:
        sim.stop()
        logger.info("Replay interrupted")

    return 0


if __name__ == "__main__":
    sys.exit(main())
