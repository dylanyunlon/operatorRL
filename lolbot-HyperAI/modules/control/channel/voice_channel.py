"""Voice TTS output channel. Verbatim from Claude25 control_component.py."""
from __future__ import annotations
from typing import Optional
from modules.control.channel.output_channel import OutputChannel
from modules.control.action_dispatch.action_dispatcher import (
    ActionPriority, DispatchAction,
)
from modules.common.adapters.game_messages import VoiceCommand
from cyber.node.node import Writer


class VoiceOutputChannel(OutputChannel):
    def __init__(self, voice_writer: Optional[Writer] = None, cooldown_s: float = 5.0) -> None:
        super().__init__(name="voice", min_priority=ActionPriority.MEDIUM, cooldown_s=cooldown_s)
        self._writer = voice_writer

    def set_writer(self, writer: Writer) -> None:
        self._writer = writer

    def _do_output(self, action: DispatchAction) -> None:
        if self._writer is None:
            return
        text = action.voice_text or action.text
        if not text:
            return
        cmd = VoiceCommand(
            text=text,
            priority=action.priority.value,
            game_time=action.data.get("game_time", 0.0),
        )
        self._writer.Write(cmd)
