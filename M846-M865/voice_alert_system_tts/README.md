# M862: VoiceAlertSystemTTS

Voice alert system with TTS for real-time strategy callouts during gameplay

## Dependencies

M846, M861

## Interfaces

- `configure_tts(engine: str, voice: str, rate: float) -> bool`
- `speak(text: str, priority: int, interrupt: bool) -> bool`
- `queue_alert(alert_type: str, data: dict) -> str`
- `set_alert_rules(rules: dict) -> bool`
- `mute() -> bool`
- `unmute() -> bool`
- `get_queue_status() -> dict`
- `set_cooldown(alert_type: str, seconds: float) -> bool`
- `get_supported_languages() -> list`
- `export_alert_history(session_id: str) -> str`

## Architecture

Follows Seraphine LCU connector patterns with Fiddler network capture.
Network capture preferred over vision for zero hallucination.

## Usage

```python
from voice_alert_system_tts import VoiceAlertSystemTTS

obj = VoiceAlertSystemTTS()
print(obj.get_state())  # "ready"
```
