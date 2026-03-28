"""
Proxifier Config — Auto-configure global proxy rules for game traffic.

Generates Proxifier-compatible configuration files to route game process
traffic through Fiddler proxy while bypassing non-game traffic.

Location: extensions/fiddler-bridge/src/fiddler_bridge/proxifier_config.py
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "fiddler_bridge.proxifier_config.v1"


class ProxifierConfig:
    """Proxifier global proxy rule configurator.

    Manages proxy rules for routing game process traffic through Fiddler,
    generates Proxifier XML configuration, and provides game-specific presets.
    """

    def __init__(
        self,
        proxy_host: str = "127.0.0.1",
        proxy_port: int = 8866,
    ) -> None:
        self.proxy_host = proxy_host
        self.proxy_port = proxy_port
        self._rules: list[dict[str, str]] = []
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    @classmethod
    def lol_preset(cls) -> "ProxifierConfig":
        """Create a config preset for League of Legends.

        Returns:
            ProxifierConfig with LoL process rules.
        """
        cfg = cls()
        cfg.add_rule("LeagueClient.exe", action="proxy")
        cfg.add_rule("LeagueClientUx.exe", action="proxy")
        cfg.add_rule("League of Legends.exe", action="proxy")
        cfg.add_rule("RiotClientServices.exe", action="proxy")
        return cfg

    @classmethod
    def mahjong_preset(cls) -> "ProxifierConfig":
        """Create a config preset for mahjong clients.

        Returns:
            ProxifierConfig with mahjong process rules.
        """
        cfg = cls()
        cfg.add_rule("jantama_mahjongsoul", action="proxy")
        cfg.add_rule("tenhou.exe", action="proxy")
        return cfg

    def add_rule(self, process: str, action: str = "proxy") -> None:
        """Add a proxy rule.

        Args:
            process: Process executable name.
            action: "proxy" to route through Fiddler, "direct" to bypass.
        """
        self._rules.append({"process": process, "action": action})

    def list_rules(self) -> list[dict[str, str]]:
        """List all configured rules.

        Returns:
            List of rule dicts.
        """
        return list(self._rules)

    def generate_config_xml(self) -> str:
        """Generate Proxifier-compatible XML configuration.

        Returns:
            XML string for Proxifier profile.
        """
        lines = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<ProxifierProfile version="101">',
            '  <Options>',
            f'    <ProxyAddress>{self.proxy_host}</ProxyAddress>',
            f'    <ProxyPort>{self.proxy_port}</ProxyPort>',
            '    <ProxyType>HTTPS</ProxyType>',
            '  </Options>',
            '  <Rules>',
        ]
        for rule in self._rules:
            action = "Proxy" if rule["action"] == "proxy" else "Direct"
            lines.append(f'    <Rule>')
            lines.append(f'      <Name>{rule["process"]}</Name>')
            lines.append(f'      <Applications>{rule["process"]}</Applications>')
            lines.append(f'      <Action>{action}</Action>')
            lines.append(f'    </Rule>')
        lines.append('  </Rules>')
        lines.append('</ProxifierProfile>')
        return "\n".join(lines)

    def _fire_evolution(self, data: dict[str, Any]) -> None:
        """Fire evolution callback."""
        if self.evolution_callback is None:
            return
        enriched = {
            "module": "proxifier_config",
            "timestamp": time.time(),
            **data,
        }
        try:
            self.evolution_callback(enriched)
        except Exception as exc:
            logger.warning("Evolution callback error: %s", exc)
