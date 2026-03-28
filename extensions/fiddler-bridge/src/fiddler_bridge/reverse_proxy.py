"""
Reverse Proxy Manager — manages Fiddler reverse proxy rules.

Controls local-to-remote port mappings used to intercept game traffic
without requiring system-wide proxy configuration.

Location: extensions/fiddler-bridge/src/fiddler_bridge/reverse_proxy.py
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


class PortConflictError(Exception):
    """Raised when a proxy rule conflicts with an existing one."""
    pass


@dataclass
class ProxyRule:
    """A single reverse proxy rule: local_port → remote_host:remote_port."""
    local_port: int
    remote_host: str
    remote_port: int

    def __post_init__(self) -> None:
        if not (0 < self.local_port <= 65535):
            raise ValueError(f"Invalid local_port: {self.local_port}")
        if not (0 < self.remote_port <= 65535):
            raise ValueError(f"Invalid remote_port: {self.remote_port}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "local_port": self.local_port,
            "remote_host": self.remote_host,
            "remote_port": self.remote_port,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ProxyRule":
        return cls(
            local_port=d["local_port"],
            remote_host=d["remote_host"],
            remote_port=d["remote_port"],
        )


@dataclass
class ProxyStatus:
    """Status of the reverse proxy manager."""
    enabled: bool
    rule_count: int
    rules: list[dict[str, Any]] = field(default_factory=list)


class ReverseProxyManager:
    """Manages Fiddler reverse proxy rules for game traffic interception.

    Usage:
        mgr = ReverseProxyManager()
        mgr.add_rule(local_port=18080, remote_host="127.0.0.1", remote_port=2999)
        mgr.enable()
    """

    def __init__(self) -> None:
        self._rules: list[ProxyRule] = []
        self._enabled: bool = False

    @property
    def rules(self) -> list[ProxyRule]:
        return list(self._rules)

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def active_count(self) -> int:
        return len(self._rules) if self._enabled else 0

    def add_rule(
        self,
        local_port: int,
        remote_host: str,
        remote_port: int,
    ) -> ProxyRule:
        """Add a reverse proxy rule. Raises PortConflictError if local_port already in use."""
        for existing in self._rules:
            if existing.local_port == local_port:
                raise PortConflictError(
                    f"Local port {local_port} already mapped to "
                    f"{existing.remote_host}:{existing.remote_port}"
                )
        rule = ProxyRule(local_port=local_port, remote_host=remote_host, remote_port=remote_port)
        self._rules.append(rule)
        logger.info("Added proxy rule: %d → %s:%d", local_port, remote_host, remote_port)
        return rule

    def remove_rule(self, local_port: int) -> bool:
        """Remove a proxy rule by local port. Returns True if found and removed."""
        for i, rule in enumerate(self._rules):
            if rule.local_port == local_port:
                self._rules.pop(i)
                logger.info("Removed proxy rule for local port %d", local_port)
                return True
        return False

    def enable(self) -> None:
        """Enable reverse proxy."""
        self._enabled = True

    def disable(self) -> None:
        """Disable reverse proxy."""
        self._enabled = False

    def status(self) -> ProxyStatus:
        """Get current proxy status."""
        return ProxyStatus(
            enabled=self._enabled,
            rule_count=len(self._rules),
            rules=[r.to_dict() for r in self._rules],
        )

    def cleanup(self) -> None:
        """Remove all rules and disable proxy."""
        self._rules.clear()
        self._enabled = False
        logger.info("Cleaned up all proxy rules")
