# M859: NetworkProtocolDecoder

Decodes LoL network protocols via Fiddler proxy integration with Proxifier

## Dependencies

M846

## Interfaces

- `configure_fiddler(host: str, port: int, api_key: str) -> bool`
- `configure_proxifier(rules: dict) -> bool`
- `start_capture() -> bool`
- `stop_capture() -> bool`
- `decode_packet(raw_data: bytes) -> dict`
- `filter_lol_traffic(sessions: list) -> list`
- `extract_api_calls(sessions: list) -> list`
- `get_capture_stats() -> dict`
- `export_har(path: str) -> str`
- `replay_session(session_id: str) -> dict`

## Architecture

Follows Seraphine LCU connector patterns with Fiddler network capture.
Network capture preferred over vision for zero hallucination.

## Usage

```python
from network_protocol_decoder import NetworkProtocolDecoder

obj = NetworkProtocolDecoder()
print(obj.get_state())  # "ready"
```
