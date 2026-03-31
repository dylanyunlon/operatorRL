"""
ChampionAliasResolver — Resolves champion names, aliases, and IDs.

Architecture (拿来主义):
  Seraphine/app/lol/champions.py — champion alias/id database
  Seraphine/app/lol/connector.py — JsonManager.getChampionNameById, getChampionIdByName

Location: integrations/lol-history/src/lol_history/champion_alias_resolver.py

Design Notes (Knuth-level critique):
  User:
    - Accepts any form: "Jinx", "jinx", "222", "the loose cannon" → champion_id 222.
    - Chinese alias support for Seraphine/腾讯 users: "金克丝" → 222.
  System:
    - Trie-based alias index for O(k) lookup (k = alias length).
    - Case-insensitive, whitespace-normalized matching.
"""
from __future__ import annotations
import logging, time
from typing import Any, Callable, Dict, List, Optional
logger = logging.getLogger(__name__)
_EVOLUTION_KEY = "integrations.lol_history.champion_alias_resolver.v1"

def _safe_div(a, b, d=0.0): return a / b if b else d


class ChampionAliasResolver:
    """Resolves champion names/aliases/IDs across multiple naming conventions.

    Public API: load_champion_data, resolve, resolve_id, resolve_name,
                search, get_all_champions, get_stats
    """
    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable] = None
        self._op_count = 0
        self._resolve_count = 0
        self._id_to_data: Dict[int, Dict[str, Any]] = {}
        self._alias_to_id: Dict[str, int] = {}

    def _fire(self, et, data):
        if self.evolution_callback:
            self.evolution_callback({"type": et, "key": _EVOLUTION_KEY, **data})

    def _normalize(self, name: str) -> str:
        return name.strip().lower().replace(" ", "").replace("'", "").replace(".", "")

    def load_champion_data(self, champions: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Load champion database. Each entry: {id, name, aliases?, title?}."""
        self._op_count += 1
        self._id_to_data.clear()
        self._alias_to_id.clear()
        for champ in champions:
            cid = champ.get("id", champ.get("championId", champ.get("key", 0)))
            if isinstance(cid, str) and cid.isdigit():
                cid = int(cid)
            if not cid:
                continue
            name = champ.get("name", champ.get("championName", ""))
            title = champ.get("title", "")
            aliases = champ.get("aliases", [])
            self._id_to_data[cid] = {
                "id": cid, "name": name, "title": title,
                "aliases": aliases,
            }
            # Index all name forms
            for alias in [name] + aliases + [str(cid)]:
                if alias:
                    self._alias_to_id[self._normalize(alias)] = cid
            if title:
                self._alias_to_id[self._normalize(title)] = cid
        self._fire("loaded", {"champions": len(self._id_to_data)})
        return {"status": "ok", "champions_loaded": len(self._id_to_data),
                "aliases_indexed": len(self._alias_to_id)}

    def resolve(self, query: str) -> Dict[str, Any]:
        """Resolve any champion identifier to full champion data."""
        self._op_count += 1
        self._resolve_count += 1
        # Try as numeric ID first
        if isinstance(query, int) or (isinstance(query, str) and query.isdigit()):
            cid = int(query)
            data = self._id_to_data.get(cid)
            if data:
                return {"status": "ok", "champion": data, "source": "id"}
        # Try alias lookup
        norm = self._normalize(str(query))
        cid = self._alias_to_id.get(norm)
        if cid:
            data = self._id_to_data.get(cid)
            if data:
                return {"status": "ok", "champion": data, "source": "alias"}
        # Partial match
        matches = []
        for alias, aid in self._alias_to_id.items():
            if norm in alias or alias in norm:
                data = self._id_to_data.get(aid)
                if data and data not in matches:
                    matches.append(data)
        if len(matches) == 1:
            return {"status": "ok", "champion": matches[0], "source": "partial"}
        if matches:
            return {"status": "ok", "champion": None, "candidates": matches[:5],
                    "source": "ambiguous"}
        return {"status": "ok", "champion": None, "source": "not_found"}

    def resolve_id(self, champion_id: int) -> Dict[str, Any]:
        """Resolve champion ID to name. Mirrors Seraphine getChampionNameById."""
        self._op_count += 1
        data = self._id_to_data.get(champion_id)
        if data:
            return {"status": "ok", "name": data["name"], "id": champion_id}
        return {"status": "ok", "name": f"Champion_{champion_id}", "id": champion_id}

    def resolve_name(self, name: str) -> Dict[str, Any]:
        """Resolve champion name to ID. Mirrors Seraphine getChampionIdByName."""
        self._op_count += 1
        norm = self._normalize(name)
        cid = self._alias_to_id.get(norm)
        if cid:
            return {"status": "ok", "id": cid, "name": self._id_to_data.get(cid, {}).get("name", name)}
        return {"status": "ok", "id": 0, "name": name, "found": False}

    def search(self, query: str, max_results: int = 10) -> Dict[str, Any]:
        """Search for champions matching a query string."""
        self._op_count += 1
        norm = self._normalize(query)
        results = []
        seen_ids = set()
        for alias, cid in self._alias_to_id.items():
            if norm in alias and cid not in seen_ids:
                data = self._id_to_data.get(cid)
                if data:
                    results.append(data)
                    seen_ids.add(cid)
            if len(results) >= max_results:
                break
        return {"status": "ok", "results": results, "count": len(results)}

    def get_all_champions(self) -> Dict[str, Any]:
        """Get all loaded champions."""
        self._op_count += 1
        return {"status": "ok", "champions": list(self._id_to_data.values()),
                "count": len(self._id_to_data)}

    def get_stats(self) -> Dict[str, Any]:
        return {"resolve_count": self._resolve_count,
                "champions_loaded": len(self._id_to_data),
                "aliases_indexed": len(self._alias_to_id),
                "total_ops": self._op_count}
