"""
FiddlerMCPClient — Client for Fiddler MCP network capture bridge.
Claude25: Extracted from canbus_component.py. All logic verbatim (Claude1-24).
"""
from __future__ import annotations
import json, time, urllib.request
from typing import Any, Dict, List, Optional, Tuple
from modules.common.status.error_code import ErrorCode, Status

class FiddlerMCPClient:
    """Client for the Fiddler MCP bridge."""
    def __init__(self, mcp_url: str = "http://127.0.0.1:8866") -> None:
        self._mcp_url = mcp_url
        self._enabled = False
        self._last_poll_time: float = 0.0
    def enable(self) -> None: self._enabled = True
    def disable(self) -> None: self._enabled = False
    def poll_sessions(self) -> Tuple[Optional[List[Dict[str, Any]]], Status]:
        if not self._enabled:
            return None, Status.ok("Fiddler disabled")
        try:
            url = f"{self._mcp_url}/api/sessions"
            req = urllib.request.Request(url, method="GET")
            req.add_header("Accept", "application/json")
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                body = resp.read().decode("utf-8")
                sessions = json.loads(body)
                self._last_poll_time = time.time()
                return sessions, Status.ok()
        except Exception as exc:
            return None, Status.error(ErrorCode.CANBUS_FIDDLER_CONNECTION_FAILED, f"Fiddler poll failed: {exc}")
