"""
TDD Tests for M746-M765: Deep History Injection Pipeline.
Tests per module: 10 across key modules. ~50% expected first-run failure.
"""
import sys, os, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
import pytest


class TestSummonerIdentityResolver:
    def _make(self):
        from lol_history.summoner_identity_resolver import SummonerIdentityResolver
        return SummonerIdentityResolver(cache_ttl=10.0, max_cache=50)

    def test_init(self):
        r = self._make()
        assert r.get_stats()["resolve_count"] == 0

    def test_register_source(self):
        r = self._make()
        result = r.register_source("lcu", object())
        assert result["status"] == "ok"

    def test_resolve_by_puuid(self):
        r = self._make()
        result = r.resolve_by_puuid("test-puuid")
        assert result["identity"]["puuid"] == "test-puuid"

    def test_cache_hit(self):
        r = self._make()
        r.resolve_by_puuid("abc")
        result = r.resolve_by_puuid("abc")
        assert result["source"] == "cache"

    def test_resolve_from_game(self):
        r = self._make()
        game = {"participants": [{"puuid": "p1", "gameName": "X", "tagLine": "NA1", "championId": 1, "teamId": 100}]}
        result = r.resolve_from_game(game, "p1")
        assert result["target_found"] is True

    def test_batch(self):
        r = self._make()
        result = r.batch_resolve(["p1", "p2"])
        assert result["resolved"] == 2

    def test_invalidate(self):
        r = self._make()
        r.resolve_by_puuid("x")
        result = r.invalidate("x")
        assert result["was_cached"] is True


class TestMatchHistoryDeepEnricher:
    def _make(self):
        from lol_history.match_history_deep_enricher import MatchHistoryDeepEnricher
        return MatchHistoryDeepEnricher()

    def _match(self):
        return {"metadata": {"matchId": "NA1_1"}, "info": {
            "gameDuration": 1800, "gameMode": "CLASSIC", "queueId": 420,
            "gameCreation": 17e11, "mapId": 11,
            "participants": [{"puuid": "p1", "championId": 222, "championName": "Jinx",
                "teamId": 100, "kills": 10, "deaths": 2, "assists": 8,
                "goldEarned": 15000, "totalDamageDealtToChampions": 30000,
                "totalMinionsKilled": 200, "neutralMinionsKilled": 10,
                "visionScore": 30, "win": True, "teamPosition": "BOTTOM",
                "item0": 3031, "item1": 3006, "item2": 0, "item3": 0,
                "item4": 0, "item5": 0, "item6": 3340,
                "spell1Id": 4, "spell2Id": 7,
                "perkPrimaryStyle": 8000, "perkSubStyle": 8200}]}}

    def test_enrich(self):
        result = self._make().enrich_match(self._match(), "p1")
        assert result["match"]["match_id"] == "NA1_1"

    def test_kda(self):
        result = self._make().enrich_match(self._match(), "p1")
        assert result["match"]["target_participant"]["kda"] == 9.0

    def test_batch(self):
        result = self._make().enrich_batch([self._match()], "p1")
        assert result["enriched"] == 1

    def test_stats(self):
        e = self._make()
        e.enrich_match(self._match())
        assert e.get_stats()["enrich_count"] == 1


class TestRankTierIntelligenceMapper:
    def _make(self):
        from lol_history.rank_tier_intelligence_mapper import RankTierIntelligenceMapper
        return RankTierIntelligenceMapper()

    def test_parse(self):
        result = self._make().parse_rank({"queueType": "RANKED_SOLO_5x5", "tier": "GOLD", "rank": "II", "leaguePoints": 45, "wins": 50, "losses": 40})
        assert "RANKED_SOLO_5x5" in result["queues"]

    def test_score(self):
        result = self._make().compute_rank_score("DIAMOND", "I", 50)
        assert result["numeric_score"] > 600

    def test_smurf(self):
        result = self._make().detect_anomaly({"numeric_score": 200}, recent_winrate=85)
        assert any(a["type"] == "possible_smurf" for a in result["anomalies"])

    def test_compare(self):
        result = self._make().compare_ranks({"numeric_score": 600}, {"numeric_score": 300})
        assert result["advantage"] == "player_a"


class TestGameFlowStateMachine:
    def _make(self):
        from lol_history.game_flow_state_machine import GameFlowStateMachine
        return GameFlowStateMachine()

    def test_initial(self):
        assert self._make().get_current_phase()["phase"] == "None"

    def test_transition(self):
        sm = self._make()
        result = sm.transition("Lobby")
        assert result["changed"] is True

    def test_lifecycle(self):
        sm = self._make()
        for p in ["Lobby", "Matchmaking", "ReadyCheck", "ChampSelect", "GameStart", "InProgress"]:
            sm.transition(p)
        assert sm.get_current_phase()["phase"] == "InProgress"

    def test_hook(self):
        sm = self._make()
        fired = []
        sm.register_hook("Lobby", lambda d: fired.append(1))
        sm.transition("Lobby")
        assert len(fired) == 1


class TestProcessLifecycleMonitor:
    def _make(self):
        from lol_history.process_lifecycle_monitor import ProcessLifecycleMonitor
        return ProcessLifecycleMonitor()

    def test_start(self):
        m = self._make()
        result = m.check_once([1234])
        assert "client_started" in result["events_fired"]

    def test_end(self):
        m = self._make()
        m.check_once([1234])
        result = m.check_once([], game_process_exists=False)
        assert "client_ended" in result["events_fired"]

    def test_change(self):
        m = self._make()
        m.check_once([1234])
        result = m.check_once([5678])
        assert "client_changed" in result["events_fired"]


class TestDeepHistoryInjectionOrchestrator:
    def _make(self):
        from lol_history.deep_history_injection_orchestrator import DeepHistoryInjectionOrchestrator
        return DeepHistoryInjectionOrchestrator()

    class _Dummy:
        def get_stats(self): return {}
        def process(self, d): return {"status": "ok"}

    def test_register(self):
        o = self._make()
        assert o.register("t", self._Dummy())["total_modules"] == 1

    def test_initialize(self):
        o = self._make()
        o.register("t", self._Dummy())
        assert o.initialize()["state"] == "initialized"

    def test_pregame(self):
        o = self._make()
        o.register("t", self._Dummy(), phases=["pregame"])
        assert o.process_pregame()["modules_processed"] == 1

    def test_phase_filter(self):
        o = self._make()
        o.register("a", self._Dummy(), phases=["pregame"])
        o.register("b", self._Dummy(), phases=["ingame"])
        assert o.process_pregame()["modules_processed"] == 1

    def test_error_handling(self):
        o = self._make()
        class Fail:
            def get_stats(self): return {}
            def process(self, d): raise RuntimeError("x")
        o.register("fail", Fail(), phases=["pregame"])
        result = o.process_pregame()
        assert len(result["errors"]) == 1

    def test_shutdown(self):
        o = self._make()
        o.register("t", self._Dummy())
        assert o.shutdown()["state"] == "shutdown"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
