# Protocol Decoder

Game protocol codec registry for operatorRL — provides structured parsing of game network traffic.

## Supported Codecs

| Codec | Game | Protocol | Source |
|---|---|---|---|
| `LiqiCodec` | Majsoul (雀魂) | WebSocket + Protobuf + XOR | Akagi 拿来 |
| `LoLCodec` | League of Legends | HTTP JSON (Live Client Data API) | New |
| `Dota2Codec` | Dota 2 | HTTP JSON (Game State Integration) | New |
| `TenhouCodec` | Tenhou (天凤) | WebSocket XML | Akagi 拿来 |
| `RiichiCityCodec` | Riichi City (一番街) | WebSocket Binary | Akagi 拿来 |

## Architecture

```
Raw bytes (from Fiddler Bridge)
    → CodecRegistry.get("liqi")
        → GameCodec.parse(raw_bytes)
            → Structured dict
                → AgentOS GovernedEnvironment.step()
```

## Quick Start

```python
from protocol_decoder.codec import CodecRegistry, LiqiCodec, LoLCodec

registry = CodecRegistry()
registry.register(LiqiCodec())
registry.register(LoLCodec())

codec = registry.get("lol")
result = codec.parse(raw_json_bytes)
```

## Testing

```bash
PYTHONPATH=src pytest tests/ -v
```
