"""
Exceptions - Centralized exception hierarchy for the LoL Fiddler Agent.

All custom exceptions inherit from LoLAgentError for clean
try/except handling. Each subsystem has its own exception class
for precise error discrimination.
"""

from __future__ import annotations

from typing import Any, Optional


class LoLAgentError(Exception):
    """Base exception for all LoL Fiddler Agent errors."""

    def __init__(self, message: str = "", details: Optional[dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.details = details or {}


# ── Network Layer ─────────────────────────────────────────────────────────

class NetworkError(LoLAgentError):
    """Base for network-related errors."""
    pass


class FiddlerConnectionError(NetworkError):
    """Cannot connect to Fiddler MCP server."""
    pass


class FiddlerAuthError(NetworkError):
    """Authentication with Fiddler MCP failed."""
    pass


class FiddlerToolError(NetworkError):
    """A Fiddler MCP tool call failed."""
    pass


class LiveClientAPIError(NetworkError):
    """Error communicating with LoL Live Client API."""
    pass


class RiotAPIError(NetworkError):
    """Error from Riot Games developer API."""

    def __init__(
        self, message: str = "", status_code: int = 0,
        retry_after: Optional[int] = None, **kwargs: Any,
    ) -> None:
        super().__init__(message, **kwargs)
        self.status_code = status_code
        self.retry_after = retry_after


class RateLimitError(NetworkError):
    """API rate limit exceeded."""

    def __init__(self, endpoint: str = "", retry_after: float = 0.0) -> None:
        super().__init__(f"Rate limited on {endpoint}, retry after {retry_after}s")
        self.endpoint = endpoint
        self.retry_after = retry_after


# ── Data Layer ────────────────────────────────────────────────────────────

class DataError(LoLAgentError):
    """Base for data processing errors."""
    pass


class ParseError(DataError):
    """Failed to parse game state data."""
    pass


class ValidationError(DataError):
    """Game state data failed validation."""
    pass


class SchemaError(DataError):
    """Unexpected data schema from API."""
    pass


# ── ML Layer ──────────────────────────────────────────────────────────────

class MLError(LoLAgentError):
    """Base for ML-related errors."""
    pass


class ModelNotFoundError(MLError):
    """Prediction model file not found."""
    pass


class ModelLoadError(MLError):
    """Failed to load prediction model."""
    pass


class PredictionError(MLError):
    """Model prediction failed."""
    pass


class FeatureExtractionError(MLError):
    """Feature extraction from game state failed."""
    pass


# ── Strategy Layer ────────────────────────────────────────────────────────

class StrategyError(LoLAgentError):
    """Base for strategy evaluation errors."""
    pass


class EvaluatorError(StrategyError):
    """A strategy evaluator failed."""

    def __init__(self, evaluator_name: str = "", message: str = "") -> None:
        super().__init__(f"Evaluator {evaluator_name}: {message}")
        self.evaluator_name = evaluator_name


# ── Integration Layer ─────────────────────────────────────────────────────

class IntegrationError(LoLAgentError):
    """Base for integration errors."""
    pass


class AgentOSError(IntegrationError):
    """Error in AgentOS integration."""
    pass


class PolicyViolationError(AgentOSError):
    """Agent action violated an AgentOS policy."""

    def __init__(self, policy: str = "", action: str = "", message: str = "") -> None:
        super().__init__(f"Policy '{policy}' violated by action '{action}': {message}")
        self.policy = policy
        self.action = action


# ── Replay Layer ──────────────────────────────────────────────────────────

class ReplayError(LoLAgentError):
    """Base for replay-related errors."""
    pass


class ReplayCorruptedError(ReplayError):
    """Replay file is corrupted or unreadable."""
    pass


class ReplayNotFoundError(ReplayError):
    """Replay file not found."""
    pass


# ── Configuration ─────────────────────────────────────────────────────────

class ConfigError(LoLAgentError):
    """Configuration error."""
    pass


class MissingConfigError(ConfigError):
    """Required configuration value is missing."""

    def __init__(self, key: str = "") -> None:
        super().__init__(f"Missing required config: {key}")
        self.key = key
