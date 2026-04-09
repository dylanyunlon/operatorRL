"""
Environment — Runtime environment detection and configuration.
================================================================

Apollo reference: ``cyber/common/environment.h``

Detects the runtime environment (development, testing, production)
and provides paths for log directories, data directories, etc.

Claude27: New file.
Location: lolbot-HyperAI/cyber/common/environment.py
"""

from __future__ import annotations

import os
import logging
from enum import Enum, auto
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class RunMode(Enum):
    """Runtime mode.

    Apollo equivalent: implicit in various flag checks.
    """
    DEVELOPMENT = auto()
    TESTING = auto()
    PRODUCTION = auto()
    REPLAY = auto()
    MOCK = auto()


class Environment:
    """Runtime environment detection and path resolution.

    Apollo equivalent: ``cyber::common::GetEnv()`` and related helpers.

    Usage::

        env = Environment()
        env.detect()
        log_dir = env.log_dir
        mode = env.run_mode
    """

    def __init__(self) -> None:
        self._run_mode: RunMode = RunMode.DEVELOPMENT
        self._project_root: Path = Path(__file__).resolve().parent.parent.parent
        self._log_dir: Path = self._project_root / "logs"
        self._data_dir: Path = self._project_root / "data"
        self._config_dir: Path = self._project_root / "configs"
        self._detected = False

    @property
    def run_mode(self) -> RunMode:
        return self._run_mode

    @property
    def project_root(self) -> Path:
        return self._project_root

    @property
    def log_dir(self) -> Path:
        return self._log_dir

    @property
    def data_dir(self) -> Path:
        return self._data_dir

    @property
    def config_dir(self) -> Path:
        return self._config_dir

    @property
    def is_production(self) -> bool:
        return self._run_mode == RunMode.PRODUCTION

    @property
    def is_testing(self) -> bool:
        return self._run_mode == RunMode.TESTING

    @property
    def is_replay(self) -> bool:
        return self._run_mode == RunMode.REPLAY

    @property
    def is_mock(self) -> bool:
        return self._run_mode == RunMode.MOCK

    def detect(self) -> RunMode:
        """Auto-detect runtime environment from env vars and state.

        Apollo pattern: checks CYBER_PATH, module flags, etc.
        We check LOLBOT_* env vars set by run.py CLI.
        """
        # Check data source override (from run.py --mock / --replay)
        data_source = os.environ.get("LOLBOT_CANBUS__DATA_SOURCE", "")
        if data_source == "mock":
            self._run_mode = RunMode.MOCK
        elif data_source == "replay":
            self._run_mode = RunMode.REPLAY
        elif os.environ.get("LOLBOT_TESTING", ""):
            self._run_mode = RunMode.TESTING
        elif os.environ.get("LOLBOT_PRODUCTION", ""):
            self._run_mode = RunMode.PRODUCTION
        else:
            self._run_mode = RunMode.DEVELOPMENT

        # Override directories from env
        log_dir_env = os.environ.get("LOLBOT_LOG_DIR", "")
        if log_dir_env:
            self._log_dir = Path(log_dir_env)

        data_dir_env = os.environ.get("LOLBOT_DATA_DIR", "")
        if data_dir_env:
            self._data_dir = Path(data_dir_env)

        # Ensure directories exist
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._data_dir.mkdir(parents=True, exist_ok=True)

        self._detected = True
        logger.info(
            "Environment detected: mode=%s, root=%s",
            self._run_mode.name, self._project_root,
        )
        return self._run_mode

    def snapshot(self) -> dict:
        return {
            "run_mode": self._run_mode.name,
            "project_root": str(self._project_root),
            "log_dir": str(self._log_dir),
            "data_dir": str(self._data_dir),
            "config_dir": str(self._config_dir),
            "detected": self._detected,
        }

    def __repr__(self) -> str:
        return f"<Environment mode={self._run_mode.name}>"
