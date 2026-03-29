"""
TDD Tests for M376-M385: Cross-Game Unified CLI + Configuration System.

100 tests (10 per module), designed for ~50% initial failure rate.
Tests written BEFORE implementation per TDD methodology.

Reference projects (拿来主义):
  - PARL: AgentBase lifecycle → M378 game_launcher
  - DI-star: BaseLearner config/hooks → M377 config_loader, M380 log_manager
  - Akagi: Controller plugin discovery → M381 plugin_loader
  - Akagi: settings.py → M377 config_loader
"""

import importlib.util
import json
import os
import sys
import tempfile
import time

import pytest

# ---------------------------------------------------------------------------
# Helper: load module from file path
# ---------------------------------------------------------------------------
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
_CLI_SRC = os.path.join(_ROOT, "agentos", "cli")


def _load(name: str, filepath: str):
    spec = importlib.util.spec_from_file_location(name, filepath)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def cli_main_mod():
    return _load("cli_main", os.path.join(_CLI_SRC, "main.py"))


@pytest.fixture(scope="module")
def config_mod():
    return _load("config_loader", os.path.join(_CLI_SRC, "config_loader.py"))


@pytest.fixture(scope="module")
def launcher_mod():
    return _load("game_launcher", os.path.join(_CLI_SRC, "game_launcher.py"))


@pytest.fixture(scope="module")
def status_mod():
    return _load("status_reporter", os.path.join(_CLI_SRC, "status_reporter.py"))


@pytest.fixture(scope="module")
def log_mod():
    return _load("log_manager", os.path.join(_CLI_SRC, "log_manager.py"))


@pytest.fixture(scope="module")
def plugin_mod():
    return _load("plugin_loader", os.path.join(_CLI_SRC, "plugin_loader.py"))


@pytest.fixture(scope="module")
def cred_mod():
    return _load("credential_manager", os.path.join(_CLI_SRC, "credential_manager.py"))


@pytest.fixture(scope="module")
def health_mod():
    return _load("health_monitor", os.path.join(_CLI_SRC, "health_monitor.py"))


@pytest.fixture(scope="module")
def upgrade_mod():
    return _load("upgrade_checker", os.path.join(_CLI_SRC, "upgrade_checker.py"))


@pytest.fixture(scope="module")
def session_mod():
    return _load("session_recorder", os.path.join(_CLI_SRC, "session_recorder.py"))


# ===================================================================
# M376: CLI Main — 10 tests
# ===================================================================
class TestCLIMain:
    """argparse CLI entry + subcommand routing."""

    def test_create_parser(self, cli_main_mod):
        parser = cli_main_mod.create_parser()
        assert parser is not None

    def test_parse_start_command(self, cli_main_mod):
        parser = cli_main_mod.create_parser()
        args = parser.parse_args(["start", "--game", "lol"])
        assert args.command == "start"
        assert args.game == "lol"

    def test_parse_stop_command(self, cli_main_mod):
        parser = cli_main_mod.create_parser()
        args = parser.parse_args(["stop", "--game", "lol"])
        assert args.command == "stop"

    def test_parse_status_command(self, cli_main_mod):
        parser = cli_main_mod.create_parser()
        args = parser.parse_args(["status"])
        assert args.command == "status"

    def test_parse_config_path(self, cli_main_mod):
        parser = cli_main_mod.create_parser()
        args = parser.parse_args(["start", "--game", "dota2", "--config", "/tmp/c.yaml"])
        assert args.config == "/tmp/c.yaml"

    def test_route_start(self, cli_main_mod):
        result = cli_main_mod.route_command("start", {"game": "lol"})
        assert result["status"] in ("started", "ok", "routed")

    def test_route_stop(self, cli_main_mod):
        result = cli_main_mod.route_command("stop", {"game": "lol"})
        assert result["status"] in ("stopped", "ok", "routed")

    def test_route_status(self, cli_main_mod):
        result = cli_main_mod.route_command("status", {})
        assert isinstance(result, dict)

    def test_route_unknown_command(self, cli_main_mod):
        result = cli_main_mod.route_command("unknown_cmd", {})
        assert result.get("error") is not None or result.get("status") == "unknown"

    def test_version_flag(self, cli_main_mod):
        ver = cli_main_mod.get_version()
        assert isinstance(ver, str)
        assert len(ver) > 0


# ===================================================================
# M377: ConfigLoader — 10 tests
# ===================================================================
class TestConfigLoader:
    """YAML/JSON config + env var override."""

    def test_load_empty_dict(self, config_mod):
        loader = config_mod.ConfigLoader()
        cfg = loader.load_dict({})
        assert isinstance(cfg, dict)

    def test_load_dict_preserves_values(self, config_mod):
        loader = config_mod.ConfigLoader()
        cfg = loader.load_dict({"game": "lol", "interval": 1.0})
        assert cfg["game"] == "lol"

    def test_load_json_string(self, config_mod):
        loader = config_mod.ConfigLoader()
        cfg = loader.load_json_string('{"game": "dota2", "debug": true}')
        assert cfg["game"] == "dota2"
        assert cfg["debug"] is True

    def test_load_json_file(self, config_mod):
        loader = config_mod.ConfigLoader()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"game": "mahjong"}, f)
            f.flush()
            cfg = loader.load_file(f.name)
        os.unlink(f.name)
        assert cfg["game"] == "mahjong"

    def test_env_override(self, config_mod):
        loader = config_mod.ConfigLoader()
        os.environ["AGENTOS_GAME"] = "lol_test"
        cfg = loader.load_dict({"game": "default"})
        cfg = loader.apply_env_overrides(cfg, prefix="AGENTOS_")
        assert cfg["game"] == "lol_test"
        del os.environ["AGENTOS_GAME"]

    def test_get_nested(self, config_mod):
        loader = config_mod.ConfigLoader()
        cfg = loader.load_dict({"a": {"b": {"c": 42}}})
        assert loader.get_nested(cfg, "a.b.c") == 42

    def test_get_nested_missing_returns_default(self, config_mod):
        loader = config_mod.ConfigLoader()
        cfg = loader.load_dict({"a": 1})
        assert loader.get_nested(cfg, "x.y.z", default="N/A") == "N/A"

    def test_merge_configs(self, config_mod):
        loader = config_mod.ConfigLoader()
        base = {"a": 1, "b": 2}
        override = {"b": 3, "c": 4}
        merged = loader.merge(base, override)
        assert merged["a"] == 1
        assert merged["b"] == 3
        assert merged["c"] == 4

    def test_validate_required_keys(self, config_mod):
        loader = config_mod.ConfigLoader()
        cfg = {"game": "lol"}
        assert loader.validate(cfg, required=["game"]) is True

    def test_validate_missing_key_fails(self, config_mod):
        loader = config_mod.ConfigLoader()
        cfg = {"game": "lol"}
        assert loader.validate(cfg, required=["game", "interval"]) is False


# ===================================================================
# M378: GameLauncher — 10 tests
# ===================================================================
class TestGameLauncher:
    """Unified start/stop + process management."""

    def test_init(self, launcher_mod):
        gl = launcher_mod.GameLauncher()
        assert gl is not None

    def test_register_game(self, launcher_mod):
        gl = launcher_mod.GameLauncher()
        gl.register_game("lol", {"entry": "lol_agent.main"})
        assert "lol" in gl.list_games()

    def test_launch_registered_game(self, launcher_mod):
        gl = launcher_mod.GameLauncher()
        gl.register_game("lol", {"entry": "lol_agent.main"})
        result = gl.launch("lol")
        assert result["status"] in ("launched", "running")

    def test_launch_unregistered_game_error(self, launcher_mod):
        gl = launcher_mod.GameLauncher()
        with pytest.raises(KeyError):
            gl.launch("nonexistent")

    def test_stop_game(self, launcher_mod):
        gl = launcher_mod.GameLauncher()
        gl.register_game("lol", {"entry": "lol_agent.main"})
        gl.launch("lol")
        gl.stop("lol")
        status = gl.get_game_status("lol")
        assert status["status"] in ("stopped", "idle")

    def test_get_game_status(self, launcher_mod):
        gl = launcher_mod.GameLauncher()
        gl.register_game("lol", {"entry": "lol_agent.main"})
        status = gl.get_game_status("lol")
        assert "status" in status

    def test_stop_not_running_game(self, launcher_mod):
        gl = launcher_mod.GameLauncher()
        gl.register_game("lol", {"entry": "lol_agent.main"})
        gl.stop("lol")  # should not raise
        status = gl.get_game_status("lol")
        assert status["status"] in ("stopped", "idle")

    def test_list_games_empty(self, launcher_mod):
        gl = launcher_mod.GameLauncher()
        assert gl.list_games() == []

    def test_multiple_games(self, launcher_mod):
        gl = launcher_mod.GameLauncher()
        gl.register_game("lol", {"entry": "lol_main"})
        gl.register_game("dota2", {"entry": "dota2_main"})
        assert len(gl.list_games()) == 2

    def test_evolution_callback(self, launcher_mod):
        events = []
        gl = launcher_mod.GameLauncher()
        gl.evolution_callback = lambda e: events.append(e)
        gl.register_game("lol", {"entry": "lol_agent"})
        gl.launch("lol")
        assert len(events) >= 1


# ===================================================================
# M379: StatusReporter — 10 tests
# ===================================================================
class TestStatusReporter:
    """Real-time status output + progress bars."""

    def test_init(self, status_mod):
        sr = status_mod.StatusReporter()
        assert sr is not None

    def test_report_text(self, status_mod):
        sr = status_mod.StatusReporter()
        text = sr.format_status({"game": "lol", "state": "running", "uptime": 120})
        assert "lol" in text

    def test_format_progress(self, status_mod):
        sr = status_mod.StatusReporter()
        bar = sr.format_progress(current=50, total=100)
        assert isinstance(bar, str)
        assert "50" in bar or "%" in bar

    def test_format_progress_zero_total(self, status_mod):
        sr = status_mod.StatusReporter()
        bar = sr.format_progress(current=0, total=0)
        assert isinstance(bar, str)  # no ZeroDivisionError

    def test_format_progress_100_percent(self, status_mod):
        sr = status_mod.StatusReporter()
        bar = sr.format_progress(current=100, total=100)
        assert "100" in bar

    def test_add_section(self, status_mod):
        sr = status_mod.StatusReporter()
        sr.add_section("Network", {"latency": "15ms", "status": "ok"})
        sections = sr.get_sections()
        assert "Network" in sections

    def test_clear_sections(self, status_mod):
        sr = status_mod.StatusReporter()
        sr.add_section("A", {"x": 1})
        sr.clear_sections()
        assert len(sr.get_sections()) == 0

    def test_format_multi_section(self, status_mod):
        sr = status_mod.StatusReporter()
        sr.add_section("CPU", {"usage": "45%"})
        sr.add_section("Memory", {"usage": "60%"})
        text = sr.render_all()
        assert "CPU" in text
        assert "Memory" in text

    def test_format_elapsed_time(self, status_mod):
        sr = status_mod.StatusReporter()
        formatted = sr.format_elapsed(3661)  # 1h 1m 1s
        assert "1h" in formatted or "01:" in formatted

    def test_format_elapsed_zero(self, status_mod):
        sr = status_mod.StatusReporter()
        formatted = sr.format_elapsed(0)
        assert "0" in formatted


# ===================================================================
# M380: LogManager — 10 tests
# ===================================================================
class TestLogManager:
    """Leveled logging + rotation + coloring."""

    def test_init(self, log_mod):
        lm = log_mod.LogManager()
        assert lm is not None

    def test_set_level(self, log_mod):
        lm = log_mod.LogManager()
        lm.set_level("DEBUG")
        assert lm.get_level() == "DEBUG"

    def test_set_level_invalid_defaults_info(self, log_mod):
        lm = log_mod.LogManager()
        lm.set_level("INVALID_LEVEL")
        assert lm.get_level() in ("INFO", "WARNING")

    def test_log_message(self, log_mod):
        lm = log_mod.LogManager()
        lm.log("INFO", "test message")
        history = lm.get_recent(1)
        assert len(history) == 1
        assert "test message" in history[0]["message"]

    def test_log_debug(self, log_mod):
        lm = log_mod.LogManager()
        lm.set_level("DEBUG")
        lm.log("DEBUG", "debug msg")
        history = lm.get_recent(1)
        assert history[0]["level"] == "DEBUG"

    def test_log_filtering(self, log_mod):
        lm = log_mod.LogManager()
        lm.set_level("WARNING")
        lm.log("DEBUG", "should be filtered")
        lm.log("WARNING", "should appear")
        history = lm.get_recent(10)
        assert len(history) == 1

    def test_get_recent_limit(self, log_mod):
        lm = log_mod.LogManager()
        lm.set_level("DEBUG")
        for i in range(20):
            lm.log("INFO", f"msg {i}")
        recent = lm.get_recent(5)
        assert len(recent) == 5

    def test_colorize(self, log_mod):
        lm = log_mod.LogManager()
        colored = lm.colorize("ERROR", "bad thing")
        assert isinstance(colored, str)
        # Should contain ANSI codes or plain text
        assert "bad thing" in colored

    def test_clear(self, log_mod):
        lm = log_mod.LogManager()
        lm.log("INFO", "test")
        lm.clear()
        assert len(lm.get_recent(10)) == 0

    def test_export_log(self, log_mod):
        lm = log_mod.LogManager()
        lm.set_level("DEBUG")
        lm.log("INFO", "export test")
        exported = lm.export()
        assert isinstance(exported, str)
        assert "export test" in exported


# ===================================================================
# M381: PluginLoader — 10 tests
# ===================================================================
class TestPluginLoader:
    """Dynamic game integration plugin discovery."""

    def test_init(self, plugin_mod):
        pl = plugin_mod.PluginLoader()
        assert pl is not None

    def test_register_plugin(self, plugin_mod):
        pl = plugin_mod.PluginLoader()
        pl.register("lol_plugin", {"game": "lol", "entry": "lol_agent.main"})
        assert "lol_plugin" in pl.list_plugins()

    def test_unregister_plugin(self, plugin_mod):
        pl = plugin_mod.PluginLoader()
        pl.register("tmp_plugin", {"game": "test"})
        pl.unregister("tmp_plugin")
        assert "tmp_plugin" not in pl.list_plugins()

    def test_get_plugin_info(self, plugin_mod):
        pl = plugin_mod.PluginLoader()
        pl.register("lol_plugin", {"game": "lol", "version": "1.0"})
        info = pl.get_info("lol_plugin")
        assert info["game"] == "lol"

    def test_get_nonexistent_plugin(self, plugin_mod):
        pl = plugin_mod.PluginLoader()
        info = pl.get_info("nonexistent")
        assert info is None

    def test_discover_from_dict(self, plugin_mod):
        pl = plugin_mod.PluginLoader()
        manifest = {
            "plugins": [
                {"name": "p1", "game": "lol"},
                {"name": "p2", "game": "dota2"},
            ]
        }
        pl.discover_from_manifest(manifest)
        assert len(pl.list_plugins()) >= 2

    def test_plugin_enabled_default(self, plugin_mod):
        pl = plugin_mod.PluginLoader()
        pl.register("test_p", {"game": "test"})
        assert pl.is_enabled("test_p") is True

    def test_disable_plugin(self, plugin_mod):
        pl = plugin_mod.PluginLoader()
        pl.register("test_p", {"game": "test"})
        pl.disable("test_p")
        assert pl.is_enabled("test_p") is False

    def test_enable_plugin(self, plugin_mod):
        pl = plugin_mod.PluginLoader()
        pl.register("test_p", {"game": "test"})
        pl.disable("test_p")
        pl.enable("test_p")
        assert pl.is_enabled("test_p") is True

    def test_list_enabled_only(self, plugin_mod):
        pl = plugin_mod.PluginLoader()
        pl.register("p1", {"game": "lol"})
        pl.register("p2", {"game": "dota2"})
        pl.disable("p2")
        enabled = pl.list_enabled()
        assert "p1" in enabled
        assert "p2" not in enabled


# ===================================================================
# M382: CredentialManager — 10 tests
# ===================================================================
class TestCredentialManager:
    """API key/token secure storage."""

    def test_init(self, cred_mod):
        cm = cred_mod.CredentialManager()
        assert cm is not None

    def test_store_and_retrieve(self, cred_mod):
        cm = cred_mod.CredentialManager()
        cm.store("riot_api_key", "RGAPI-12345")
        assert cm.retrieve("riot_api_key") == "RGAPI-12345"

    def test_retrieve_nonexistent(self, cred_mod):
        cm = cred_mod.CredentialManager()
        assert cm.retrieve("nonexistent_key") is None

    def test_delete_credential(self, cred_mod):
        cm = cred_mod.CredentialManager()
        cm.store("temp_key", "value")
        cm.delete("temp_key")
        assert cm.retrieve("temp_key") is None

    def test_list_keys(self, cred_mod):
        cm = cred_mod.CredentialManager()
        cm.store("key1", "v1")
        cm.store("key2", "v2")
        keys = cm.list_keys()
        assert "key1" in keys
        assert "key2" in keys

    def test_values_not_in_list(self, cred_mod):
        """list_keys should not expose values."""
        cm = cred_mod.CredentialManager()
        cm.store("secret", "mysecretvalue")
        keys = cm.list_keys()
        for k in keys:
            assert "mysecretvalue" not in str(k)

    def test_overwrite_credential(self, cred_mod):
        cm = cred_mod.CredentialManager()
        cm.store("key", "old_value")
        cm.store("key", "new_value")
        assert cm.retrieve("key") == "new_value"

    def test_masked_display(self, cred_mod):
        cm = cred_mod.CredentialManager()
        cm.store("api_key", "RGAPI-12345678")
        masked = cm.masked("api_key")
        assert "12345678" not in masked
        assert "***" in masked or "..." in masked

    def test_clear_all(self, cred_mod):
        cm = cred_mod.CredentialManager()
        cm.store("k1", "v1")
        cm.store("k2", "v2")
        cm.clear_all()
        assert len(cm.list_keys()) == 0

    def test_export_does_not_expose_values(self, cred_mod):
        cm = cred_mod.CredentialManager()
        cm.store("secret", "super_secret_value")
        export = cm.export_summary()
        assert "super_secret_value" not in str(export)


# ===================================================================
# M383: HealthMonitor — 10 tests
# ===================================================================
class TestHealthMonitor:
    """System resource + network + API health checks."""

    def test_init(self, health_mod):
        hm = health_mod.HealthMonitor()
        assert hm is not None

    def test_check_returns_dict(self, health_mod):
        hm = health_mod.HealthMonitor()
        result = hm.check()
        assert isinstance(result, dict)

    def test_check_has_cpu(self, health_mod):
        hm = health_mod.HealthMonitor()
        result = hm.check()
        assert "cpu" in result

    def test_check_has_memory(self, health_mod):
        hm = health_mod.HealthMonitor()
        result = hm.check()
        assert "memory" in result

    def test_check_has_overall_status(self, health_mod):
        hm = health_mod.HealthMonitor()
        result = hm.check()
        assert "overall" in result
        assert result["overall"] in ("healthy", "degraded", "unhealthy")

    def test_register_endpoint(self, health_mod):
        hm = health_mod.HealthMonitor()
        hm.register_endpoint("riot_api", "https://127.0.0.1:2999")
        assert "riot_api" in hm.list_endpoints()

    def test_unregister_endpoint(self, health_mod):
        hm = health_mod.HealthMonitor()
        hm.register_endpoint("test_ep", "http://localhost")
        hm.unregister_endpoint("test_ep")
        assert "test_ep" not in hm.list_endpoints()

    def test_get_history(self, health_mod):
        hm = health_mod.HealthMonitor()
        hm.check()
        hm.check()
        hist = hm.get_history()
        assert len(hist) >= 2

    def test_cpu_value_range(self, health_mod):
        hm = health_mod.HealthMonitor()
        result = hm.check()
        assert 0.0 <= result["cpu"] <= 100.0

    def test_memory_value_range(self, health_mod):
        hm = health_mod.HealthMonitor()
        result = hm.check()
        assert 0.0 <= result["memory"] <= 100.0


# ===================================================================
# M384: UpgradeChecker — 10 tests
# ===================================================================
class TestUpgradeChecker:
    """Version comparison + update prompts."""

    def test_init(self, upgrade_mod):
        uc = upgrade_mod.UpgradeChecker(current_version="1.0.0")
        assert uc.current_version == "1.0.0"

    def test_compare_same_version(self, upgrade_mod):
        uc = upgrade_mod.UpgradeChecker(current_version="1.0.0")
        assert uc.compare("1.0.0") == 0

    def test_compare_newer_available(self, upgrade_mod):
        uc = upgrade_mod.UpgradeChecker(current_version="1.0.0")
        assert uc.compare("2.0.0") > 0  # remote is newer

    def test_compare_older_available(self, upgrade_mod):
        uc = upgrade_mod.UpgradeChecker(current_version="2.0.0")
        assert uc.compare("1.0.0") < 0  # remote is older

    def test_needs_update_true(self, upgrade_mod):
        uc = upgrade_mod.UpgradeChecker(current_version="1.0.0")
        assert uc.needs_update("2.0.0") is True

    def test_needs_update_false(self, upgrade_mod):
        uc = upgrade_mod.UpgradeChecker(current_version="2.0.0")
        assert uc.needs_update("1.0.0") is False

    def test_format_update_message(self, upgrade_mod):
        uc = upgrade_mod.UpgradeChecker(current_version="1.0.0")
        msg = uc.format_update_message("2.0.0")
        assert "1.0.0" in msg
        assert "2.0.0" in msg

    def test_parse_version(self, upgrade_mod):
        uc = upgrade_mod.UpgradeChecker(current_version="1.2.3")
        parts = uc.parse_version("1.2.3")
        assert parts == (1, 2, 3)

    def test_parse_version_two_parts(self, upgrade_mod):
        uc = upgrade_mod.UpgradeChecker(current_version="1.0")
        parts = uc.parse_version("1.0")
        assert parts == (1, 0, 0)

    def test_changelog_placeholder(self, upgrade_mod):
        uc = upgrade_mod.UpgradeChecker(current_version="1.0.0")
        changelog = uc.get_changelog("2.0.0")
        assert isinstance(changelog, str)


# ===================================================================
# M385: SessionRecorder — 10 tests
# ===================================================================
class TestSessionRecorder:
    """Full session save/replay/export."""

    def test_init(self, session_mod):
        sr = session_mod.SessionRecorder()
        assert sr is not None

    def test_start_recording(self, session_mod):
        sr = session_mod.SessionRecorder()
        sr.start()
        assert sr.is_recording() is True

    def test_stop_recording(self, session_mod):
        sr = session_mod.SessionRecorder()
        sr.start()
        sr.stop()
        assert sr.is_recording() is False

    def test_record_event(self, session_mod):
        sr = session_mod.SessionRecorder()
        sr.start()
        sr.record_event({"type": "decision", "action": "push_lane", "time": 100})
        assert sr.event_count() == 1

    def test_record_multiple_events(self, session_mod):
        sr = session_mod.SessionRecorder()
        sr.start()
        for i in range(10):
            sr.record_event({"type": "tick", "time": i * 10})
        assert sr.event_count() == 10

    def test_record_event_while_not_recording(self, session_mod):
        sr = session_mod.SessionRecorder()
        sr.record_event({"type": "test"})
        assert sr.event_count() == 0

    def test_get_events(self, session_mod):
        sr = session_mod.SessionRecorder()
        sr.start()
        sr.record_event({"type": "a"})
        sr.record_event({"type": "b"})
        events = sr.get_events()
        assert len(events) == 2

    def test_export_json(self, session_mod):
        sr = session_mod.SessionRecorder()
        sr.start()
        sr.record_event({"type": "test", "value": 42})
        sr.stop()
        exported = sr.export_json()
        data = json.loads(exported)
        assert "events" in data
        assert len(data["events"]) == 1

    def test_clear_events(self, session_mod):
        sr = session_mod.SessionRecorder()
        sr.start()
        sr.record_event({"type": "a"})
        sr.clear()
        assert sr.event_count() == 0

    def test_get_duration(self, session_mod):
        sr = session_mod.SessionRecorder()
        sr.start()
        sr.record_event({"type": "a", "time": 100})
        sr.record_event({"type": "b", "time": 200})
        sr.stop()
        dur = sr.get_duration()
        assert isinstance(dur, float)
        assert dur >= 0.0
