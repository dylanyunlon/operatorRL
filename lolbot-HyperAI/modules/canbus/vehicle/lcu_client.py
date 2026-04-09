"""
LCUClient — HTTP client for LoL Live Client Data API.
Claude25: Extracted from canbus_component.py. All logic verbatim (Claude1-24).
Apollo ref: modules/drivers/canbus/can_client/ — transport in own file.
"""
from __future__ import annotations
import json, ssl, time, urllib.error, urllib.request
from typing import Any, Dict, Optional, Tuple
from modules.common.status.error_code import ErrorCode, Status

def _create_lcu_ssl_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx

_LCU_SSL_CTX = _create_lcu_ssl_context()
_LCU_BASE_URL = "https://127.0.0.1:2999"
_LCU_TIMEOUT_S = 2.0

class LCUClient:
    """Lightweight HTTP client for the Live Client Data API."""
    def __init__(self, base_url: str = _LCU_BASE_URL, timeout: float = _LCU_TIMEOUT_S) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._request_count: int = 0
        self._error_count: int = 0
        self._last_latency_ms: float = 0.0

    def get(self, endpoint: str) -> Tuple[Optional[Dict[str, Any]], Status]:
        url = f"{self._base_url}{endpoint}"
        self._request_count += 1
        t0 = time.monotonic()
        try:
            req = urllib.request.Request(url, method="GET")
            req.add_header("Accept", "application/json")
            with urllib.request.urlopen(req, timeout=self._timeout, context=_LCU_SSL_CTX) as resp:
                self._last_latency_ms = (time.monotonic() - t0) * 1000
                if resp.status != 200:
                    self._error_count += 1
                    return None, Status.error(ErrorCode.CANBUS_LCU_HTTP_ERROR, f"HTTP {resp.status} from {endpoint}", http_status=resp.status)
                body = resp.read().decode("utf-8")
                return json.loads(body), Status.ok()
        except urllib.error.URLError as exc:
            self._last_latency_ms = (time.monotonic() - t0) * 1000
            self._error_count += 1
            reason = str(getattr(exc, "reason", exc))
            if "Connection refused" in reason or "No connection" in reason:
                return None, Status.error(ErrorCode.CANBUS_LCU_NOT_RUNNING, f"LCU not running: {reason}")
            return None, Status.error(ErrorCode.CANBUS_LCU_CONNECTION_FAILED, f"URL error: {reason}")
        except TimeoutError:
            self._last_latency_ms = (time.monotonic() - t0) * 1000
            self._error_count += 1
            return None, Status.error(ErrorCode.CANBUS_LCU_TIMEOUT, f"Timeout after {self._timeout}s")
        except json.JSONDecodeError as exc:
            self._last_latency_ms = (time.monotonic() - t0) * 1000
            self._error_count += 1
            return None, Status.error(ErrorCode.CANBUS_LCU_INVALID_RESPONSE, f"JSON decode error: {exc}")
        except Exception as exc:
            self._last_latency_ms = (time.monotonic() - t0) * 1000
            self._error_count += 1
            return None, Status.error(ErrorCode.CANBUS_LCU_CONNECTION_FAILED, f"Unexpected error: {type(exc).__name__}: {exc}")

    @property
    def stats(self) -> Dict[str, Any]:
        return {"request_count": self._request_count, "error_count": self._error_count, "last_latency_ms": round(self._last_latency_ms, 2)}
