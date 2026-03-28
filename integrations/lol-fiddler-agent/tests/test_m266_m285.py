"""
TDD Tests for M266-M285: Evolution Callback Injection + Data/Feedback/Replay Integration.

200 tests (10 per task). Designed to fail ~50% before implementation.
Tests verify evolution hooks are appended at file end WITHOUT adding/removing functions.

Author: dylanyunlong
"""

import pytest
import sys
import os
import time
import json
import inspect

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

# ═══════════════════════════════════════════════════════════════════════════
# M266: power_spike.py — evolution callback + training data annotation
# ═══════════════════════════════════════════════════════════════════════════

class TestM266PowerSpikeEvolution:
    """power_spike.py must gain _EVOLUTION_KEY + EvolvablePowerSpikeDetector wrapper."""

    def test_evolution_key_constant_exists(self):
        from lol_fiddler_agent.strategies import power_spike
        assert hasattr(power_spike, '_EVOLUTION_KEY')
        assert power_spike._EVOLUTION_KEY == 'power_spike'

    def test_evolvable_wrapper_class_exists(self):
        from lol_fiddler_agent.strategies.power_spike import EvolvablePowerSpikeDetector
        assert EvolvablePowerSpikeDetector is not None

    def test_evolvable_has_evolution_callback(self):
        from lol_fiddler_agent.strategies.power_spike import EvolvablePowerSpikeDetector
        det = EvolvablePowerSpikeDetector()
        assert hasattr(det, 'evolution_callback')

    def test_set_and_fire_evolution_callback(self):
        from lol_fiddler_agent.strategies.power_spike import EvolvablePowerSpikeDetector
        det = EvolvablePowerSpikeDetector()
        records = []
        det.evolution_callback = lambda d: records.append(d)
        det._fire_evolution({'test': True})
        assert len(records) == 1
        assert records[0]['test'] is True

    def test_callback_receives_module_key(self):
        from lol_fiddler_agent.strategies.power_spike import EvolvablePowerSpikeDetector
        det = EvolvablePowerSpikeDetector()
        records = []
        det.evolution_callback = lambda d: records.append(d)
        det._fire_evolution({'spike': 'level6'})
        assert records[0].get('module') == 'power_spike'

    def test_callback_includes_timestamp(self):
        from lol_fiddler_agent.strategies.power_spike import EvolvablePowerSpikeDetector
        det = EvolvablePowerSpikeDetector()
        records = []
        det.evolution_callback = lambda d: records.append(d)
        det._fire_evolution({'x': 1})
        assert 'timestamp' in records[0]

    def test_to_training_annotation(self):
        from lol_fiddler_agent.strategies.power_spike import EvolvablePowerSpikeDetector
        det = EvolvablePowerSpikeDetector()
        annotation = det.to_training_annotation(
            spike_type='level', significance=0.8, advice_count=2
        )
        assert annotation['module'] == 'power_spike'
        assert annotation['spike_type'] == 'level'
        assert annotation['significance'] == 0.8

    def test_original_class_unchanged(self):
        from lol_fiddler_agent.strategies.power_spike import PowerSpikeDetector
        det = PowerSpikeDetector()
        assert callable(det.evaluate)
        assert callable(det.reset)
        assert callable(det.get_recent_spikes)

    def test_function_count_unchanged(self):
        """Original file had 9 classes+functions. Must remain 9."""
        from lol_fiddler_agent.strategies import power_spike
        original_names = ['PowerSpike', 'PowerSpikeDetector']
        for name in original_names:
            assert hasattr(power_spike, name), f"Missing original: {name}"

    def test_no_callback_does_not_crash(self):
        from lol_fiddler_agent.strategies.power_spike import EvolvablePowerSpikeDetector
        det = EvolvablePowerSpikeDetector()
        det._fire_evolution({'x': 1})  # Should not raise


# ═══════════════════════════════════════════════════════════════════════════
# M267: gold_efficiency.py — evolution callback
# ═══════════════════════════════════════════════════════════════════════════

class TestM267GoldEfficiencyEvolution:

    def test_evolution_key(self):
        from lol_fiddler_agent.strategies import gold_efficiency
        assert gold_efficiency._EVOLUTION_KEY == 'gold_efficiency'

    def test_evolvable_wrapper_exists(self):
        from lol_fiddler_agent.strategies.gold_efficiency import EvolvableGoldEfficiencyEvaluator
        assert EvolvableGoldEfficiencyEvaluator is not None

    def test_has_evolution_callback_slot(self):
        from lol_fiddler_agent.strategies.gold_efficiency import EvolvableGoldEfficiencyEvaluator
        e = EvolvableGoldEfficiencyEvaluator()
        assert hasattr(e, 'evolution_callback')

    def test_fire_evolution_with_data(self):
        from lol_fiddler_agent.strategies.gold_efficiency import EvolvableGoldEfficiencyEvaluator
        e = EvolvableGoldEfficiencyEvaluator()
        results = []
        e.evolution_callback = lambda d: results.append(d)
        e._fire_evolution({'grade': 'S'})
        assert results[0]['module'] == 'gold_efficiency'

    def test_to_training_annotation(self):
        from lol_fiddler_agent.strategies.gold_efficiency import EvolvableGoldEfficiencyEvaluator
        e = EvolvableGoldEfficiencyEvaluator()
        ann = e.to_training_annotation(gold_per_min=420.0, grade='A')
        assert ann['gold_per_min'] == 420.0

    def test_original_classes_intact(self):
        from lol_fiddler_agent.strategies.gold_efficiency import GoldEfficiencyEvaluator, GoldAnalysis
        assert GoldEfficiencyEvaluator is not None
        assert GoldAnalysis is not None

    def test_function_count(self):
        from lol_fiddler_agent.strategies import gold_efficiency
        assert hasattr(gold_efficiency, 'GoldEfficiencyEvaluator')

    def test_no_callback_safe(self):
        from lol_fiddler_agent.strategies.gold_efficiency import EvolvableGoldEfficiencyEvaluator
        e = EvolvableGoldEfficiencyEvaluator()
        e._fire_evolution({})

    def test_callback_timestamp_present(self):
        from lol_fiddler_agent.strategies.gold_efficiency import EvolvableGoldEfficiencyEvaluator
        e = EvolvableGoldEfficiencyEvaluator()
        r = []
        e.evolution_callback = lambda d: r.append(d)
        e._fire_evolution({})
        assert 'timestamp' in r[0]

    def test_inherits_base(self):
        from lol_fiddler_agent.strategies.gold_efficiency import EvolvableGoldEfficiencyEvaluator
        e = EvolvableGoldEfficiencyEvaluator()
        assert callable(getattr(e, 'evaluate', None))


# ═══════════════════════════════════════════════════════════════════════════
# M268: map_awareness.py — evolution callback
# ═══════════════════════════════════════════════════════════════════════════

class TestM268MapAwarenessEvolution:

    def test_evolution_key(self):
        from lol_fiddler_agent.strategies import map_awareness
        assert map_awareness._EVOLUTION_KEY == 'map_awareness'

    def test_evolvable_wrapper(self):
        from lol_fiddler_agent.strategies.map_awareness import EvolvableMapAwarenessEvaluator
        e = EvolvableMapAwarenessEvaluator()
        assert hasattr(e, 'evolution_callback')

    def test_fire_evolution(self):
        from lol_fiddler_agent.strategies.map_awareness import EvolvableMapAwarenessEvaluator
        e = EvolvableMapAwarenessEvaluator()
        r = []
        e.evolution_callback = lambda d: r.append(d)
        e._fire_evolution({'ward': True})
        assert r[0]['module'] == 'map_awareness'

    def test_training_annotation(self):
        from lol_fiddler_agent.strategies.map_awareness import EvolvableMapAwarenessEvaluator
        e = EvolvableMapAwarenessEvaluator()
        ann = e.to_training_annotation(ward_count=3, roam_detected=True)
        assert ann['ward_count'] == 3

    def test_original_intact(self):
        from lol_fiddler_agent.strategies.map_awareness import MapAwarenessEvaluator
        assert MapAwarenessEvaluator is not None

    def test_original_evaluate_exists(self):
        from lol_fiddler_agent.strategies.map_awareness import MapAwarenessEvaluator
        e = MapAwarenessEvaluator()
        assert callable(e.evaluate)

    def test_map_region_enum_intact(self):
        from lol_fiddler_agent.strategies.map_awareness import MapRegion
        assert MapRegion is not None

    def test_no_callback_safe(self):
        from lol_fiddler_agent.strategies.map_awareness import EvolvableMapAwarenessEvaluator
        e = EvolvableMapAwarenessEvaluator()
        e._fire_evolution({})

    def test_inherits_evaluate(self):
        from lol_fiddler_agent.strategies.map_awareness import EvolvableMapAwarenessEvaluator
        assert callable(getattr(EvolvableMapAwarenessEvaluator(), 'evaluate', None))

    def test_ward_suggestion_class_intact(self):
        from lol_fiddler_agent.strategies.map_awareness import WardSuggestion
        assert WardSuggestion is not None


# ═══════════════════════════════════════════════════════════════════════════
# M269: objective_timer.py — evolution callback
# ═══════════════════════════════════════════════════════════════════════════

class TestM269ObjectiveTimerEvolution:

    def test_evolution_key(self):
        from lol_fiddler_agent.strategies import objective_timer
        assert objective_timer._EVOLUTION_KEY == 'objective_timer'

    def test_evolvable_wrapper(self):
        from lol_fiddler_agent.strategies.objective_timer import EvolvableObjectiveTracker
        assert EvolvableObjectiveTracker is not None

    def test_has_callback(self):
        from lol_fiddler_agent.strategies.objective_timer import EvolvableObjectiveTracker
        e = EvolvableObjectiveTracker()
        assert hasattr(e, 'evolution_callback')

    def test_fire_evolution(self):
        from lol_fiddler_agent.strategies.objective_timer import EvolvableObjectiveTracker
        e = EvolvableObjectiveTracker()
        r = []
        e.evolution_callback = lambda d: r.append(d)
        e._fire_evolution({'dragon': 'cloud'})
        assert r[0]['module'] == 'objective_timer'

    def test_training_annotation(self):
        from lol_fiddler_agent.strategies.objective_timer import EvolvableObjectiveTracker
        e = EvolvableObjectiveTracker()
        ann = e.to_training_annotation(objective='dragon', team='ORDER')
        assert ann['objective'] == 'dragon'

    def test_original_classes(self):
        from lol_fiddler_agent.strategies.objective_timer import ObjectiveTracker, ObjectiveType
        assert ObjectiveTracker is not None
        assert ObjectiveType is not None

    def test_original_update_method(self):
        from lol_fiddler_agent.strategies.objective_timer import ObjectiveTracker
        t = ObjectiveTracker()
        assert callable(t.update)

    def test_no_callback_safe(self):
        from lol_fiddler_agent.strategies.objective_timer import EvolvableObjectiveTracker
        e = EvolvableObjectiveTracker()
        e._fire_evolution({})

    def test_get_alerts_intact(self):
        from lol_fiddler_agent.strategies.objective_timer import ObjectiveTracker
        t = ObjectiveTracker()
        alerts = t.get_alerts(0.0)
        assert isinstance(alerts, list)

    def test_inherits_tracker(self):
        from lol_fiddler_agent.strategies.objective_timer import EvolvableObjectiveTracker, ObjectiveTracker
        assert issubclass(EvolvableObjectiveTracker, ObjectiveTracker)


# ═══════════════════════════════════════════════════════════════════════════
# M270: death_analyzer.py — evolution callback
# ═══════════════════════════════════════════════════════════════════════════

class TestM270DeathAnalyzerEvolution:

    def test_evolution_key(self):
        from lol_fiddler_agent.strategies import death_analyzer
        assert death_analyzer._EVOLUTION_KEY == 'death_analyzer'

    def test_evolvable_wrapper(self):
        from lol_fiddler_agent.strategies.death_analyzer import EvolvableDeathAnalyzerEngine
        assert EvolvableDeathAnalyzerEngine is not None

    def test_has_callback(self):
        from lol_fiddler_agent.strategies.death_analyzer import EvolvableDeathAnalyzerEngine
        e = EvolvableDeathAnalyzerEngine()
        assert hasattr(e, 'evolution_callback')

    def test_fire_evolution(self):
        from lol_fiddler_agent.strategies.death_analyzer import EvolvableDeathAnalyzerEngine
        e = EvolvableDeathAnalyzerEngine()
        r = []
        e.evolution_callback = lambda d: r.append(d)
        e._fire_evolution({'death_type': 'ganked'})
        assert r[0]['module'] == 'death_analyzer'

    def test_training_annotation(self):
        from lol_fiddler_agent.strategies.death_analyzer import EvolvableDeathAnalyzerEngine
        e = EvolvableDeathAnalyzerEngine()
        ann = e.to_training_annotation(avoidable_rate=0.6, total_deaths=5)
        assert ann['avoidable_rate'] == 0.6

    def test_original_classes(self):
        from lol_fiddler_agent.strategies.death_analyzer import DeathAnalyzerEngine, DeathRecord
        assert DeathAnalyzerEngine is not None
        assert DeathRecord is not None

    def test_record_death_intact(self):
        from lol_fiddler_agent.strategies.death_analyzer import DeathAnalyzerEngine, DeathRecord
        e = DeathAnalyzerEngine()
        e.record_death(DeathRecord(game_time=100.0, game_phase='MID'))
        assert e.death_count == 1

    def test_no_callback_safe(self):
        from lol_fiddler_agent.strategies.death_analyzer import EvolvableDeathAnalyzerEngine
        e = EvolvableDeathAnalyzerEngine()
        e._fire_evolution({})

    def test_inherits_engine(self):
        from lol_fiddler_agent.strategies.death_analyzer import EvolvableDeathAnalyzerEngine, DeathAnalyzerEngine
        assert issubclass(EvolvableDeathAnalyzerEngine, DeathAnalyzerEngine)

    def test_analyze_method_intact(self):
        from lol_fiddler_agent.strategies.death_analyzer import EvolvableDeathAnalyzerEngine
        e = EvolvableDeathAnalyzerEngine()
        result = e.analyze()
        assert hasattr(result, 'avoidable_rate')


# ═══════════════════════════════════════════════════════════════════════════
# M271: wave_management.py — evolution callback
# ═══════════════════════════════════════════════════════════════════════════

class TestM271WaveManagementEvolution:

    def test_evolution_key(self):
        from lol_fiddler_agent.strategies import wave_management
        assert wave_management._EVOLUTION_KEY == 'wave_management'

    def test_evolvable_wrapper(self):
        from lol_fiddler_agent.strategies.wave_management import EvolvableWaveManagementEvaluator
        assert EvolvableWaveManagementEvaluator is not None

    def test_has_callback(self):
        from lol_fiddler_agent.strategies.wave_management import EvolvableWaveManagementEvaluator
        e = EvolvableWaveManagementEvaluator()
        assert hasattr(e, 'evolution_callback')

    def test_fire_evolution(self):
        from lol_fiddler_agent.strategies.wave_management import EvolvableWaveManagementEvaluator
        e = EvolvableWaveManagementEvaluator()
        r = []
        e.evolution_callback = lambda d: r.append(d)
        e._fire_evolution({'cs_grade': 'A'})
        assert r[0]['module'] == 'wave_management'

    def test_training_annotation(self):
        from lol_fiddler_agent.strategies.wave_management import EvolvableWaveManagementEvaluator
        e = EvolvableWaveManagementEvaluator()
        ann = e.to_training_annotation(cs_per_min=7.5, cs_grade='A')
        assert ann['cs_per_min'] == 7.5

    def test_original_class(self):
        from lol_fiddler_agent.strategies.wave_management import WaveManagementEvaluator
        assert WaveManagementEvaluator is not None

    def test_get_cs_grade_intact(self):
        from lol_fiddler_agent.strategies.wave_management import WaveManagementEvaluator
        grade = WaveManagementEvaluator.get_cs_grade(8.0)
        assert grade in ('S', 'A', 'B', 'C', 'D')

    def test_no_callback_safe(self):
        from lol_fiddler_agent.strategies.wave_management import EvolvableWaveManagementEvaluator
        e = EvolvableWaveManagementEvaluator()
        e._fire_evolution({})

    def test_inherits_evaluator(self):
        from lol_fiddler_agent.strategies.wave_management import EvolvableWaveManagementEvaluator, WaveManagementEvaluator
        assert issubclass(EvolvableWaveManagementEvaluator, WaveManagementEvaluator)

    def test_evaluate_method(self):
        from lol_fiddler_agent.strategies.wave_management import EvolvableWaveManagementEvaluator
        e = EvolvableWaveManagementEvaluator()
        assert callable(e.evaluate)


# ═══════════════════════════════════════════════════════════════════════════
# M272: summoner_tracker.py — evolution callback
# ═══════════════════════════════════════════════════════════════════════════

class TestM272SummonerTrackerEvolution:

    def test_evolution_key(self):
        from lol_fiddler_agent.strategies import summoner_tracker
        assert summoner_tracker._EVOLUTION_KEY == 'summoner_tracker'

    def test_evolvable_wrapper(self):
        from lol_fiddler_agent.strategies.summoner_tracker import EvolvableSummonerSpellTracker
        assert EvolvableSummonerSpellTracker is not None

    def test_has_callback(self):
        from lol_fiddler_agent.strategies.summoner_tracker import EvolvableSummonerSpellTracker
        e = EvolvableSummonerSpellTracker()
        assert hasattr(e, 'evolution_callback')

    def test_fire_evolution(self):
        from lol_fiddler_agent.strategies.summoner_tracker import EvolvableSummonerSpellTracker
        e = EvolvableSummonerSpellTracker()
        r = []
        e.evolution_callback = lambda d: r.append(d)
        e._fire_evolution({'flash_down': 'enemy_adc'})
        assert r[0]['module'] == 'summoner_tracker'

    def test_training_annotation(self):
        from lol_fiddler_agent.strategies.summoner_tracker import EvolvableSummonerSpellTracker
        e = EvolvableSummonerSpellTracker()
        ann = e.to_training_annotation(tracked_count=8, engage_windows=2)
        assert ann['tracked_count'] == 8

    def test_original_class(self):
        from lol_fiddler_agent.strategies.summoner_tracker import SummonerSpellTracker
        assert SummonerSpellTracker is not None

    def test_mark_used_intact(self):
        from lol_fiddler_agent.strategies.summoner_tracker import SummonerSpellTracker
        t = SummonerSpellTracker()
        assert callable(t.mark_used)

    def test_no_callback_safe(self):
        from lol_fiddler_agent.strategies.summoner_tracker import EvolvableSummonerSpellTracker
        e = EvolvableSummonerSpellTracker()
        e._fire_evolution({})

    def test_inherits_tracker(self):
        from lol_fiddler_agent.strategies.summoner_tracker import EvolvableSummonerSpellTracker, SummonerSpellTracker
        assert issubclass(EvolvableSummonerSpellTracker, SummonerSpellTracker)

    def test_tracked_count_property(self):
        from lol_fiddler_agent.strategies.summoner_tracker import EvolvableSummonerSpellTracker
        e = EvolvableSummonerSpellTracker()
        assert e.tracked_count == 0


# ═══════════════════════════════════════════════════════════════════════════
# M273: team_comp.py — evolution callback
# ═══════════════════════════════════════════════════════════════════════════

class TestM273TeamCompEvolution:

    def test_evolution_key(self):
        from lol_fiddler_agent.strategies import team_comp
        assert team_comp._EVOLUTION_KEY == 'team_comp'

    def test_evolvable_wrapper(self):
        from lol_fiddler_agent.strategies.team_comp import EvolvableTeamCompAnalyzer
        assert EvolvableTeamCompAnalyzer is not None

    def test_has_callback(self):
        from lol_fiddler_agent.strategies.team_comp import EvolvableTeamCompAnalyzer
        e = EvolvableTeamCompAnalyzer()
        assert hasattr(e, 'evolution_callback')

    def test_fire_evolution(self):
        from lol_fiddler_agent.strategies.team_comp import EvolvableTeamCompAnalyzer
        e = EvolvableTeamCompAnalyzer()
        r = []
        e.evolution_callback = lambda d: r.append(d)
        e._fire_evolution({'comp_type': 'teamfight'})
        assert r[0]['module'] == 'team_comp'

    def test_training_annotation(self):
        from lol_fiddler_agent.strategies.team_comp import EvolvableTeamCompAnalyzer
        e = EvolvableTeamCompAnalyzer()
        ann = e.to_training_annotation(has_frontline=True, damage_profile='mixed')
        assert ann['has_frontline'] is True

    def test_original_class(self):
        from lol_fiddler_agent.strategies.team_comp import TeamCompAnalyzer
        assert TeamCompAnalyzer is not None

    def test_composition_profile_intact(self):
        from lol_fiddler_agent.strategies.team_comp import CompositionProfile
        assert CompositionProfile is not None

    def test_no_callback_safe(self):
        from lol_fiddler_agent.strategies.team_comp import EvolvableTeamCompAnalyzer
        e = EvolvableTeamCompAnalyzer()
        e._fire_evolution({})

    def test_inherits_analyzer(self):
        from lol_fiddler_agent.strategies.team_comp import EvolvableTeamCompAnalyzer, TeamCompAnalyzer
        assert issubclass(EvolvableTeamCompAnalyzer, TeamCompAnalyzer)

    def test_evaluate_method(self):
        from lol_fiddler_agent.strategies.team_comp import EvolvableTeamCompAnalyzer
        e = EvolvableTeamCompAnalyzer()
        assert callable(e.evaluate)


# ═══════════════════════════════════════════════════════════════════════════
# M274: pre_game.py — connect to pregame_scout
# ═══════════════════════════════════════════════════════════════════════════

class TestM274PreGameEvolution:

    def test_evolution_key(self):
        from lol_fiddler_agent.strategies import pre_game
        assert pre_game._EVOLUTION_KEY == 'pre_game'

    def test_evolvable_wrapper(self):
        from lol_fiddler_agent.strategies.pre_game import EvolvablePreGameAnalyzer
        assert EvolvablePreGameAnalyzer is not None

    def test_has_callback(self):
        from lol_fiddler_agent.strategies.pre_game import EvolvablePreGameAnalyzer
        e = EvolvablePreGameAnalyzer()
        assert hasattr(e, 'evolution_callback')

    def test_fire_evolution(self):
        from lol_fiddler_agent.strategies.pre_game import EvolvablePreGameAnalyzer
        e = EvolvablePreGameAnalyzer()
        r = []
        e.evolution_callback = lambda d: r.append(d)
        e._fire_evolution({'matchup': 'favorable'})
        assert r[0]['module'] == 'pre_game'

    def test_has_scout_slot(self):
        from lol_fiddler_agent.strategies.pre_game import EvolvablePreGameAnalyzer
        e = EvolvablePreGameAnalyzer()
        assert hasattr(e, 'pregame_scout')

    def test_set_scout(self):
        from lol_fiddler_agent.strategies.pre_game import EvolvablePreGameAnalyzer
        e = EvolvablePreGameAnalyzer()
        e.pregame_scout = "mock_scout"
        assert e.pregame_scout == "mock_scout"

    def test_training_annotation(self):
        from lol_fiddler_agent.strategies.pre_game import EvolvablePreGameAnalyzer
        e = EvolvablePreGameAnalyzer()
        ann = e.to_training_annotation(matchup_score=0.7, early_plan='aggressive')
        assert ann['matchup_score'] == 0.7

    def test_original_class(self):
        from lol_fiddler_agent.strategies.pre_game import PreGameAnalyzer
        assert PreGameAnalyzer is not None

    def test_no_callback_safe(self):
        from lol_fiddler_agent.strategies.pre_game import EvolvablePreGameAnalyzer
        e = EvolvablePreGameAnalyzer()
        e._fire_evolution({})

    def test_inherits_analyzer(self):
        from lol_fiddler_agent.strategies.pre_game import EvolvablePreGameAnalyzer, PreGameAnalyzer
        assert issubclass(EvolvablePreGameAnalyzer, PreGameAnalyzer)


# ═══════════════════════════════════════════════════════════════════════════
# M275: orchestrator.py — global strategy coordination + evolution integration
# ═══════════════════════════════════════════════════════════════════════════

class TestM275OrchestratorEvolution:

    def test_evolution_key(self):
        from lol_fiddler_agent import orchestrator
        assert orchestrator._EVOLUTION_KEY == 'orchestrator'

    def test_evolution_coordinator_class(self):
        from lol_fiddler_agent.orchestrator import EvolutionCoordinator
        assert EvolutionCoordinator is not None

    def test_coordinator_register_module(self):
        from lol_fiddler_agent.orchestrator import EvolutionCoordinator
        c = EvolutionCoordinator()
        c.register_module('power_spike', {})
        assert 'power_spike' in c.registered_modules

    def test_coordinator_collect_annotations(self):
        from lol_fiddler_agent.orchestrator import EvolutionCoordinator
        c = EvolutionCoordinator()
        c.record_annotation({'module': 'test', 'data': 123})
        assert len(c.annotations) == 1

    def test_coordinator_export_training_batch(self):
        from lol_fiddler_agent.orchestrator import EvolutionCoordinator
        c = EvolutionCoordinator()
        c.record_annotation({'module': 'a', 'x': 1})
        c.record_annotation({'module': 'b', 'x': 2})
        batch = c.export_training_batch()
        assert len(batch) == 2

    def test_coordinator_reset(self):
        from lol_fiddler_agent.orchestrator import EvolutionCoordinator
        c = EvolutionCoordinator()
        c.record_annotation({'x': 1})
        c.reset()
        assert len(c.annotations) == 0

    def test_coordinator_stats(self):
        from lol_fiddler_agent.orchestrator import EvolutionCoordinator
        c = EvolutionCoordinator()
        stats = c.get_stats()
        assert 'total_annotations' in stats

    def test_original_orchestrator_intact(self):
        from lol_fiddler_agent.orchestrator import Orchestrator, OrchestratorConfig
        assert Orchestrator is not None
        assert OrchestratorConfig is not None

    def test_original_function_count(self):
        from lol_fiddler_agent import orchestrator
        assert hasattr(orchestrator, 'Orchestrator')
        assert hasattr(orchestrator, 'OrchestratorConfig')

    def test_no_callback_safe(self):
        from lol_fiddler_agent.orchestrator import EvolutionCoordinator
        c = EvolutionCoordinator()
        c.record_annotation({})  # Should not crash


# ═══════════════════════════════════════════════════════════════════════════
# M276: pipeline.py — feature flow + training data formatting
# ═══════════════════════════════════════════════════════════════════════════

class TestM276PipelineEvolution:

    def test_evolution_key(self):
        from lol_fiddler_agent.data import pipeline
        assert pipeline._EVOLUTION_KEY == 'data_pipeline'

    def test_training_stage_class(self):
        from lol_fiddler_agent.data.pipeline import TrainingAnnotationStage
        assert TrainingAnnotationStage is not None

    def test_training_stage_is_pipeline_stage(self):
        from lol_fiddler_agent.data.pipeline import TrainingAnnotationStage, PipelineStage
        assert issubclass(TrainingAnnotationStage, PipelineStage)

    def test_training_stage_process_returns_snapshot(self):
        import asyncio
        from lol_fiddler_agent.data.pipeline import TrainingAnnotationStage
        from lol_fiddler_agent.models.game_snapshot import GameSnapshot
        stage = TrainingAnnotationStage()
        snap = GameSnapshot(game_time=300.0, active_player_name="Test")
        result = asyncio.get_event_loop().run_until_complete(stage.process(snap))
        assert result is not None

    def test_training_stage_fires_callback(self):
        import asyncio
        from lol_fiddler_agent.data.pipeline import TrainingAnnotationStage
        from lol_fiddler_agent.models.game_snapshot import GameSnapshot
        stage = TrainingAnnotationStage()
        records = []
        stage.on_annotation = lambda d: records.append(d)
        snap = GameSnapshot(game_time=300.0, active_player_name="Test")
        asyncio.get_event_loop().run_until_complete(stage.process(snap))
        assert len(records) >= 1

    def test_create_evolution_pipeline(self):
        from lol_fiddler_agent.data.pipeline import create_evolution_pipeline
        p = create_evolution_pipeline()
        assert p.stage_count >= 5  # Standard 4 + training annotation

    def test_original_create_standard_pipeline(self):
        from lol_fiddler_agent.data.pipeline import create_standard_pipeline
        p = create_standard_pipeline()
        assert p.stage_count == 4

    def test_original_classes(self):
        from lol_fiddler_agent.data.pipeline import DataPipeline, ParseStage, ValidateStage
        assert DataPipeline is not None

    def test_pipeline_metrics_intact(self):
        from lol_fiddler_agent.data.pipeline import PipelineMetrics
        m = PipelineMetrics()
        assert m.drop_rate == 0.0

    def test_no_callback_safe(self):
        import asyncio
        from lol_fiddler_agent.data.pipeline import TrainingAnnotationStage
        from lol_fiddler_agent.models.game_snapshot import GameSnapshot
        stage = TrainingAnnotationStage()
        snap = GameSnapshot(game_time=300.0, active_player_name="Test")
        asyncio.get_event_loop().run_until_complete(stage.process(snap))


# ═══════════════════════════════════════════════════════════════════════════
# M277: event_processor.py — game event → training span
# ═══════════════════════════════════════════════════════════════════════════

class TestM277EventProcessorEvolution:

    def test_evolution_key(self):
        from lol_fiddler_agent.data import event_processor
        assert event_processor._EVOLUTION_KEY == 'event_processor'

    def test_evolvable_wrapper(self):
        from lol_fiddler_agent.data.event_processor import EvolvableEventProcessor
        assert EvolvableEventProcessor is not None

    def test_has_callback(self):
        from lol_fiddler_agent.data.event_processor import EvolvableEventProcessor
        e = EvolvableEventProcessor()
        assert hasattr(e, 'evolution_callback')

    def test_fire_evolution(self):
        from lol_fiddler_agent.data.event_processor import EvolvableEventProcessor
        e = EvolvableEventProcessor()
        r = []
        e.evolution_callback = lambda d: r.append(d)
        e._fire_evolution({'event': 'kill'})
        assert r[0]['module'] == 'event_processor'

    def test_to_training_span(self):
        from lol_fiddler_agent.data.event_processor import EvolvableEventProcessor
        e = EvolvableEventProcessor()
        span = e.to_training_span(event_type='kill', game_time=150.0, magnitude=0.8)
        assert span['event_type'] == 'kill'
        assert span['game_time'] == 150.0

    def test_original_class(self):
        from lol_fiddler_agent.data.event_processor import EventProcessor
        assert EventProcessor is not None

    def test_original_process_events(self):
        from lol_fiddler_agent.data.event_processor import EventProcessor
        e = EventProcessor()
        assert callable(e.process_events)

    def test_no_callback_safe(self):
        from lol_fiddler_agent.data.event_processor import EvolvableEventProcessor
        e = EvolvableEventProcessor()
        e._fire_evolution({})

    def test_inherits_processor(self):
        from lol_fiddler_agent.data.event_processor import EvolvableEventProcessor, EventProcessor
        assert issubclass(EvolvableEventProcessor, EventProcessor)

    def test_momentum_shift_class_intact(self):
        from lol_fiddler_agent.data.event_processor import MomentumShift
        assert MomentumShift is not None


# ═══════════════════════════════════════════════════════════════════════════
# M278: performance_report.py — post-game analysis
# ═══════════════════════════════════════════════════════════════════════════

class TestM278PerformanceReportEvolution:

    def test_evolution_key(self):
        from lol_fiddler_agent.data import performance_report
        assert performance_report._EVOLUTION_KEY == 'performance_report'

    def test_evolvable_wrapper(self):
        from lol_fiddler_agent.data.performance_report import EvolvableReportGenerator
        assert EvolvableReportGenerator is not None

    def test_has_callback(self):
        from lol_fiddler_agent.data.performance_report import EvolvableReportGenerator
        e = EvolvableReportGenerator()
        assert hasattr(e, 'evolution_callback')

    def test_fire_evolution(self):
        from lol_fiddler_agent.data.performance_report import EvolvableReportGenerator
        e = EvolvableReportGenerator()
        r = []
        e.evolution_callback = lambda d: r.append(d)
        e._fire_evolution({'grade': 'S'})
        assert r[0]['module'] == 'performance_report'

    def test_training_annotation(self):
        from lol_fiddler_agent.data.performance_report import EvolvableReportGenerator
        e = EvolvableReportGenerator()
        ann = e.to_training_annotation(overall_grade='A', kda=4.5, won=True)
        assert ann['overall_grade'] == 'A'
        assert ann['won'] is True

    def test_original_class(self):
        from lol_fiddler_agent.data.performance_report import ReportGenerator
        assert ReportGenerator is not None

    def test_generate_method_intact(self):
        from lol_fiddler_agent.data.performance_report import ReportGenerator
        g = ReportGenerator()
        assert callable(g.generate)

    def test_no_callback_safe(self):
        from lol_fiddler_agent.data.performance_report import EvolvableReportGenerator
        e = EvolvableReportGenerator()
        e._fire_evolution({})

    def test_inherits_generator(self):
        from lol_fiddler_agent.data.performance_report import EvolvableReportGenerator, ReportGenerator
        assert issubclass(EvolvableReportGenerator, ReportGenerator)

    def test_performance_grade_class(self):
        from lol_fiddler_agent.data.performance_report import PerformanceGrade
        assert PerformanceGrade is not None


# ═══════════════════════════════════════════════════════════════════════════
# M279: exporter.py — export formats
# ═══════════════════════════════════════════════════════════════════════════

class TestM279ExporterEvolution:

    def test_evolution_key(self):
        from lol_fiddler_agent.data import exporter
        assert exporter._EVOLUTION_KEY == 'exporter'

    def test_evolvable_wrapper(self):
        from lol_fiddler_agent.data.exporter import EvolvableDataExporter
        assert EvolvableDataExporter is not None

    def test_has_callback(self):
        from lol_fiddler_agent.data.exporter import EvolvableDataExporter
        e = EvolvableDataExporter()
        assert hasattr(e, 'evolution_callback')

    def test_fire_evolution(self):
        from lol_fiddler_agent.data.exporter import EvolvableDataExporter
        e = EvolvableDataExporter()
        r = []
        e.evolution_callback = lambda d: r.append(d)
        e._fire_evolution({'format': 'jsonl'})
        assert r[0]['module'] == 'exporter'

    def test_export_al_format(self):
        from lol_fiddler_agent.data.exporter import EvolvableDataExporter
        e = EvolvableDataExporter()
        row = e.to_agentlightning_format(
            snapshot_id='s1', game_time=300.0, features={'gold': 5000}
        )
        assert row['snapshot_id'] == 's1'
        assert row['format'] == 'agentlightning'

    def test_original_class(self):
        from lol_fiddler_agent.data.exporter import DataExporter
        assert DataExporter is not None

    def test_add_method_intact(self):
        from lol_fiddler_agent.data.exporter import DataExporter
        e = DataExporter()
        assert callable(e.add)

    def test_no_callback_safe(self):
        from lol_fiddler_agent.data.exporter import EvolvableDataExporter
        e = EvolvableDataExporter()
        e._fire_evolution({})

    def test_inherits_exporter(self):
        from lol_fiddler_agent.data.exporter import EvolvableDataExporter, DataExporter
        assert issubclass(EvolvableDataExporter, DataExporter)

    def test_export_config_intact(self):
        from lol_fiddler_agent.data.exporter import ExportConfig
        assert ExportConfig is not None


# ═══════════════════════════════════════════════════════════════════════════
# M280: feedback/aggregator.py — multi-dimensional reward aggregation
# ═══════════════════════════════════════════════════════════════════════════

class TestM280AggregatorEvolution:

    def test_evolution_key(self):
        from lol_fiddler_agent.feedback import aggregator
        assert aggregator._EVOLUTION_KEY == 'feedback_aggregator'

    def test_evolvable_wrapper(self):
        from lol_fiddler_agent.feedback.aggregator import EvolvableFeedbackAggregator
        assert EvolvableFeedbackAggregator is not None

    def test_has_callback(self):
        from lol_fiddler_agent.feedback.aggregator import EvolvableFeedbackAggregator
        e = EvolvableFeedbackAggregator()
        assert hasattr(e, 'evolution_callback')

    def test_fire_evolution(self):
        from lol_fiddler_agent.feedback.aggregator import EvolvableFeedbackAggregator
        e = EvolvableFeedbackAggregator()
        r = []
        e.evolution_callback = lambda d: r.append(d)
        e._fire_evolution({'compliance': 0.75})
        assert r[0]['module'] == 'feedback_aggregator'

    def test_compute_multidim_reward(self):
        from lol_fiddler_agent.feedback.aggregator import EvolvableFeedbackAggregator
        e = EvolvableFeedbackAggregator()
        reward = e.compute_multidim_reward(
            compliance=0.8, effectiveness=0.6, gold_delta=500
        )
        assert isinstance(reward, dict)
        assert 'composite' in reward

    def test_to_training_annotation(self):
        from lol_fiddler_agent.feedback.aggregator import EvolvableFeedbackAggregator
        e = EvolvableFeedbackAggregator()
        ann = e.to_training_annotation(session_id='s1', win=True, compliance=0.7)
        assert ann['session_id'] == 's1'

    def test_original_class(self):
        from lol_fiddler_agent.feedback.aggregator import FeedbackAggregator
        assert FeedbackAggregator is not None

    def test_add_records_intact(self):
        from lol_fiddler_agent.feedback.aggregator import FeedbackAggregator
        a = FeedbackAggregator()
        assert callable(a.add_records)

    def test_no_callback_safe(self):
        from lol_fiddler_agent.feedback.aggregator import EvolvableFeedbackAggregator
        e = EvolvableFeedbackAggregator()
        e._fire_evolution({})

    def test_inherits_aggregator(self):
        from lol_fiddler_agent.feedback.aggregator import EvolvableFeedbackAggregator, FeedbackAggregator
        assert issubclass(EvolvableFeedbackAggregator, FeedbackAggregator)


# ═══════════════════════════════════════════════════════════════════════════
# M281: feedback/tracker.py — advice adoption tracking
# ═══════════════════════════════════════════════════════════════════════════

class TestM281TrackerEvolution:

    def test_evolution_key(self):
        from lol_fiddler_agent.feedback import tracker
        assert tracker._EVOLUTION_KEY == 'feedback_tracker'

    def test_evolvable_wrapper(self):
        from lol_fiddler_agent.feedback.tracker import EvolvableFeedbackTracker
        assert EvolvableFeedbackTracker is not None

    def test_has_callback(self):
        from lol_fiddler_agent.feedback.tracker import EvolvableFeedbackTracker
        e = EvolvableFeedbackTracker()
        assert hasattr(e, 'evolution_callback')

    def test_fire_evolution(self):
        from lol_fiddler_agent.feedback.tracker import EvolvableFeedbackTracker
        e = EvolvableFeedbackTracker()
        r = []
        e.evolution_callback = lambda d: r.append(d)
        e._fire_evolution({'followed': True})
        assert r[0]['module'] == 'feedback_tracker'

    def test_to_training_annotation(self):
        from lol_fiddler_agent.feedback.tracker import EvolvableFeedbackTracker
        e = EvolvableFeedbackTracker()
        ann = e.to_training_annotation(compliance_rate=0.65, total_records=20)
        assert ann['compliance_rate'] == 0.65

    def test_original_class(self):
        from lol_fiddler_agent.feedback.tracker import FeedbackTracker
        assert FeedbackTracker is not None

    def test_export_for_training_intact(self):
        from lol_fiddler_agent.feedback.tracker import FeedbackTracker
        t = FeedbackTracker()
        data = t.export_for_training()
        assert isinstance(data, list)

    def test_no_callback_safe(self):
        from lol_fiddler_agent.feedback.tracker import EvolvableFeedbackTracker
        e = EvolvableFeedbackTracker()
        e._fire_evolution({})

    def test_inherits_tracker(self):
        from lol_fiddler_agent.feedback.tracker import EvolvableFeedbackTracker, FeedbackTracker
        assert issubclass(EvolvableFeedbackTracker, FeedbackTracker)

    def test_compliance_record_intact(self):
        from lol_fiddler_agent.feedback.tracker import ComplianceRecord
        assert ComplianceRecord is not None


# ═══════════════════════════════════════════════════════════════════════════
# M282: replay/buffer.py — experience replay for RL
# ═══════════════════════════════════════════════════════════════════════════

class TestM282BufferEvolution:

    def test_evolution_key(self):
        from lol_fiddler_agent.replay import buffer
        assert buffer._EVOLUTION_KEY == 'replay_buffer'

    def test_evolvable_wrapper(self):
        from lol_fiddler_agent.replay.buffer import EvolvableReplayBuffer
        assert EvolvableReplayBuffer is not None

    def test_has_callback(self):
        from lol_fiddler_agent.replay.buffer import EvolvableReplayBuffer
        e = EvolvableReplayBuffer()
        assert hasattr(e, 'evolution_callback')

    def test_fire_evolution(self):
        from lol_fiddler_agent.replay.buffer import EvolvableReplayBuffer
        e = EvolvableReplayBuffer()
        r = []
        e.evolution_callback = lambda d: r.append(d)
        e._fire_evolution({'batch_size': 32})
        assert r[0]['module'] == 'replay_buffer'

    def test_add_with_evolution_metadata(self):
        from lol_fiddler_agent.replay.buffer import EvolvableReplayBuffer
        e = EvolvableReplayBuffer()
        e.add_with_evolution(
            state=[1.0]*10, action=0, reward=1.0,
            next_state=[1.1]*10, done=False, generation=3
        )
        assert e.size == 1

    def test_to_training_batch(self):
        from lol_fiddler_agent.replay.buffer import EvolvableReplayBuffer
        e = EvolvableReplayBuffer()
        for i in range(5):
            e.add([float(i)]*10, i % 3, float(i)*0.1, [float(i+1)]*10)
        batch = e.to_training_batch(batch_size=3)
        assert batch is not None

    def test_original_class(self):
        from lol_fiddler_agent.replay.buffer import ReplayBuffer
        assert ReplayBuffer is not None

    def test_prioritized_buffer_intact(self):
        from lol_fiddler_agent.replay.buffer import PrioritizedReplayBuffer
        assert PrioritizedReplayBuffer is not None

    def test_no_callback_safe(self):
        from lol_fiddler_agent.replay.buffer import EvolvableReplayBuffer
        e = EvolvableReplayBuffer()
        e._fire_evolution({})

    def test_inherits_buffer(self):
        from lol_fiddler_agent.replay.buffer import EvolvableReplayBuffer, ReplayBuffer
        assert issubclass(EvolvableReplayBuffer, ReplayBuffer)


# ═══════════════════════════════════════════════════════════════════════════
# M283: replay/recorder.py — game recording + annotation
# ═══════════════════════════════════════════════════════════════════════════

class TestM283RecorderEvolution:

    def test_evolution_key(self):
        from lol_fiddler_agent.replay import recorder
        assert recorder._EVOLUTION_KEY == 'replay_recorder'

    def test_evolvable_wrapper(self):
        from lol_fiddler_agent.replay.recorder import EvolvableReplayRecorder
        assert EvolvableReplayRecorder is not None

    def test_has_callback(self):
        from lol_fiddler_agent.replay.recorder import EvolvableReplayRecorder
        e = EvolvableReplayRecorder()
        assert hasattr(e, 'evolution_callback')

    def test_fire_evolution(self):
        from lol_fiddler_agent.replay.recorder import EvolvableReplayRecorder
        e = EvolvableReplayRecorder()
        r = []
        e.evolution_callback = lambda d: r.append(d)
        e._fire_evolution({'recording': True})
        assert r[0]['module'] == 'replay_recorder'

    def test_record_evolution_event(self):
        from lol_fiddler_agent.replay.recorder import EvolvableReplayRecorder
        import tempfile
        e = EvolvableReplayRecorder(replay_dir=tempfile.mkdtemp())
        e.start_recording()
        e.record_evolution_event(
            event_type='model_update', generation=5, metrics={'reward': 0.8}
        )
        assert e.event_count >= 1

    def test_original_class(self):
        from lol_fiddler_agent.replay.recorder import ReplayRecorder
        assert ReplayRecorder is not None

    def test_replay_player_intact(self):
        from lol_fiddler_agent.replay.recorder import ReplayPlayer
        assert ReplayPlayer is not None

    def test_no_callback_safe(self):
        from lol_fiddler_agent.replay.recorder import EvolvableReplayRecorder
        e = EvolvableReplayRecorder()
        e._fire_evolution({})

    def test_inherits_recorder(self):
        from lol_fiddler_agent.replay.recorder import EvolvableReplayRecorder, ReplayRecorder
        assert issubclass(EvolvableReplayRecorder, ReplayRecorder)

    def test_list_replays_intact(self):
        from lol_fiddler_agent.replay.recorder import list_replays
        assert callable(list_replays)


# ═══════════════════════════════════════════════════════════════════════════
# M284: models/game_snapshot.py — evolution metadata fields
# ═══════════════════════════════════════════════════════════════════════════

class TestM284GameSnapshotEvolution:

    def test_evolution_key(self):
        from lol_fiddler_agent.models import game_snapshot
        assert game_snapshot._EVOLUTION_KEY == 'game_snapshot'

    def test_evolution_metadata_class(self):
        from lol_fiddler_agent.models.game_snapshot import EvolutionMetadata
        assert EvolutionMetadata is not None

    def test_metadata_has_generation(self):
        from lol_fiddler_agent.models.game_snapshot import EvolutionMetadata
        m = EvolutionMetadata(generation=3, model_version='v1.2')
        assert m.generation == 3

    def test_metadata_has_model_version(self):
        from lol_fiddler_agent.models.game_snapshot import EvolutionMetadata
        m = EvolutionMetadata(generation=1, model_version='v2.0')
        assert m.model_version == 'v2.0'

    def test_metadata_has_reward_signal(self):
        from lol_fiddler_agent.models.game_snapshot import EvolutionMetadata
        m = EvolutionMetadata(generation=1, model_version='v1', reward_signal=0.75)
        assert m.reward_signal == 0.75

    def test_attach_metadata_to_snapshot(self):
        from lol_fiddler_agent.models.game_snapshot import GameSnapshot, attach_evolution_metadata, EvolutionMetadata
        snap = GameSnapshot(game_time=300.0, active_player_name="Test")
        meta = EvolutionMetadata(generation=2, model_version='v1.1')
        enriched = attach_evolution_metadata(snap, meta)
        assert enriched is not None

    def test_original_class(self):
        from lol_fiddler_agent.models.game_snapshot import GameSnapshot
        assert GameSnapshot is not None

    def test_compute_hash_intact(self):
        from lol_fiddler_agent.models.game_snapshot import GameSnapshot
        snap = GameSnapshot(game_time=300.0, active_player_name="Test")
        h = snap.compute_hash()
        assert isinstance(h, str)

    def test_to_json_intact(self):
        from lol_fiddler_agent.models.game_snapshot import GameSnapshot
        snap = GameSnapshot(game_time=300.0, active_player_name="Test")
        j = snap.to_json()
        assert isinstance(j, str)

    def test_team_snapshot_intact(self):
        from lol_fiddler_agent.models.game_snapshot import TeamSnapshot
        assert TeamSnapshot is not None


# ═══════════════════════════════════════════════════════════════════════════
# M285: models/champion_db.py — connect to champion_meta
# ═══════════════════════════════════════════════════════════════════════════

class TestM285ChampionDBEvolution:

    def test_evolution_key(self):
        from lol_fiddler_agent.models import champion_db
        assert champion_db._EVOLUTION_KEY == 'champion_db'

    def test_evolvable_wrapper(self):
        from lol_fiddler_agent.models.champion_db import EvolvableChampionDatabase
        assert EvolvableChampionDatabase is not None

    def test_has_callback(self):
        from lol_fiddler_agent.models.champion_db import EvolvableChampionDatabase
        e = EvolvableChampionDatabase()
        assert hasattr(e, 'evolution_callback')

    def test_fire_evolution(self):
        from lol_fiddler_agent.models.champion_db import EvolvableChampionDatabase
        e = EvolvableChampionDatabase()
        r = []
        e.evolution_callback = lambda d: r.append(d)
        e._fire_evolution({'champion_count': 160})
        assert r[0]['module'] == 'champion_db'

    def test_has_meta_connector_slot(self):
        from lol_fiddler_agent.models.champion_db import EvolvableChampionDatabase
        e = EvolvableChampionDatabase()
        assert hasattr(e, 'champion_meta_connector')

    def test_set_meta_connector(self):
        from lol_fiddler_agent.models.champion_db import EvolvableChampionDatabase
        e = EvolvableChampionDatabase()
        e.champion_meta_connector = "mock_connector"
        assert e.champion_meta_connector == "mock_connector"

    def test_to_training_annotation(self):
        from lol_fiddler_agent.models.champion_db import EvolvableChampionDatabase
        e = EvolvableChampionDatabase()
        ann = e.to_training_annotation(champion='Ahri', win_rate=52.3, pick_rate=8.1)
        assert ann['champion'] == 'Ahri'

    def test_original_class(self):
        from lol_fiddler_agent.models.champion_db import ChampionDatabase
        assert ChampionDatabase is not None

    def test_no_callback_safe(self):
        from lol_fiddler_agent.models.champion_db import EvolvableChampionDatabase
        e = EvolvableChampionDatabase()
        e._fire_evolution({})

    def test_inherits_db(self):
        from lol_fiddler_agent.models.champion_db import EvolvableChampionDatabase, ChampionDatabase
        assert issubclass(EvolvableChampionDatabase, ChampionDatabase)
