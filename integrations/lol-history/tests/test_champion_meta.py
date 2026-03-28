"""
TDD Tests for M264: ChampionMeta — version-aware champion strength detection.

10 tests: construction, add match data, compute tier list, detect meta shifts,
patch-aware filtering, win rate calculation, pick/ban analysis.
"""

import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


class TestChampionMetaConstruction:
    def test_import_and_construct(self):
        from lol_history.champion_meta import ChampionMeta
        meta = ChampionMeta()
        assert meta is not None

    def test_default_patch(self):
        from lol_history.champion_meta import ChampionMeta
        meta = ChampionMeta()
        assert meta.current_patch is not None or meta.current_patch == ""


class TestChampionMetaData:
    def test_add_match_data(self):
        from lol_history.champion_meta import ChampionMeta
        meta = ChampionMeta()
        meta.add_match("Jinx", win=True, patch="14.10")
        meta.add_match("Jinx", win=False, patch="14.10")
        meta.add_match("Caitlyn", win=True, patch="14.10")
        assert meta.match_count >= 3

    def test_champion_win_rate(self):
        from lol_history.champion_meta import ChampionMeta
        meta = ChampionMeta()
        meta.add_match("Jinx", win=True, patch="14.10")
        meta.add_match("Jinx", win=True, patch="14.10")
        meta.add_match("Jinx", win=False, patch="14.10")
        wr = meta.win_rate("Jinx", patch="14.10")
        assert abs(wr - 2.0 / 3.0) < 0.01


class TestChampionMetaTierList:
    def test_tier_list(self):
        from lol_history.champion_meta import ChampionMeta
        meta = ChampionMeta()
        for _ in range(10):
            meta.add_match("Jinx", win=True, patch="14.10")
        for _ in range(10):
            meta.add_match("Yasuo", win=False, patch="14.10")
        tiers = meta.tier_list(patch="14.10")
        assert isinstance(tiers, list)
        assert tiers[0]["champion"] == "Jinx"

    def test_tier_list_minimum_games(self):
        from lol_history.champion_meta import ChampionMeta
        meta = ChampionMeta()
        meta.add_match("RareChamp", win=True, patch="14.10")
        tiers = meta.tier_list(patch="14.10", min_games=5)
        # RareChamp should be filtered out
        champ_names = [t["champion"] for t in tiers]
        assert "RareChamp" not in champ_names


class TestChampionMetaShift:
    def test_detect_meta_shift(self):
        from lol_history.champion_meta import ChampionMeta
        meta = ChampionMeta()
        for _ in range(10):
            meta.add_match("Jinx", win=True, patch="14.9")
        for _ in range(10):
            meta.add_match("Jinx", win=False, patch="14.10")
        shift = meta.detect_shift("Jinx", from_patch="14.9", to_patch="14.10")
        assert shift < 0  # Win rate dropped

    def test_strongest_champions(self):
        from lol_history.champion_meta import ChampionMeta
        meta = ChampionMeta()
        for _ in range(20):
            meta.add_match("Broken", win=True, patch="14.10")
        for _ in range(20):
            meta.add_match("Weak", win=False, patch="14.10")
        strongest = meta.strongest(patch="14.10", top_n=1)
        assert strongest[0] == "Broken"


class TestChampionMetaSerialization:
    def test_to_dict(self):
        from lol_history.champion_meta import ChampionMeta
        meta = ChampionMeta()
        meta.add_match("Jinx", win=True, patch="14.10")
        data = meta.to_dict()
        assert isinstance(data, dict)
        assert "matches" in data or "champions" in data

    def test_evolution_key(self):
        from lol_history.champion_meta import _EVOLUTION_KEY
        assert isinstance(_EVOLUTION_KEY, str)

    def test_pick_rate(self):
        from lol_history.champion_meta import ChampionMeta
        meta = ChampionMeta()
        meta.add_match("Jinx", win=True, patch="14.10")
        meta.add_match("Cait", win=True, patch="14.10")
        meta.add_match("Jinx", win=False, patch="14.10")
        pr = meta.pick_rate("Jinx", patch="14.10")
        assert abs(pr - 2.0 / 3.0) < 0.01
