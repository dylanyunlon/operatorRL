"""
Fiddler Rule Engine — Declarative traffic filtering rules.
Location: extensions/fiddler-bridge/src/fiddler_rule_engine.py
Reference: Akagi MITM filter rules, Fiddler Everywhere rule system
"""
from __future__ import annotations
import logging, re, time, uuid
from typing import Any, Callable, Optional
from urllib.parse import urlparse
logger = logging.getLogger(__name__)
_EVOLUTION_KEY: str = "extensions.fiddler_bridge.fiddler_rule_engine.v1"

class FiddlerRuleEngine:
    def __init__(self) -> None:
        self._rules: dict[str, dict[str, Any]] = {}
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def add_rule(self, rule: dict[str, Any]) -> str:
        rule_id = str(uuid.uuid4())
        self._rules[rule_id] = {**rule, "id": rule_id}
        return rule_id

    def remove_rule(self, rule_id: str) -> None:
        self._rules.pop(rule_id, None)

    def list_rules(self) -> list[dict[str, Any]]:
        return list(self._rules.values())

    def evaluate(self, packet: dict[str, Any]) -> bool:
        if self.evolution_callback:
            self.evolution_callback({"type": "rule_evaluated", "key": _EVOLUTION_KEY, "timestamp": time.time()})

        if not self._rules:
            return True

        # Sort rules by priority (lower = higher priority)
        sorted_rules = sorted(self._rules.values(), key=lambda r: r.get("priority", 10))

        for rule in sorted_rules:
            rtype = rule.get("type", "")
            url = packet.get("url", "")
            parsed = urlparse(url)
            domain = parsed.hostname or ""

            if rtype == "domain_blacklist":
                if domain in rule.get("domains", []):
                    return False
            elif rtype == "domain_whitelist":
                if domain not in rule.get("domains", []):
                    return False
            elif rtype == "status_filter":
                status = packet.get("status", 200)
                if status in rule.get("exclude", []):
                    return False
            elif rtype == "method_filter":
                method = packet.get("method", "GET")
                if method not in rule.get("allowed", []):
                    return False
            elif rtype == "url_pattern":
                pattern = rule.get("pattern", "")
                if pattern not in url:
                    return False

        return True
