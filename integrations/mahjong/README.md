# Mahjong Agent Integration

Agentic mahjong AI integration for operatorRL — bridges Akagi's MITM architecture 
with the self-evolving training loop.

## Architecture

```
Game Server (Majsoul/Tenhou/Riichi City)
    ↕ WebSocket
Fiddler Bridge (extensions/fiddler-bridge)
    ↕ MCP
MitmHandlerABC → MajsoulMitmHandler
    ↕ raw bytes
MahjongBridgeBase → MajsoulBridge
    ↕ mjai events
MahjongAgent (orchestrator)
    ↕ mjai events/actions
MjaiBotBase → MortalAdapter
    ↕ decisions
TrainingCollector → AgentLightning
```

## Supported Platforms

| Platform | Protocol | Bridge | Status |
|----------|----------|--------|--------|
| 雀魂 Majsoul | liqi protobuf | `MajsoulBridge` | ✅ Core |
| 天凤 Tenhou | XML over WebSocket | Planned | 🔜 |
| 一番街 Riichi City | Binary | Planned | 🔜 |

## Quick Start

```python
from mahjong_agent.agent import MahjongAgent, AgentConfig
from mahjong_agent.models.mortal_adapter import MortalAdapter

# Create agent with Mortal backend
agent = MahjongAgent(config=AgentConfig(player_name="my_bot"))
agent.set_bot(MortalAdapter())

# Process game events
action = agent.on_message({"type": "start_game", "id": 0, "names": ["0","1","2","3"]})
action = agent.on_message({"type": "tsumo", "actor": 0, "pai": "5m"})
```

## Training Data Collection

```python
from mahjong_agent.training_collector import TrainingCollector

collector = TrainingCollector()
collector.record(state={"hand": ["1m","2m","3m"]}, action={"type": "dahai", "pai": "1m"}, reward=0.0)
collector.mark_episode_end(final_reward=10.0)
batch = collector.to_agent_lightning_batch()
```

## Reward Design

| Event | Reward | Rationale |
|-------|--------|-----------|
| Win (agari) | +score/10000 | Proportional to hand value |
| Lose (deal-in) | -score/10000 | Proportional to loss |
| Tenpai | +0.5 | Progress signal |
| Riichi | -0.3 | Risk of 1000pt deposit |
| Draw | ~0 | Neutral outcome |
| 1st place | +20 | Placement bonus |
| 4th place | -20 | Placement penalty |

## File Map

| File | Purpose |
|------|---------|
| `agent.py` | Central orchestrator |
| `bridge/bridge_base.py` | MITM bridge ABC |
| `bridge/majsoul_bridge.py` | 雀魂 bridge (tile mapping + state) |
| `bridge/liqi_parser.py` | Protocol decoder adapter |
| `bridge/mitm_abc.py` | WebSocket lifecycle ABC |
| `bridge/mitm_majsoul.py` | 雀魂 MITM handler |
| `models/mjai_bot_base.py` | Bot decision ABC |
| `models/mortal_adapter.py` | Mortal DRL adapter |
| `training_collector.py` | State/action/reward collector |
| `reward.py` | Mahjong reward functions |
