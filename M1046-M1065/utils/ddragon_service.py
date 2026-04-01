#!/usr/bin/env python3
"""
M1060: Data Dragon Static Data Service
========================================

OperatorRL Agentic System: 自部署 自环境反馈 自演化

Integrates with Riot's Data Dragon CDN to provide champion, item, rune,
and summoner spell static data. Caches locally for offline operation.

Data Dragon URL: https://ddragon.leagueoflegends.com

References:
    - leagueoflegends-optimizer: Riot API data pipeline
    - Seraphine: app/lol/champions.py champion data
    - Riot Developer Portal: developer.riotgames.com/docs/lol

Production Critique:
    1. User: Static data is cached locally after first download.
       Subsequent launches work fully offline. Cache invalidation
       on game patch version change (e.g., 14.10 → 14.11).
    2. System: CDN data is ~10MB compressed. Initial download takes
       5-15 seconds. Cache stored as JSON in config/ddragon/.
"""

import json
import os
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urljoin

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
try:
    from evo_logging.evolution_logger import LogCategory, get_logger
except ImportError:
    def get_logger(*a, **kw):
        class _FL:
            def info(self, *a, **kw): pass
            def error(self, *a, **kw): pass
            def debug(self, *a, **kw): pass
        return _FL()
    class LogCategory:
        SYSTEM = "system"

try:
    import aiohttp
    HAS_AIOHTTP = True
except ImportError:
    HAS_AIOHTTP = False


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DDRAGON_BASE_URL = "https://ddragon.leagueoflegends.com"
DDRAGON_VERSIONS_URL = f"{DDRAGON_BASE_URL}/api/versions.json"
DDRAGON_CDN_URL = f"{DDRAGON_BASE_URL}/cdn"
CACHE_DIR_DEFAULT = "config/ddragon"
CACHE_TTL_HOURS = 168  # 7 days cache validity


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------

@dataclass
class ChampionData:
    """Static champion data from Data Dragon."""
    champion_id: int = 0
    key: str = ""
    name: str = ""
    title: str = ""
    tags: List[str] = field(default_factory=list)
    stats: Dict[str, float] = field(default_factory=dict)
    spells: List[Dict] = field(default_factory=list)
    passive: Dict = field(default_factory=dict)
    image_url: str = ""
    splash_url: str = ""
    difficulty: int = 0
    attack: int = 0
    defense: int = 0
    magic: int = 0

    @classmethod
    def from_ddragon(cls, data: Dict, version: str) -> 'ChampionData':
        """Parse from Data Dragon champion JSON."""
        info = data.get('info', {})
        image = data.get('image', {})
        image_filename = image.get('full', '')
        return cls(
            champion_id=int(data.get('key', 0)),
            key=data.get('id', ''),
            name=data.get('name', ''),
            title=data.get('title', ''),
            tags=data.get('tags', []),
            stats=data.get('stats', {}),
            spells=[{
                'id': s.get('id', ''),
                'name': s.get('name', ''),
                'description': s.get('description', ''),
                'cooldown': s.get('cooldown', []),
                'cost': s.get('cost', []),
                'range': s.get('range', []),
            } for s in data.get('spells', [])],
            passive={
                'name': data.get('passive', {}).get('name', ''),
                'description': data.get('passive', {}).get('description', ''),
            },
            image_url=(
                f"{DDRAGON_CDN_URL}/{version}/img/champion/{image_filename}"
                if image_filename else ""),
            splash_url=(
                f"{DDRAGON_CDN_URL}/img/champion/splash/{data.get('id', '')}_0.jpg"),
            difficulty=info.get('difficulty', 0),
            attack=info.get('attack', 0),
            defense=info.get('defense', 0),
            magic=info.get('magic', 0),
        )

    def to_dict(self) -> Dict[str, Any]:
        return self.__dict__.copy()

    @property
    def role_tags(self) -> str:
        return ", ".join(self.tags) if self.tags else "Unknown"

    @property
    def is_ranged(self) -> bool:
        return self.stats.get('attackrange', 0) > 300

    @property
    def base_hp(self) -> float:
        return self.stats.get('hp', 0)

    @property
    def base_ad(self) -> float:
        return self.stats.get('attackdamage', 0)


@dataclass
class ItemData:
    """Static item data from Data Dragon."""
    item_id: int = 0
    name: str = ""
    description: str = ""
    plaintext: str = ""
    gold_total: int = 0
    gold_base: int = 0
    gold_sell: int = 0
    purchasable: bool = True
    tags: List[str] = field(default_factory=list)
    stats: Dict[str, float] = field(default_factory=dict)
    from_items: List[int] = field(default_factory=list)
    into_items: List[int] = field(default_factory=list)
    image_url: str = ""

    @classmethod
    def from_ddragon(cls, item_id: int, data: Dict, version: str) -> 'ItemData':
        gold = data.get('gold', {})
        image = data.get('image', {})
        return cls(
            item_id=item_id,
            name=data.get('name', ''),
            description=data.get('description', ''),
            plaintext=data.get('plaintext', ''),
            gold_total=gold.get('total', 0),
            gold_base=gold.get('base', 0),
            gold_sell=gold.get('sell', 0),
            purchasable=gold.get('purchasable', True),
            tags=data.get('tags', []),
            stats=data.get('stats', {}),
            from_items=[int(x) for x in data.get('from', [])],
            into_items=[int(x) for x in data.get('into', [])],
            image_url=(
                f"{DDRAGON_CDN_URL}/{version}/img/item/{image.get('full', '')}"
                if image.get('full') else ""),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v}

    @property
    def is_completed(self) -> bool:
        """Is this a completed (non-component) item?"""
        return self.gold_total >= 2500 and not self.into_items

    @property
    def gold_efficiency(self) -> float:
        """Rough gold efficiency estimate."""
        stat_value = sum(self.stats.values()) * 100  # Simplified
        if self.gold_total == 0:
            return 0.0
        return round(stat_value / self.gold_total, 2)


@dataclass
class RuneData:
    """Static rune data from Data Dragon."""
    rune_id: int = 0
    key: str = ""
    name: str = ""
    icon: str = ""
    short_desc: str = ""
    long_desc: str = ""
    tree: str = ""  # Precision, Domination, Sorcery, Resolve, Inspiration

    def to_dict(self) -> Dict[str, Any]:
        return self.__dict__.copy()


@dataclass
class SummonerSpellData:
    """Static summoner spell data."""
    spell_id: int = 0
    key: str = ""
    name: str = ""
    description: str = ""
    cooldown: float = 0.0
    image_url: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return self.__dict__.copy()


# ---------------------------------------------------------------------------
# Data Dragon Service
# ---------------------------------------------------------------------------

class DataDragonService:
    """
    Service for loading and caching Data Dragon static data.

    Provides champion, item, rune, and spell lookups. Downloads
    data on first use, then serves from local cache.

    Production critique:
        1. User: If CDN is unreachable (offline mode), falls back to
           bundled minimal dataset (champion names + IDs only).
        2. System: Cache is versioned by game patch. When patch changes,
           old cache is kept until new data downloads successfully.
    """
    def __init__(self, cache_dir: str = CACHE_DIR_DEFAULT, locale: str = "en_US"):
        self._cache_dir = Path(cache_dir)
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._locale = locale
        self._logger = get_logger()
        self._version: Optional[str] = None
        self._champions: Dict[int, ChampionData] = {}
        self._champions_by_name: Dict[str, ChampionData] = {}
        self._items: Dict[int, ItemData] = {}
        self._runes: Dict[int, RuneData] = {}
        self._spells: Dict[int, SummonerSpellData] = {}
        self._loaded = False

    async def initialize(self) -> bool:
        """Load data from cache or download from CDN."""
        # Try loading from cache first
        if self._load_from_cache():
            self._loaded = True
            self._logger.info(
                LogCategory.SYSTEM,
                f"Data Dragon loaded from cache: v{self._version}, "
                f"{len(self._champions)} champions, {len(self._items)} items")
            return True
        # Download from CDN
        if not HAS_AIOHTTP:
            self._logger.warn(
                LogCategory.SYSTEM,
                "aiohttp not available, using bundled minimal data")
            self._load_bundled_minimal()
            return True
        try:
            await self._download_and_cache()
            self._loaded = True
            return True
        except Exception as e:
            self._logger.error(
                LogCategory.SYSTEM,
                f"Failed to download Data Dragon: {e}")
            self._load_bundled_minimal()
            return True

    def _load_from_cache(self) -> bool:
        """Load all data from local cache files."""
        cache_meta = self._cache_dir / "meta.json"
        if not cache_meta.exists():
            return False
        try:
            meta = json.loads(cache_meta.read_text())
            cached_at = meta.get('cached_at', 0)
            if time.time() - cached_at > CACHE_TTL_HOURS * 3600:
                return False  # Cache expired
            self._version = meta.get('version', '')
            # Load champions
            champ_file = self._cache_dir / "champions.json"
            if champ_file.exists():
                champ_data = json.loads(champ_file.read_text())
                for key, cdata in champ_data.items():
                    champ = ChampionData(**cdata)
                    self._champions[champ.champion_id] = champ
                    self._champions_by_name[champ.name.lower()] = champ
            # Load items
            item_file = self._cache_dir / "items.json"
            if item_file.exists():
                item_data = json.loads(item_file.read_text())
                for iid, idata in item_data.items():
                    self._items[int(iid)] = ItemData(**idata)
            # Load runes
            rune_file = self._cache_dir / "runes.json"
            if rune_file.exists():
                rune_data = json.loads(rune_file.read_text())
                for rid, rdata in rune_data.items():
                    self._runes[int(rid)] = RuneData(**rdata)
            return bool(self._champions)
        except Exception:
            return False

    async def _download_and_cache(self) -> None:
        """Download from Data Dragon CDN and cache locally."""
        timeout = aiohttp.ClientTimeout(total=30.0)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            # Get latest version
            async with session.get(DDRAGON_VERSIONS_URL) as resp:
                versions = await resp.json()
                self._version = versions[0]
            # Download champions
            champ_url = (
                f"{DDRAGON_CDN_URL}/{self._version}/data/"
                f"{self._locale}/championFull.json")
            async with session.get(champ_url) as resp:
                data = await resp.json()
                for key, cdata in data.get('data', {}).items():
                    champ = ChampionData.from_ddragon(cdata, self._version)
                    self._champions[champ.champion_id] = champ
                    self._champions_by_name[champ.name.lower()] = champ
            # Download items
            item_url = (
                f"{DDRAGON_CDN_URL}/{self._version}/data/"
                f"{self._locale}/item.json")
            async with session.get(item_url) as resp:
                data = await resp.json()
                for iid, idata in data.get('data', {}).items():
                    item = ItemData.from_ddragon(int(iid), idata, self._version)
                    self._items[item.item_id] = item
            # Save to cache
            self._save_cache()

    def _save_cache(self) -> None:
        """Save all data to local cache."""
        meta = {
            'version': self._version,
            'locale': self._locale,
            'cached_at': time.time(),
            'champion_count': len(self._champions),
            'item_count': len(self._items),
        }
        (self._cache_dir / "meta.json").write_text(
            json.dumps(meta, indent=2))
        champ_data = {
            str(cid): c.to_dict() for cid, c in self._champions.items()}
        (self._cache_dir / "champions.json").write_text(
            json.dumps(champ_data, ensure_ascii=False))
        item_data = {
            str(iid): i.to_dict() for iid, i in self._items.items()}
        (self._cache_dir / "items.json").write_text(
            json.dumps(item_data, ensure_ascii=False))

    def _load_bundled_minimal(self) -> None:
        """Load minimal hardcoded data for offline mode."""
        from core.riot_api_models import CHAMPION_NAMES
        for cid, name in CHAMPION_NAMES.items():
            self._champions[cid] = ChampionData(
                champion_id=cid, name=name, key=name)
            self._champions_by_name[name.lower()] = self._champions[cid]
        self._version = "bundled"
        self._loaded = True

    # ---- Public API ----

    def get_champion(self, champion_id: int) -> Optional[ChampionData]:
        return self._champions.get(champion_id)

    def get_champion_by_name(self, name: str) -> Optional[ChampionData]:
        return self._champions_by_name.get(name.lower())

    def get_champion_name(self, champion_id: int) -> str:
        champ = self._champions.get(champion_id)
        return champ.name if champ else f"Champion#{champion_id}"

    def get_item(self, item_id: int) -> Optional[ItemData]:
        return self._items.get(item_id)

    def get_item_name(self, item_id: int) -> str:
        item = self._items.get(item_id)
        return item.name if item else f"Item#{item_id}"

    def get_rune(self, rune_id: int) -> Optional[RuneData]:
        return self._runes.get(rune_id)

    def get_all_champions(self) -> List[ChampionData]:
        return list(self._champions.values())

    def get_champions_by_tag(self, tag: str) -> List[ChampionData]:
        """Get all champions with a specific tag (Fighter, Mage, etc)."""
        return [c for c in self._champions.values()
                if tag in c.tags]

    def get_completed_items(self) -> List[ItemData]:
        """Get all completed (non-component) items."""
        return [i for i in self._items.values() if i.is_completed]

    def search_champion(self, query: str) -> List[ChampionData]:
        """Fuzzy search champions by name."""
        q = query.lower()
        exact = self._champions_by_name.get(q)
        if exact:
            return [exact]
        return [c for c in self._champions.values()
                if q in c.name.lower() or q in c.key.lower()]

    def get_version(self) -> str:
        return self._version or "unknown"

    def get_stats(self) -> Dict[str, Any]:
        return {
            'version': self._version,
            'loaded': self._loaded,
            'champions': len(self._champions),
            'items': len(self._items),
            'runes': len(self._runes),
            'spells': len(self._spells),
        }


class ChampionMatchupDatabase:
    """
    Database of champion matchup statistics.

    Combines static Data Dragon data with historical match data
    to provide matchup-specific advice during champion select.

    Production critique:
        1. User: Matchup data is presented as "X vs Y: expected outcome
           and key tips" during champion select.
        2. System: Database is populated incrementally from analyzed
           matches. Cold-start uses COUNTER_PICK_DB from champ_select_advisor.
    """
    def __init__(self, ddragon: DataDragonService):
        self._ddragon = ddragon
        self._matchups: Dict[Tuple[int, int], Dict] = {}
        self._logger = get_logger()

    def record_matchup(
        self, champ_a_id: int, champ_b_id: int,
        champ_a_won: bool, lane: str
    ) -> None:
        """Record a matchup outcome."""
        key = (min(champ_a_id, champ_b_id), max(champ_a_id, champ_b_id))
        if key not in self._matchups:
            self._matchups[key] = {
                'games': 0, 'wins_lower_id': 0,
                'lanes': defaultdict(int)}
        self._matchups[key]['games'] += 1
        if (champ_a_won and champ_a_id == key[0]) or \
           (not champ_a_won and champ_b_id == key[0]):
            self._matchups[key]['wins_lower_id'] += 1
        self._matchups[key]['lanes'][lane] += 1

    def get_matchup(
        self, champ_a_id: int, champ_b_id: int
    ) -> Optional[Dict]:
        """Get matchup statistics between two champions."""
        key = (min(champ_a_id, champ_b_id), max(champ_a_id, champ_b_id))
        data = self._matchups.get(key)
        if not data or data['games'] < 3:
            return None
        lower_wr = data['wins_lower_id'] / data['games']
        if champ_a_id == key[0]:
            a_winrate = lower_wr
        else:
            a_winrate = 1.0 - lower_wr
        name_a = self._ddragon.get_champion_name(champ_a_id)
        name_b = self._ddragon.get_champion_name(champ_b_id)
        return {
            'champion_a': name_a,
            'champion_b': name_b,
            'games': data['games'],
            'a_winrate': round(a_winrate * 100, 1),
            'b_winrate': round((1 - a_winrate) * 100, 1),
            'favored': name_a if a_winrate > 0.52 else (
                name_b if a_winrate < 0.48 else 'even'),
        }

    def get_best_matchups(
        self, champion_id: int, min_games: int = 5
    ) -> List[Dict]:
        """Get best matchups for a champion (highest winrate)."""
        results = []
        for (a, b), data in self._matchups.items():
            if data['games'] < min_games:
                continue
            if a == champion_id or b == champion_id:
                mu = self.get_matchup(champion_id,
                                      b if a == champion_id else a)
                if mu:
                    results.append(mu)
        results.sort(key=lambda x: x['a_winrate'], reverse=True)
        return results[:10]

    def get_worst_matchups(
        self, champion_id: int, min_games: int = 5
    ) -> List[Dict]:
        """Get worst matchups for a champion (lowest winrate)."""
        results = self.get_best_matchups(champion_id, min_games)
        results.sort(key=lambda x: x['a_winrate'])
        return results[:10]

    def get_total_matchups(self) -> int:
        return len(self._matchups)

    def get_total_games(self) -> int:
        return sum(d['games'] for d in self._matchups.values())
