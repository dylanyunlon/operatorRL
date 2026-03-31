"""
MultiAccountLinker — Links multiple accounts by play-style fingerprinting.

Architecture (拿来主义):
  opponent_behavior_modeler.py + opponent_model_persistence.py（M609）

Location: integrations/lol-history/src/lol_history/multi_account_linker.py

Design Notes (Knuth-level critique):
  User:
    - register_account handles duplicate registration — updates existing fingerprint.
    - find_links returns confidence scores so users can set their own threshold.
    - Fingerprint is privacy-preserving: uses behavioral stats, not PII.
  System:
    - evolution_callback fires on every operation for system-level tracking.
    - Cosine similarity for fingerprint comparison — scale-invariant.
    - op_count provides basic throughput monitoring.
"""

from __future__ import annotations

import logging
import math
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "integrations.lol_history.multi_account_linker.v1"

_FINGERPRINT_KEYS = [
    "avg_cs_per_min", "avg_vision_score", "avg_kda",
    "preferred_role_pct", "avg_game_duration", "ward_frequency",
    "aggression_index", "roam_frequency", "objective_priority",
]


def _safe_div(a: float, b: float, default: float = 0.0) -> float:
    return a / b if b != 0 else default


def _cosine_similarity(a: List[float], b: List[float]) -> float:
    """Cosine similarity between two vectors."""
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(x * x for x in b))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


class MultiAccountLinker:
    """Links multiple accounts by play-style fingerprinting.

    Public API
    ----------
    register_account    — register an account with its behavioral fingerprint
    build_fingerprint   — extract fingerprint from match history
    find_links          — find potential linked accounts
    get_account_info    — get stored info for an account
    get_stats           — internal statistics

    Evolution
    ---------
    Set evolution_callback to receive structured events.
    """

    def __init__(self, *, similarity_threshold: float = 0.85) -> None:
        self.evolution_callback: Optional[Callable[[Dict[str, Any]], None]] = None
        self._op_count: int = 0
        self._accounts: Dict[str, Dict[str, Any]] = {}  # account_id -> info
        self._threshold: float = similarity_threshold

    def _fire(self, event_type: str, data: Dict[str, Any]) -> None:
        if self.evolution_callback is not None:
            self.evolution_callback({
                "source": _EVOLUTION_KEY, "type": event_type,
                "timestamp": time.time(), "payload": data,
            })

    # ------------------------------------------------------------------ #

    def build_fingerprint(self, match_history: List[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Extract behavioral fingerprint from match history.

        Parameters
        ----------
        match_history : list of dict
            Each dict is a match record with stats.

        Returns
        -------
        dict  with status, fingerprint (dict of float)
        """
        self._op_count += 1
        if match_history is None or not match_history:
            return {"status": "ok", "op": "build_fingerprint",
                    "fingerprint": {k: 0.0 for k in _FINGERPRINT_KEYS}}

        fp: Dict[str, float] = {}
        n = len(match_history)

        fp["avg_cs_per_min"] = sum(
            _safe_div(m.get("cs", 0), m.get("game_duration", 1) / 60)
            for m in match_history
        ) / n

        fp["avg_vision_score"] = sum(m.get("vision_score", 0) for m in match_history) / n

        fp["avg_kda"] = sum(
            _safe_div(m.get("kills", 0) + m.get("assists", 0), max(m.get("deaths", 1), 1))
            for m in match_history
        ) / n

        fp["preferred_role_pct"] = 0.0
        roles: Dict[str, int] = {}
        for m in match_history:
            r = m.get("role", "unknown")
            roles[r] = roles.get(r, 0) + 1
        if roles:
            fp["preferred_role_pct"] = max(roles.values()) / n

        fp["avg_game_duration"] = sum(m.get("game_duration", 0) for m in match_history) / n
        fp["ward_frequency"] = sum(m.get("wards_placed", 0) for m in match_history) / n
        fp["aggression_index"] = sum(m.get("kills", 0) for m in match_history) / n
        fp["roam_frequency"] = sum(m.get("roam_count", 0) for m in match_history) / n
        fp["objective_priority"] = sum(
            m.get("dragons_taken", 0) + m.get("barons_taken", 0)
            for m in match_history
        ) / n

        for k in fp:
            fp[k] = round(fp[k], 4)

        return {"status": "ok", "op": "build_fingerprint", "fingerprint": fp}

    # ------------------------------------------------------------------ #

    def register_account(self, account_id: str,
                         fingerprint: Dict[str, float] = None,
                         metadata: Dict[str, Any] = None) -> Dict[str, Any]:
        """Register an account with its behavioral fingerprint.

        Parameters
        ----------
        account_id : str
        fingerprint : dict of float
        metadata : dict

        Returns
        -------
        dict
        """
        self._op_count += 1
        if fingerprint is None:
            fingerprint = {}

        self._accounts[account_id] = {
            "fingerprint": fingerprint,
            "metadata": metadata or {},
            "registered_at": time.time(),
        }

        self._fire("register_account", {"account_id": account_id})
        return {"status": "ok", "op": "register_account",
                "account_id": account_id, "total_accounts": len(self._accounts)}

    # ------------------------------------------------------------------ #

    def find_links(self, account_id: str, top_n: int = 5) -> Dict[str, Any]:
        """Find potential linked accounts by fingerprint similarity.

        Parameters
        ----------
        account_id : str
        top_n : int

        Returns
        -------
        dict  with status, links (list of {account_id, similarity})
        """
        self._op_count += 1
        _start = time.time()

        acct = self._accounts.get(account_id)
        if acct is None:
            return {"status": "error", "reason": "unknown account_id"}

        target_fp = acct["fingerprint"]
        target_vec = [target_fp.get(k, 0.0) for k in _FINGERPRINT_KEYS]

        links: List[Dict[str, Any]] = []
        for other_id, other_info in self._accounts.items():
            if other_id == account_id:
                continue
            other_fp = other_info["fingerprint"]
            other_vec = [other_fp.get(k, 0.0) for k in _FINGERPRINT_KEYS]
            sim = _cosine_similarity(target_vec, other_vec)
            if sim >= self._threshold:
                links.append({
                    "account_id": other_id,
                    "similarity": round(sim, 4),
                })

        links.sort(key=lambda x: -x["similarity"])
        links = links[:top_n]

        elapsed = time.time() - _start
        self._fire("find_links_completed", {"elapsed": elapsed, "link_count": len(links)})
        return {"status": "ok", "op": "find_links",
                "account_id": account_id, "links": links}

    # ------------------------------------------------------------------ #

    def get_account_info(self, account_id: str) -> Dict[str, Any]:
        """Get stored info for an account."""
        self._op_count += 1
        acct = self._accounts.get(account_id)
        if acct is None:
            return {"status": "ok", "op": "get_account_info", "found": False}
        return {"status": "ok", "op": "get_account_info",
                "found": True, **acct}

    # ------------------------------------------------------------------ #

    def get_stats(self) -> Dict[str, Any]:
        """Internal statistics."""
        return {
            "op_count": self._op_count,
            "total_accounts": len(self._accounts),
            "similarity_threshold": self._threshold,
        }
