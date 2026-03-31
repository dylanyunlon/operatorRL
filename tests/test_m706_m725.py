"""
TDD Tests for M706-M725: Historical Battle Intelligence Live Fusion.

Each module gets 10 tests. Tests are designed so ~50% will fail on first run
if implementation has subtle bugs. All test real logic, no mocks.
"""
import sys, os, time, pytest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "integrations", "lol-history", "src"))


# ── M706: OpponentHistoryAggregator ──
class TestOpponentHistoryAggregator:
    def _make(self):
        from lol_history.opponent_history_aggregator import OpponentHistoryAggregator
        return OpponentHistoryAggregator(cache_ttl_seconds=2.0)

    def test_register_source(self):
        a = self._make()
        r = a.register_source("s1", lambda p, c: [])
        assert r["status"] == "ok" and r["total_sources"] == 1

    def test_aggregate_empty(self):
        a = self._make()
        r = a.aggregate("p1")
        assert r["total_unique"] == 0 and r["status"] == "ok"

    def test_aggregate_single_source(self):
        a = self._make()
        a.register_source("riot", lambda p, c: [{"match_id": "m1", "win": True}])
        r = a.aggregate("p1")
        assert r["total_unique"] == 1 and r["source_breakdown"]["riot"] == 1

    def test_deduplication(self):
        a = self._make()
        a.register_source("s1", lambda p, c: [{"match_id": "m1"}, {"match_id": "m2"}])
        a.register_source("s2", lambda p, c: [{"match_id": "m1"}, {"match_id": "m3"}])
        r = a.aggregate("p1")
        assert r["total_unique"] == 3 and r["duplicates_removed"] == 1

    def test_cache_hit(self):
        a = self._make()
        call_count = [0]
        def fetcher(p, c):
            call_count[0] += 1
            return [{"match_id": "m1"}]
        a.register_source("s1", fetcher)
        a.aggregate("p1")
        r2 = a.aggregate("p1")
        assert r2.get("from_cache") is True
        assert call_count[0] == 1  # only called once

    def test_cache_expiry(self):
        a = self._make()  # 2s TTL
        call_count = [0]
        def fetcher(p, c):
            call_count[0] += 1
            return [{"match_id": "m1"}]
        a.register_source("s1", fetcher)
        a.aggregate("p1")
        time.sleep(2.1)
        a.aggregate("p1")
        assert call_count[0] == 2

    def test_source_error_isolation(self):
        a = self._make()
        a.register_source("good", lambda p, c: [{"match_id": "m1"}])
        a.register_source("bad", lambda p, c: (_ for _ in ()).throw(RuntimeError("fail")))
        r = a.aggregate("p1")
        assert r["total_unique"] == 1 and "bad" in r["errors"]

    def test_invalidate_specific(self):
        a = self._make()
        a.register_source("s1", lambda p, c: [{"match_id": "m1"}])
        a.aggregate("p1")
        a.invalidate("p1")
        assert a.get_cached("p1") is None

    def test_invalidate_all(self):
        a = self._make()
        a.register_source("s1", lambda p, c: [])
        a.aggregate("p1")
        a.aggregate("p2")
        r = a.invalidate()
        assert r["removed"] == 2

    def test_stats(self):
        a = self._make()
        a.register_source("s1", lambda p, c: [])
        a.aggregate("p1")
        s = a.get_stats()
        assert s["aggregations"] == 1 and s["sources"] == 1


# ── M707: LiveOpponentScout ──
class TestLiveOpponentScout:
    def _make(self):
        from lol_history.live_opponent_scout import LiveOpponentScout
        return LiveOpponentScout()

    def test_scout_no_fetcher(self):
        s = self._make()
        r = s.scout_single("p1")
        assert r["matches_analyzed"] == 0

    def test_scout_single_with_data(self):
        s = self._make()
        s.set_history_fetcher(lambda p, c: [
            {"win": True, "kills": 5, "deaths": 2, "assists": 8, "championId": 1,
             "totalMinionsKilled": 180, "visionScore": 20} for _ in range(10)
        ])
        r = s.scout_single("p1", "Player1", champion_id=1)
        assert r["win_rate"] == 1.0 and r["threat_score"] > 0

    def test_scout_batch(self):
        s = self._make()
        s.set_history_fetcher(lambda p, c: [{"win": True, "kills": 3, "deaths": 3, "assists": 5,
                                              "championId": 1, "totalMinionsKilled": 150, "visionScore": 18}])
        r = s.scout([{"puuid": "p1", "summoner_name": "A"}, {"puuid": "p2", "summoner_name": "B"}])
        assert r["opponents_scouted"] == 2

    def test_threat_ranking(self):
        s = self._make()
        s.set_history_fetcher(lambda p, c: [
            {"win": p == "p1", "kills": 10 if p == "p1" else 1, "deaths": 1,
             "assists": 5, "championId": 1, "totalMinionsKilled": 200, "visionScore": 30}
        ])
        s.scout([{"puuid": "p1", "summoner_name": "A"}, {"puuid": "p2", "summoner_name": "B"}])
        ranking = s.get_threat_ranking()
        assert len(ranking) == 2
        assert ranking[0]["threat_score"] >= ranking[1]["threat_score"]

    def test_weakness_detection_high_deaths(self):
        s = self._make()
        s.set_history_fetcher(lambda p, c: [
            {"win": False, "kills": 1, "deaths": 10, "assists": 2, "championId": 1,
             "totalMinionsKilled": 80, "visionScore": 5} for _ in range(5)
        ])
        r = s.scout_single("p1")
        assert "high_death_rate" in r["weaknesses"]

    def test_weakness_low_cs(self):
        s = self._make()
        s.set_history_fetcher(lambda p, c: [
            {"win": False, "kills": 2, "deaths": 3, "assists": 4, "championId": 1,
             "totalMinionsKilled": 80, "visionScore": 20} for _ in range(5)
        ])
        r = s.scout_single("p1")
        assert "low_cs" in r["weaknesses"]

    def test_weakness_poor_vision(self):
        s = self._make()
        s.set_history_fetcher(lambda p, c: [
            {"win": True, "kills": 5, "deaths": 2, "assists": 5, "championId": 1,
             "totalMinionsKilled": 200, "visionScore": 5} for _ in range(5)
        ])
        r = s.scout_single("p1")
        assert "poor_vision" in r["weaknesses"]

    def test_losing_streak_detection(self):
        s = self._make()
        s.set_history_fetcher(lambda p, c: [
            {"win": False, "kills": 2, "deaths": 5, "assists": 3, "championId": 1,
             "totalMinionsKilled": 150, "visionScore": 15} for _ in range(5)
        ])
        r = s.scout_single("p1")
        assert "on_losing_streak" in r["weaknesses"]

    def test_custom_threat_weights(self):
        s = self._make()
        s.set_threat_weights({"win_rate": 1.0, "kda": 0.0, "recent_form": 0.0, "champion_mastery": 0.0})
        s.set_history_fetcher(lambda p, c: [{"win": True, "kills": 0, "deaths": 0, "assists": 0,
                                              "championId": 1, "totalMinionsKilled": 0, "visionScore": 0}])
        r = s.scout_single("p1")
        assert r["threat_score"] > 0

    def test_get_last_report(self):
        s = self._make()
        assert s.get_last_report() is None
        s.set_history_fetcher(lambda p, c: [])
        s.scout([{"puuid": "p1"}])
        assert s.get_last_report() is not None


# ── M708: HistoricalMatchupWinPredictor ──
class TestHistoricalMatchupWinPredictor:
    def _make(self):
        from lol_history.historical_matchup_win_predictor import HistoricalMatchupWinPredictor
        return HistoricalMatchupWinPredictor()

    def test_no_data_returns_prior(self):
        p = self._make()
        r = p.predict_lane(1, 2)
        assert r["win_probability"] == 0.5

    def test_add_matchup_data(self):
        p = self._make()
        r = p.add_matchup_data(1, 2, wins_a=7, total=10, role="mid")
        assert r["status"] == "ok"

    def test_predict_lane_with_data(self):
        p = self._make()
        p.add_matchup_data(1, 2, 8, 10, "mid")
        r = p.predict_lane(1, 2, "mid")
        assert r["win_probability"] > 0.5

    def test_bayesian_shrinkage(self):
        p = self._make()
        p.add_matchup_data(1, 2, 3, 3, "mid")  # 100% but only 3 games
        r = p.predict_lane(1, 2, "mid")
        assert r["win_probability"] < 0.95  # shrunk toward prior

    def test_confidence_scales_with_samples(self):
        p = self._make()
        p.add_matchup_data(1, 2, 5, 10, "mid")
        r1 = p.predict_lane(1, 2, "mid")
        p2 = self._make()
        p2.add_matchup_data(1, 2, 15, 30, "mid")
        r2 = p2.predict_lane(1, 2, "mid")
        assert r2["confidence"] > r1["confidence"]

    def test_predict_team(self):
        p = self._make()
        p.add_matchup_data(1, 6, 7, 10, "top")
        our = [{"champion_id": 1, "role": "top", "recent_win_rate": 0.6}]
        enemy = [{"champion_id": 6, "role": "top"}]
        r = p.predict(our, enemy)
        assert 0 < r["win_probability"] < 1

    def test_predict_team_confidence_interval(self):
        p = self._make()
        our = [{"champion_id": 1, "role": "mid", "recent_win_rate": 0.5}]
        enemy = [{"champion_id": 2, "role": "mid"}]
        r = p.predict(our, enemy)
        ci = r["confidence_interval"]
        assert ci[0] <= r["win_probability"] <= ci[1]

    def test_accumulate_matchup_data(self):
        p = self._make()
        p.add_matchup_data(1, 2, 5, 10, "mid")
        p.add_matchup_data(1, 2, 3, 5, "mid")  # adds to existing
        r = p.predict_lane(1, 2, "mid")
        assert r["sample_size"] == 15

    def test_fallback_to_any_role(self):
        p = self._make()
        p.add_matchup_data(1, 2, 6, 10, "any")
        r = p.predict_lane(1, 2, "top")  # no "top" data, falls to "any"
        assert r["sample_size"] == 10

    def test_stats(self):
        p = self._make()
        p.predict_lane(1, 2)
        s = p.get_stats()
        assert s["op_count"] > 0


# ── M714: OpponentTiltDetector ──
class TestOpponentTiltDetector:
    def _make(self):
        from lol_history.opponent_tilt_detector import OpponentTiltDetector
        return OpponentTiltDetector()

    def test_no_data(self):
        d = self._make()
        r = d.detect("p1", [])
        assert r["tilt_probability"] == 0.0

    def test_stable_player(self):
        d = self._make()
        matches = [{"win": True, "kills": 5, "deaths": 3, "championId": 1, "gameDuration": 1800}
                    for _ in range(10)]
        r = d.detect("p1", matches)
        assert r["tilt_state"] == "stable"

    def test_losing_streak_triggers_tilt(self):
        d = self._make()
        matches = [{"win": False, "kills": 2, "deaths": 6, "championId": i, "gameDuration": 1800}
                    for i in range(6)]
        r = d.detect("p1", matches)
        assert r["tilt_probability"] > 0.2

    def test_champion_hopping(self):
        d = self._make()
        matches = [{"win": False, "kills": 3, "deaths": 4, "championId": i, "gameDuration": 1800}
                    for i in range(5)]
        r = d.detect("p1", matches)
        factors = [f["factor"] for f in r["factors"]]
        assert "champion_hopping" in factors

    def test_rage_deaths(self):
        d = self._make()
        matches = [{"win": False, "kills": 1, "deaths": 12, "championId": 1, "gameDuration": 1500}
                    for _ in range(5)]
        r = d.detect("p1", matches)
        factors = [f["factor"] for f in r["factors"]]
        assert "rage_deaths" in factors

    def test_short_games(self):
        d = self._make()
        matches = [{"win": False, "kills": 1, "deaths": 5, "championId": 1, "gameDuration": 600}
                    for _ in range(5)]
        r = d.detect("p1", matches)
        factors = [f["factor"] for f in r["factors"]]
        assert "short_games" in factors

    def test_detect_batch(self):
        d = self._make()
        opps = [
            {"puuid": "p1", "summoner_name": "A",
             "recent_matches": [{"win": False, "kills": 1, "deaths": 10, "championId": 1, "gameDuration": 1800}] * 5},
            {"puuid": "p2", "summoner_name": "B",
             "recent_matches": [{"win": True, "kills": 5, "deaths": 2, "championId": 1, "gameDuration": 1800}] * 5},
        ]
        r = d.detect_batch(opps)
        assert r["most_tilted"]["puuid"] == "p1"

    def test_recommendation_exploit(self):
        d = self._make()
        matches = [{"win": False, "kills": 0, "deaths": 12, "championId": i, "gameDuration": 700}
                    for i in range(6)]
        r = d.detect("p1", matches)
        assert r["recommendation"] in ("exploit_aggression", "apply_pressure")

    def test_tilt_cap_at_1(self):
        d = self._make()
        matches = [{"win": False, "kills": 0, "deaths": 15, "championId": i, "gameDuration": 500}
                    for i in range(10)]
        r = d.detect("p1", matches)
        assert r["tilt_probability"] <= 1.0

    def test_custom_thresholds(self):
        d = self._make()
        d.set_thresholds({"losing_streak_min": 10})
        matches = [{"win": False, "kills": 3, "deaths": 4, "championId": 1, "gameDuration": 1800}
                    for _ in range(5)]
        r = d.detect("p1", matches)
        # Losing streak of 5 shouldn't trigger with threshold 10
        factors = [f["factor"] for f in r["factors"]]
        assert "losing_streak" not in factors


# ── M717: HistoryConfidenceCalibrator ──
class TestHistoryConfidenceCalibrator:
    def _make(self):
        from lol_history.history_confidence_calibrator import HistoryConfidenceCalibrator
        return HistoryConfidenceCalibrator()

    def test_perfect_data(self):
        c = self._make()
        c.set_current_patch("14.10")
        r = c.calibrate(0.8, sample_size=100, data_age_days=1, data_patch="14.10")
        assert r["calibrated_confidence"] > 0.5

    def test_zero_samples_kills_confidence(self):
        c = self._make()
        r = c.calibrate(0.9, sample_size=0)
        assert r["calibrated_confidence"] == 0.0

    def test_old_data_reduces_confidence(self):
        c = self._make()
        r_fresh = c.calibrate(0.8, sample_size=50, data_age_days=1)
        r_old = c.calibrate(0.8, sample_size=50, data_age_days=90)
        assert r_fresh["calibrated_confidence"] > r_old["calibrated_confidence"]

    def test_wrong_patch_reduces_confidence(self):
        c = self._make()
        c.set_current_patch("14.10")
        r_same = c.calibrate(0.8, sample_size=50, data_patch="14.10")
        r_old = c.calibrate(0.8, sample_size=50, data_patch="13.1")
        assert r_same["calibrated_confidence"] > r_old["calibrated_confidence"]

    def test_source_reliability(self):
        c = self._make()
        c.register_source_reliability("riot_api", 1.0)
        c.register_source_reliability("scraper", 0.5)
        r_good = c.calibrate(0.8, sample_size=50, source="riot_api")
        r_bad = c.calibrate(0.8, sample_size=50, source="scraper")
        assert r_good["calibrated_confidence"] > r_bad["calibrated_confidence"]

    def test_confidence_tiers(self):
        c = self._make()
        r = c.calibrate(0.95, sample_size=100, data_age_days=1)
        assert r["confidence_tier"] in ("high", "medium", "low", "very_low")

    def test_extra_factors(self):
        c = self._make()
        r1 = c.calibrate(0.8, sample_size=50)
        r2 = c.calibrate(0.8, sample_size=50, extra_factors={"team_variance": 0.5})
        assert r2["calibrated_confidence"] < r1["calibrated_confidence"]

    def test_calibrated_never_exceeds_1(self):
        c = self._make()
        r = c.calibrate(1.0, sample_size=200, data_age_days=0)
        assert r["calibrated_confidence"] <= 1.0

    def test_factors_in_result(self):
        c = self._make()
        r = c.calibrate(0.7, sample_size=10, data_age_days=5, data_patch="14.10", source="s1")
        assert "sample_size" in r["factors"]
        assert "recency" in r["factors"]

    def test_stats(self):
        c = self._make()
        c.calibrate(0.5, sample_size=5)
        s = c.get_stats()
        assert s["calibrate_count"] == 1


# ── M724: HistoricalIntelCacheManager ──
class TestHistoricalIntelCacheManager:
    def _make(self):
        from lol_history.historical_intel_cache_manager import HistoricalIntelCacheManager
        return HistoricalIntelCacheManager(max_entries=10, default_ttl_s=2.0)

    def test_set_and_get(self):
        c = self._make()
        c.set("ns1", "k1", {"data": 42})
        assert c.get("ns1", "k1") == {"data": 42}

    def test_get_miss(self):
        c = self._make()
        assert c.get("ns1", "nonexistent") is None

    def test_ttl_expiry(self):
        c = self._make()
        c.set("ns1", "k1", "value", ttl_s=1.0)
        time.sleep(1.1)
        assert c.get("ns1", "k1") is None

    def test_lru_eviction(self):
        c = self._make()  # max 10
        for i in range(15):
            c.set("ns", f"k{i}", f"v{i}")
        assert c.get("ns", "k0") is None  # evicted
        assert c.get("ns", "k14") == "v14"  # still there

    def test_invalidate_specific(self):
        c = self._make()
        c.set("ns", "k1", "v1")
        c.invalidate("ns", "k1")
        assert c.get("ns", "k1") is None

    def test_invalidate_namespace(self):
        c = self._make()
        c.set("ns1", "k1", "v1")
        c.set("ns1", "k2", "v2")
        c.set("ns2", "k1", "v3")
        r = c.invalidate_namespace("ns1")
        assert r["removed"] == 2
        assert c.get("ns2", "k1") == "v3"

    def test_warm(self):
        c = self._make()
        c.warm({"ns1:k1": {"value": "v1"}, "ns1:k2": {"value": "v2", "ttl_s": 5}})
        assert c.get("ns1", "k1") == "v1"
        assert c.get("ns1", "k2") == "v2"

    def test_clear(self):
        c = self._make()
        c.set("ns", "k1", "v1")
        c.set("ns", "k2", "v2")
        r = c.clear()
        assert r["cleared"] == 2
        assert c.get("ns", "k1") is None

    def test_hit_miss_stats(self):
        c = self._make()
        c.set("ns", "k1", "v1")
        c.get("ns", "k1")  # hit
        c.get("ns", "k2")  # miss
        s = c.get_stats()
        assert s["hits"] == 1 and s["misses"] == 1

    def test_namespace_breakdown(self):
        c = self._make()
        c.set("ns1", "k1", "v1")
        c.set("ns2", "k1", "v2")
        s = c.get_stats()
        assert "ns1" in s["namespace_breakdown"]


# ── M725: HistoryToLiveFusionOrchestrator ──
class TestHistoryToLiveFusionOrchestrator:
    def _make(self):
        from lol_history.history_to_live_fusion_orchestrator import HistoryToLiveFusionOrchestrator
        return HistoryToLiveFusionOrchestrator()

    def test_register_module(self):
        o = self._make()
        r = o.register_module("test_mod", object())
        assert r["status"] == "ok" and r["total_modules"] == 1

    def test_initialize(self):
        o = self._make()
        r = o.initialize()
        assert r["state"] == "initialized"

    def test_run_pregame_empty(self):
        o = self._make()
        o.initialize()
        r = o.run_pregame({"our_team": [], "enemy_team": []})
        assert r["status"] == "ok" and r["phase"] == "pregame"

    def test_run_live_update_empty(self):
        o = self._make()
        o.initialize()
        r = o.run_live_update({"event_type": "test", "game_time": 100})
        assert r["status"] == "ok" and r["phase"] == "live"

    def test_module_error_isolation(self):
        o = self._make()
        class BadModule:
            def scout(self, *a, **k): raise RuntimeError("boom")
        o.register_module("opponent_scout", BadModule())
        o.initialize()
        r = o.run_pregame({"our_team": [], "enemy_team": [{"puuid": "p1"}]})
        assert r["status"] == "ok"  # doesn't crash

    def test_dashboard(self):
        o = self._make()
        o.initialize()
        o.run_pregame({"our_team": [], "enemy_team": []})
        d = o.get_dashboard()
        assert d["pregame_runs"] == 1

    def test_shutdown(self):
        o = self._make()
        o.initialize()
        r = o.shutdown()
        assert r["state"] == "shutdown"

    def test_stats(self):
        o = self._make()
        s = o.get_stats()
        assert s["state"] == "uninitialized"

    def test_pregame_with_scout(self):
        o = self._make()
        class MockScout:
            def scout(self, opponents):
                return {"profiles": [{"name": "A", "threat_score": 0.8}]}
        o.register_module("opponent_scout", MockScout())
        o.initialize()
        r = o.run_pregame({"our_team": [], "enemy_team": [{"puuid": "p1"}]})
        assert "opponent_scout" in r["results"]

    def test_evolution_callback(self):
        o = self._make()
        events = []
        o.evolution_callback = lambda e: events.append(e)
        o.initialize()
        o.run_pregame({"our_team": [], "enemy_team": []})
        assert any(e.get("type") == "pregame_complete" for e in events)
