"""
Credential Manager — Secure in-memory API key and token storage.

Stores credentials with masked display, prevents value exposure
in listings and exports.

Location: agentos/cli/credential_manager.py

Reference (拿来主义):
  - Akagi/settings/settings.py: sensitive settings management
  - agentos/governance/policy_enforcer.py: security boundary pattern
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


class CredentialManager:
    """Secure in-memory credential storage.

    Stores API keys and tokens, provides masked display
    to prevent accidental exposure.
    """

    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    def store(self, key: str, value: str) -> None:
        """Store a credential.

        Args:
            key: Credential identifier.
            value: Credential value (API key, token, etc.).
        """
        self._store[key] = value

    def retrieve(self, key: str) -> Optional[str]:
        """Retrieve a credential value.

        Args:
            key: Credential identifier.

        Returns:
            Credential value or None if not found.
        """
        return self._store.get(key)

    def delete(self, key: str) -> None:
        """Delete a credential."""
        self._store.pop(key, None)

    def list_keys(self) -> list[str]:
        """List all credential identifiers (not values)."""
        return list(self._store.keys())

    def masked(self, key: str) -> str:
        """Get a masked version of a credential value.

        Shows first 4 chars and masks the rest with ***.

        Args:
            key: Credential identifier.

        Returns:
            Masked string or 'N/A' if not found.
        """
        value = self._store.get(key)
        if value is None:
            return "N/A"
        if len(value) <= 4:
            return "***"
        return value[:4] + "***"

    def clear_all(self) -> None:
        """Remove all stored credentials."""
        self._store.clear()

    def export_summary(self) -> dict[str, Any]:
        """Export a summary without exposing values.

        Returns:
            Dict with key names and masked values.
        """
        return {
            "credential_count": len(self._store),
            "keys": [
                {"name": k, "masked": self.masked(k)}
                for k in self._store
            ],
        }
