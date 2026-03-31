#!/usr/bin/env python3
"""
M893 — ProxifierAutoConfigurator
=================================
Auto-detects LoL client processes and generates Proxifier rules to route
LeagueClient.exe/LeagueClientUx.exe through Fiddler, while League of
Legends.exe game traffic goes direct. Validates config before applying.

Dependencies: none (standalone)
Reference: M866-M885 proxifier_rule_engine pattern
"""
from __future__ import annotations
import asyncio, json, logging, os, platform, re, subprocess, time, xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from enum import Enum, auto

logger = logging.getLogger("M893.ProxifierAutoConfigurator")

FIDDLER_PROXY_HOST = "127.0.0.1"
FIDDLER_PROXY_PORT = 8866
LOL_PROCESSES_PROXY = ["LeagueClient.exe", "LeagueClientUx.exe", "RiotClientServices.exe"]
LOL_PROCESSES_DIRECT = ["League of Legends.exe"]
PROXIFIER_PROFILE_EXT = ".ppx"


class ProxyAction(Enum):
    PROXY = "proxy"
    DIRECT = "direct"
    BLOCK = "block"


class ConfigStatus(Enum):
    VALID = auto()
    INVALID = auto()
    NOT_FOUND = auto()
    APPLIED = auto()
    DRY_RUN = auto()


@dataclass
class ProxifierRule:
    name: str
    applications: List[str]
    action: ProxyAction
    proxy_host: str = ""
    proxy_port: int = 0
    enabled: bool = True
    priority: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "apps": self.applications,
                "action": self.action.value, "proxy": f"{self.proxy_host}:{self.proxy_port}" if self.proxy_host else "direct",
                "enabled": self.enabled, "priority": self.priority}


@dataclass
class ProxifierConfig:
    rules: List[ProxifierRule] = field(default_factory=list)
    proxy_host: str = FIDDLER_PROXY_HOST
    proxy_port: int = FIDDLER_PROXY_PORT
    resolve_dns_through_proxy: bool = True
    status: ConfigStatus = ConfigStatus.VALID
    validation_errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {"proxy": f"{self.proxy_host}:{self.proxy_port}",
                "rules": [r.to_dict() for r in self.rules],
                "dns_through_proxy": self.resolve_dns_through_proxy,
                "status": self.status.name, "errors": self.validation_errors}


class ProcessDetector:
    """Detect running LoL-related processes."""

    @staticmethod
    def find_lol_processes() -> Dict[str, List[int]]:
        """Return dict of process_name → list of PIDs."""
        result: Dict[str, List[int]] = {}
        try:
            if platform.system() == "Windows":
                output = subprocess.check_output(
                    ["tasklist", "/FO", "CSV", "/NH"], text=True, timeout=5
                )
                for line in output.strip().split("\n"):
                    parts = line.strip().strip('"').split('","')
                    if len(parts) >= 2:
                        name, pid = parts[0], parts[1]
                        all_lol = LOL_PROCESSES_PROXY + LOL_PROCESSES_DIRECT
                        if name in all_lol:
                            result.setdefault(name, []).append(int(pid))
            else:
                # Linux/Mac: mock for development
                logger.debug("Non-Windows OS, using mock process detection")
        except Exception as exc:
            logger.warning("Process detection failed: %s", exc)
        return result

    @staticmethod
    def find_lol_install_path() -> Optional[str]:
        """Attempt to find LoL installation directory."""
        common_paths = [
            r"C:\Riot Games\League of Legends",
            r"D:\Riot Games\League of Legends",
            r"C:\Program Files\Riot Games\League of Legends",
            r"C:\Program Files (x86)\Riot Games\League of Legends",
        ]
        for p in common_paths:
            if os.path.isdir(p):
                return p
        return None

    @staticmethod
    def is_fiddler_running() -> bool:
        """Check if Fiddler Everywhere is running."""
        try:
            if platform.system() == "Windows":
                output = subprocess.check_output(
                    ["tasklist", "/FI", "IMAGENAME eq Fiddler*", "/FO", "CSV", "/NH"],
                    text=True, timeout=5
                )
                return "Fiddler" in output
        except Exception:
            pass
        return False


class ConfigValidator:
    """Validates Proxifier configuration before applying."""

    @staticmethod
    def validate(config: ProxifierConfig) -> ConfigStatus:
        errors = []
        if not config.rules:
            errors.append("No rules defined")

        proxy_rules = [r for r in config.rules if r.action == ProxyAction.PROXY]
        if proxy_rules:
            for r in proxy_rules:
                if not r.proxy_host or r.proxy_port <= 0:
                    errors.append(f"Rule '{r.name}' has invalid proxy address")

        # Check for conflicting rules
        all_apps = []
        for r in config.rules:
            for app in r.applications:
                if app in all_apps:
                    errors.append(f"Duplicate app '{app}' in multiple rules")
                all_apps.append(app)

        # Ensure game exe goes direct
        for r in config.rules:
            if r.action == ProxyAction.PROXY:
                for app in r.applications:
                    if app in LOL_PROCESSES_DIRECT:
                        errors.append(f"Game process '{app}' must not be proxied (latency)")

        config.validation_errors = errors
        config.status = ConfigStatus.INVALID if errors else ConfigStatus.VALID
        return config.status


class ProxifierProfileGenerator:
    """Generates Proxifier .ppx profile XML."""

    @staticmethod
    def generate_ppx(config: ProxifierConfig) -> str:
        """Generate Proxifier profile XML content."""
        root = ET.Element("ProxifierProfile")
        root.set("version", "100")
        root.set("platform", "Windows")

        options = ET.SubElement(root, "Options")
        resolve = ET.SubElement(options, "Resolve")
        resolve.text = "true" if config.resolve_dns_through_proxy else "false"

        proxy_list = ET.SubElement(root, "ProxyList")
        proxy = ET.SubElement(proxy_list, "Proxy")
        proxy.set("id", "100")
        proxy.set("type", "HTTPS")
        addr = ET.SubElement(proxy, "Address")
        addr.text = config.proxy_host
        port = ET.SubElement(proxy, "Port")
        port.text = str(config.proxy_port)

        rule_list = ET.SubElement(root, "RuleList")
        for i, rule in enumerate(config.rules):
            rule_elem = ET.SubElement(rule_list, "Rule")
            rule_elem.set("enabled", "true" if rule.enabled else "false")
            name_elem = ET.SubElement(rule_elem, "Name")
            name_elem.text = rule.name
            apps_elem = ET.SubElement(rule_elem, "Applications")
            apps_elem.text = ";".join(rule.applications)
            action_elem = ET.SubElement(rule_elem, "Action")
            if rule.action == ProxyAction.PROXY:
                action_elem.set("type", "Proxy")
                action_elem.set("proxyId", "100")
            elif rule.action == ProxyAction.DIRECT:
                action_elem.set("type", "Direct")
            else:
                action_elem.set("type", "Block")

        return ET.tostring(root, encoding="unicode", xml_declaration=True)


class ProxifierAutoConfigurator:
    """
    Automatically configures Proxifier for LoL traffic interception.

    Flow:
      1. Detect LoL processes → determine which are running
      2. Generate rules: client processes → Fiddler proxy, game → direct
      3. Validate configuration (no conflicts, correct proxy settings)
      4. Optionally apply via Proxifier CLI or profile export

    Safety: Always validates before applying. Dry-run mode available.
    Game process (League of Legends.exe) NEVER goes through proxy to
    avoid adding latency to real-time gameplay network packets.
    """

    def __init__(self, fiddler_host: str = FIDDLER_PROXY_HOST,
                 fiddler_port: int = FIDDLER_PROXY_PORT):
        self._fiddler_host = fiddler_host
        self._fiddler_port = fiddler_port
        self._detector = ProcessDetector()
        self._validator = ConfigValidator()
        self._generator = ProxifierProfileGenerator()
        self._current_config: Optional[ProxifierConfig] = None
        self._config_history: List[ProxifierConfig] = []
        self._stats = {"configs_generated": 0, "validations_passed": 0,
                       "validations_failed": 0, "profiles_exported": 0}
        logger.info("ProxifierAutoConfigurator initialized (proxy=%s:%d)",
                     fiddler_host, fiddler_port)

    def generate_config(self) -> ProxifierConfig:
        """Generate optimal Proxifier config for current system state."""
        rules = []
        # Rule 1: LoL client processes → Fiddler proxy
        rules.append(ProxifierRule(
            name="LoL Client → Fiddler", applications=list(LOL_PROCESSES_PROXY),
            action=ProxyAction.PROXY, proxy_host=self._fiddler_host,
            proxy_port=self._fiddler_port, priority=1,
        ))
        # Rule 2: Game process → Direct (CRITICAL: no proxy latency for gameplay)
        rules.append(ProxifierRule(
            name="LoL Game → Direct", applications=list(LOL_PROCESSES_DIRECT),
            action=ProxyAction.DIRECT, priority=0,
        ))

        config = ProxifierConfig(
            rules=rules, proxy_host=self._fiddler_host,
            proxy_port=self._fiddler_port,
        )
        status = self._validator.validate(config)
        self._current_config = config
        self._config_history.append(config)
        self._stats["configs_generated"] += 1

        if status == ConfigStatus.VALID:
            self._stats["validations_passed"] += 1
            logger.info("Config generated: %d rules, VALID", len(rules))
        else:
            self._stats["validations_failed"] += 1
            logger.warning("Config INVALID: %s", config.validation_errors)
        return config

    def export_profile(self, output_path: str) -> bool:
        """Export current config as Proxifier .ppx profile."""
        if not self._current_config:
            self.generate_config()
        if self._current_config.status != ConfigStatus.VALID:
            logger.error("Cannot export invalid config")
            return False
        try:
            xml_content = self._generator.generate_ppx(self._current_config)
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(xml_content)
            self._stats["profiles_exported"] += 1
            logger.info("Proxifier profile exported: %s", output_path)
            return True
        except Exception as exc:
            logger.error("Profile export failed: %s", exc)
            return False

    def get_system_status(self) -> Dict[str, Any]:
        """Check current system state for LoL and Fiddler."""
        processes = self._detector.find_lol_processes()
        fiddler_running = self._detector.is_fiddler_running()
        lol_path = self._detector.find_lol_install_path()
        return {
            "lol_processes": {name: pids for name, pids in processes.items()},
            "fiddler_running": fiddler_running,
            "lol_install_path": lol_path,
            "proxy_target": f"{self._fiddler_host}:{self._fiddler_port}",
            "config_valid": self._current_config.status == ConfigStatus.VALID if self._current_config else None,
        }

    def export_stats(self) -> Dict[str, Any]:
        return {"configurator_stats": self._stats,
                "current_config": self._current_config.to_dict() if self._current_config else None}



# ---------------------------------------------------------------------------
# Extended ProxifierAutoConfigurator utilities
# ---------------------------------------------------------------------------

class FiddlerCertificateManager:
    """Manages Fiddler HTTPS certificate installation for LoL client."""

    def __init__(self):
        self._cert_installed = False
        self._cert_path = ""

    def check_certificate(self) -> bool:
        """Check if Fiddler root certificate is installed."""
        cert_locations = [
            os.path.expanduser("~/.fiddler/FiddlerRoot.cer"),
            os.path.join(os.environ.get("LOCALAPPDATA", ""), "Fiddler", "FiddlerRoot.cer"),
        ]
        for loc in cert_locations:
            if os.path.exists(loc):
                self._cert_path = loc
                self._cert_installed = True
                return True
        return False

    def get_install_instructions(self) -> List[str]:
        return [
            "1. Open Fiddler Everywhere → Settings → HTTPS",
            "2. Enable 'Capture HTTPS traffic'",
            "3. Click 'Trust root certificate'",
            "4. Follow OS prompts to install the certificate",
            "5. Restart League of Legends client",
        ]

    def status(self) -> Dict[str, Any]:
        return {"installed": self._cert_installed, "path": self._cert_path}


class ProxifierDiagnostics:
    """Diagnostic tools for Proxifier configuration issues."""

    def __init__(self, configurator: ProxifierAutoConfigurator):
        self._config = configurator

    def run_diagnostics(self) -> List[Dict[str, Any]]:
        checks = []

        # Check 1: Fiddler running
        fiddler_ok = ProcessDetector.is_fiddler_running()
        checks.append({
            "check": "Fiddler running", "passed": fiddler_ok,
            "detail": "Fiddler Everywhere detected" if fiddler_ok else "Fiddler not found",
        })

        # Check 2: LoL processes
        procs = ProcessDetector.find_lol_processes()
        lol_ok = len(procs) > 0
        checks.append({
            "check": "LoL processes", "passed": lol_ok,
            "detail": f"Found: {list(procs.keys())}" if lol_ok else "No LoL processes found",
        })

        # Check 3: Install path
        path = ProcessDetector.find_lol_install_path()
        checks.append({
            "check": "LoL install path", "passed": path is not None,
            "detail": path or "Not found in common locations",
        })

        # Check 4: Config valid
        config = self._config._current_config
        config_ok = config and config.status == ConfigStatus.VALID
        checks.append({
            "check": "Config valid", "passed": config_ok,
            "detail": "Valid" if config_ok else (config.validation_errors if config else ["Not generated"]),
        })

        # Check 5: Proxy port available
        import socket
        port_ok = False
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(1)
            result = s.connect_ex(("127.0.0.1", FIDDLER_PROXY_PORT))
            port_ok = result == 0
            s.close()
        except Exception:
            pass
        checks.append({
            "check": f"Proxy port {FIDDLER_PROXY_PORT}", "passed": port_ok,
            "detail": "Accepting connections" if port_ok else "Port not responding",
        })

        return checks


class ProxifierRuleOptimizer:
    """Optimizes Proxifier rules based on observed traffic patterns."""

    def __init__(self):
        self._traffic_stats: Dict[str, int] = collections.defaultdict(int)

    def record_traffic(self, process_name: str):
        self._traffic_stats[process_name] += 1

    def suggest_optimizations(self, config: ProxifierConfig) -> List[str]:
        suggestions = []

        # Check if any configured processes have zero traffic
        for rule in config.rules:
            for app in rule.applications:
                if self._traffic_stats.get(app, 0) == 0:
                    suggestions.append(f"Process '{app}' has no observed traffic — verify it's running")

        # Check for unrouted LoL processes
        all_configured = set()
        for rule in config.rules:
            all_configured.update(rule.applications)

        for proc_name in self._traffic_stats:
            if proc_name not in all_configured:
                suggestions.append(f"Unrouted process detected: '{proc_name}' — consider adding a rule")

        return suggestions

    def get_traffic_report(self) -> Dict[str, int]:
        return dict(self._traffic_stats)



class NetworkTestSuite:
    """Tests network connectivity through the proxy chain."""

    @staticmethod
    async def test_proxy_connectivity(host: str, port: int) -> Dict[str, Any]:
        import socket
        result = {"host": host, "port": port}
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(3)
            start = time.monotonic()
            s.connect((host, port))
            latency = (time.monotonic() - start) * 1000
            s.close()
            result.update({"reachable": True, "latency_ms": round(latency, 1)})
        except Exception as exc:
            result.update({"reachable": False, "error": str(exc)})
        return result

    @staticmethod
    async def test_lcu_connectivity() -> Dict[str, Any]:
        """Test if LCU API is accessible."""
        result = {"endpoint": "LCU API"}
        try:
            import socket
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(2)
            # LCU runs on a random port, check if any process is listening
            s.connect(("127.0.0.1", 2999))
            s.close()
            result["reachable"] = True
        except Exception:
            result["reachable"] = False
        return result

    @staticmethod
    async def run_all_tests(config: ProxifierConfig) -> List[Dict[str, Any]]:
        results = []
        results.append(await NetworkTestSuite.test_proxy_connectivity(
            config.proxy_host, config.proxy_port))
        results.append(await NetworkTestSuite.test_lcu_connectivity())
        return results


class ConfigHistory:
    """Maintains history of Proxifier configs for rollback."""
    def __init__(self, max_history: int = 20):
        self._history: List[Dict[str, Any]] = []
        self._max = max_history

    def save(self, config: ProxifierConfig):
        self._history.append({
            "config": config.to_dict(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        if len(self._history) > self._max:
            self._history = self._history[-self._max:]

    def get_previous(self, steps_back: int = 1) -> Optional[Dict]:
        idx = len(self._history) - 1 - steps_back
        if 0 <= idx < len(self._history):
            return self._history[idx]
        return None

    def get_all(self) -> List[Dict]:
        return list(self._history)
