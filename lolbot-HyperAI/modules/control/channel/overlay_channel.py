"""HUD overlay output channel. Verbatim from Claude25 control_component.py."""
from __future__ import annotations
from typing import Optional
from modules.control.channel.output_channel import OutputChannel
from modules.control.action_dispatch.action_dispatcher import (
    ActionPriority, DispatchAction,
)
from modules.control.overlay.overlay_renderer import OverlayRenderer


class OverlayOutputChannel(OutputChannel):
    def __init__(self, renderer: Optional[OverlayRenderer] = None, cooldown_s: float = 2.0) -> None:
        super().__init__(name="overlay", min_priority=ActionPriority.LOW, cooldown_s=cooldown_s)
        self._renderer = renderer

    def set_renderer(self, renderer: OverlayRenderer) -> None:
        self._renderer = renderer

    def _do_output(self, action: DispatchAction) -> None:
        if self._renderer is None:
            return
        self._renderer.add_notification(
            text=action.text, priority=action.priority.value, source=action.source,
        )

    def flush(self) -> None:
        if self._renderer:
            try:
                self._renderer.process_pending()
            except Exception:
                pass
