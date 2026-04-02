"""
Control layer configuration — voice, overlay, and dispatch settings.
=====================================================================
lolbot-HyperAI · Control Layer · Conf

Architecture position:
    modules/control/conf/control_config.py   ← YOU ARE HERE
    ├─ Used by: modules/control/control_component.py
    └─ Integrated into: conf/default_config.py (top-level tree)

Apollo reference:
    modules/control/conf/control_conf.pb.txt

Design notes:
    - Dataclass hierarchy mirrors Apollo's protobuf config pattern
    - Sensible defaults for all fields — works without config file
    - from_dict() class method for YAML/JSON loading
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class VoiceConfig:
    """TTS voice output configuration."""
    enabled: bool = True
    tts_rate_wpm: int = 180
    tts_volume: float = 0.8
    min_interval_s: float = 3.0       # min seconds between voice outputs
    win_update_interval_s: float = 30.0
    max_queue_depth: int = 8
    critical_bypass_cooldown: bool = True  # CRITICAL priority skips cooldown

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "VoiceConfig":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class OverlayConfig:
    """HUD overlay configuration."""
    enabled: bool = True
    max_elements: int = 8
    default_ttl_s: float = 10.0
    win_prob_position: str = "top_right"
    strategy_position: str = "top_left"
    objective_position: str = "bottom_right"
    font_size_px: int = 14
    opacity: float = 0.85

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "OverlayConfig":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class DispatchConfig:
    """Action dispatch configuration."""
    enabled: bool = True
    dedup_window_s: float = 3.0       # dedup window for same dedup_key
    max_queue_size: int = 32
    voice_min_priority: int = 2       # min priority for voice output
    log_all_actions: bool = True
    batch_window_ms: float = 100.0    # batch actions within this window

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "DispatchConfig":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class ControlConfig:
    """Top-level control layer configuration."""
    tick_interval_ms: float = 200.0   # 5Hz control loop
    warn_threshold_ms: float = 150.0
    voice: VoiceConfig = field(default_factory=VoiceConfig)
    overlay: OverlayConfig = field(default_factory=OverlayConfig)
    dispatch: DispatchConfig = field(default_factory=DispatchConfig)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ControlConfig":
        voice = VoiceConfig.from_dict(d.get("voice", {}))
        overlay = OverlayConfig.from_dict(d.get("overlay", {}))
        dispatch = DispatchConfig.from_dict(d.get("dispatch", {}))
        return cls(
            tick_interval_ms=d.get("tick_interval_ms", 200.0),
            warn_threshold_ms=d.get("warn_threshold_ms", 150.0),
            voice=voice,
            overlay=overlay,
            dispatch=dispatch,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tick_interval_ms": self.tick_interval_ms,
            "warn_threshold_ms": self.warn_threshold_ms,
            "voice": {
                k: getattr(self.voice, k)
                for k in self.voice.__dataclass_fields__
            },
            "overlay": {
                k: getattr(self.overlay, k)
                for k in self.overlay.__dataclass_fields__
            },
            "dispatch": {
                k: getattr(self.dispatch, k)
                for k in self.dispatch.__dataclass_fields__
            },
        }
