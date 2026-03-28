"""
Data Sanitizer — PII removal and data redaction for captured traffic.

Sanitizes HTTP headers, URLs, and body content before storage or forwarding
to training pipelines. Produces an audit trail of all redaction operations.

Location: extensions/fiddler-bridge/src/fiddler_bridge/sanitizer.py
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class RedactionRecord:
    """Record of a single redaction operation."""
    field_name: str
    rule_name: str
    original_length: int
    replacement: str


@dataclass
class SanitizeConfig:
    """Configuration for the data sanitizer.

    Attributes:
        redact_headers: Header names whose values should be redacted.
        redact_url_params: URL query parameter names to redact.
        redact_body_patterns: Additional regex patterns for body redaction.
    """
    redact_headers: list[str] = field(default_factory=lambda: [
        "Authorization", "Cookie", "Set-Cookie",
        "X-API-Key", "X-Auth-Token", "Proxy-Authorization",
    ])
    redact_url_params: list[str] = field(default_factory=lambda: [
        "api_key", "apikey", "token", "secret", "password", "access_token",
    ])
    redact_body_patterns: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "SanitizeConfig":
        return cls(
            redact_headers=d.get("redact_headers", cls.__dataclass_fields__["redact_headers"].default_factory()),
            redact_url_params=d.get("redact_url_params", cls.__dataclass_fields__["redact_url_params"].default_factory()),
            redact_body_patterns=d.get("redact_body_patterns", []),
        )


# Built-in body patterns for PII
_BUILTIN_BODY_PATTERNS: list[tuple[str, str]] = [
    ("ipv4", r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"),
    ("email", r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"),
    ("ssn", r"\b\d{3}-\d{2}-\d{4}\b"),
]


class DataSanitizer:
    """Production-grade data sanitizer with audit trail.

    Usage:
        sanitizer = DataSanitizer()
        clean_headers = sanitizer.sanitize_headers(raw_headers)
        clean_url = sanitizer.sanitize_url(raw_url)
        clean_body = sanitizer.sanitize_body(raw_body)
        audit = sanitizer.audit_trail
    """

    REDACTED = "[REDACTED]"

    def __init__(self, config: SanitizeConfig | None = None) -> None:
        self._config = config or SanitizeConfig()
        self._audit_trail: list[RedactionRecord] = []
        self._custom_patterns: list[tuple[str, re.Pattern]] = []

        # Compile built-in patterns
        self._body_patterns: list[tuple[str, re.Pattern]] = [
            (name, re.compile(pattern)) for name, pattern in _BUILTIN_BODY_PATTERNS
        ]
        # Add config-driven patterns
        for i, pattern in enumerate(self._config.redact_body_patterns):
            self._body_patterns.append((f"config_pattern_{i}", re.compile(pattern)))

    @property
    def rule_count(self) -> int:
        return len(self._config.redact_headers) + len(self._config.redact_url_params) + len(self._body_patterns) + len(self._custom_patterns)

    @property
    def redacted_header_names(self) -> list[str]:
        return list(self._config.redact_headers)

    @property
    def audit_trail(self) -> list[RedactionRecord]:
        return list(self._audit_trail)

    def register_pattern(self, name: str, pattern: str) -> None:
        """Register a custom regex pattern for body sanitization."""
        self._custom_patterns.append((name, re.compile(pattern)))

    def sanitize_headers(self, headers: dict[str, str]) -> dict[str, str]:
        """Sanitize HTTP headers, redacting sensitive values."""
        result = dict(headers)
        redact_set = {h.lower() for h in self._config.redact_headers}
        for key in list(result.keys()):
            if key.lower() in redact_set:
                self._audit_trail.append(RedactionRecord(
                    field_name=key,
                    rule_name="header_redaction",
                    original_length=len(result[key]),
                    replacement=self.REDACTED,
                ))
                result[key] = self.REDACTED
        return result

    def sanitize_url(self, url: str) -> str:
        """Sanitize URL query parameters."""
        if "?" not in url:
            return url
        base, query = url.split("?", 1)
        params = query.split("&")
        clean_params: list[str] = []
        redact_set = {p.lower() for p in self._config.redact_url_params}
        for param in params:
            if "=" in param:
                key, value = param.split("=", 1)
                if key.lower() in redact_set:
                    self._audit_trail.append(RedactionRecord(
                        field_name=key,
                        rule_name="url_param_redaction",
                        original_length=len(value),
                        replacement=self.REDACTED,
                    ))
                    clean_params.append(f"{key}={self.REDACTED}")
                else:
                    clean_params.append(param)
            else:
                clean_params.append(param)
        return f"{base}?{'&'.join(clean_params)}"

    def sanitize_body(self, body: str) -> str:
        """Sanitize body content, applying all pattern-based redactions."""
        result = body
        all_patterns = self._body_patterns + self._custom_patterns
        for name, pattern in all_patterns:
            matches = pattern.findall(result)
            if matches:
                for match in matches:
                    self._audit_trail.append(RedactionRecord(
                        field_name="body",
                        rule_name=name,
                        original_length=len(match) if isinstance(match, str) else 0,
                        replacement=self.REDACTED,
                    ))
                result = pattern.sub(self.REDACTED, result)
        return result

    def sanitize_binary(self, data: bytes) -> bytes:
        """Binary content passes through unchanged (no text-based redaction)."""
        return data
