"""
TDD Tests for M263: RankedTracker — rank/LP change trend tracking.

10 tests: construction, record rank, compute trend, streak detection,
LP delta, tier transitions, serialization.
"""

import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


class TestRankedTrackerConstruction:
    def test_import_and_construct(self):
        from lol_history.ranked_tracker import RankedTracker
        tracker = RankedTracker()
        assert tracker is not None

    def test_empty_history(self):
        from lol_history.ranked_tracker import RankedTracker
        tracker = RankedTracker()
        assert tracker.history_count == 0


class TestRankedTrackerRecord:
    def test_record_rank(self):
        from lol_history.ranked_tracker import RankedTracker
        tracker = RankedTracker()
        tracker.record("Gold", division=2, lp=75)
        assert tracker.history_count == 1

    def test_record_multiple(self):
        from lol_history.ranked_tracker import RankedTracker
        tracker = RankedTracker()
        tracker.record("Gold", division=2, lp=75)
        tracker.record("Gold", division=2, lp=90)
        tracker.record("Gold", division=1, lp=10)
        assert tracker.history_count == 3


class TestRankedTrackerTrend:
    def test_lp_trend_positive(self):
        from lol_history.ranked_tracker import RankedTracker
        tracker = RankedTracker()
        tracker.record("Gold", 2, 50)
        tracker.record("Gold", 2, 75)
        tracker.record("Gold", 2, 90)
        assert tracker.lp_trend() > 0

    def test_lp_trend_negative(self):
        from lol_history.ranked_tracker import RankedTracker
        tracker = RankedTracker()
        tracker.record("Gold", 2, 90)
        tracker.record("Gold", 2, 60)
        tracker.record("Gold", 2, 30)
        assert tracker.lp_trend() < 0

    def test_win_streak(self):
        from lol_history.ranked_tracker import RankedTracker
        tracker = RankedTracker()
        tracker.record("Gold", 2, 50, win=True)
        tracker.record("Gold", 2, 70, win=True)
        tracker.record("Gold", 2, 90, win=True)
        assert tracker.current_streak() == 3

    def test_lose_streak(self):
        from lol_history.ranked_tracker import RankedTracker
        tracker = RankedTracker()
        tracker.record("Gold", 2, 90, win=False)
        tracker.record("Gold", 2, 70, win=False)
        assert tracker.current_streak() == -2


class TestRankedTrackerSerialization:
    def test_to_dict(self):
        from lol_history.ranked_tracker import RankedTracker
        tracker = RankedTracker()
        tracker.record("Gold", 2, 50)
        data = tracker.to_dict()
        assert isinstance(data, dict)
        assert "records" in data

    def test_from_dict(self):
        from lol_history.ranked_tracker import RankedTracker
        tracker = RankedTracker()
        data = {"records": [{"tier": "Gold", "division": 2, "lp": 50}]}
        tracker.from_dict(data)
        assert tracker.history_count == 1

    def test_evolution_key(self):
        from lol_history.ranked_tracker import _EVOLUTION_KEY
        assert isinstance(_EVOLUTION_KEY, str)
