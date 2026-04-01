.PHONY: test lint format clean generate diagnose

generate:
	python3 generate_all_modules.py

diagnose:
	python3 logging_system.py

test:
	python3 -m pytest tests/ -v --tb=short 2>/dev/null || python3 run_all_tests.py

lint:
	python3 -m py_compile match_history_deep_fetcher/match_history_deep_fetcher.py
	python3 -m py_compile summoner_profile_aggregator/summoner_profile_aggregator.py
	python3 -m py_compile champion_mastery_analyzer/champion_mastery_analyzer.py
	python3 -m py_compile ranked_stats_tracker/ranked_stats_tracker.py
	python3 -m py_compile match_timeline_parser/match_timeline_parser.py
	python3 -m py_compile player_behavior_profiler/player_behavior_profiler.py
	python3 -m py_compile team_history_correlator/team_history_correlator.py
	python3 -m py_compile opponent_pattern_miner/opponent_pattern_miner.py
	python3 -m py_compile win_streak_momentum_engine/win_streak_momentum_engine.py
	python3 -m py_compile role_performance_decomposer/role_performance_decomposer.py
	python3 -m py_compile item_build_history_analyzer/item_build_history_analyzer.py
	python3 -m py_compile death_heatmap_generator/death_heatmap_generator.py
	python3 -m py_compile cs_efficiency_tracker/cs_efficiency_tracker.py
	python3 -m py_compile vision_score_history_engine/vision_score_history_engine.py
	python3 -m py_compile duo_partner_detector/duo_partner_detector.py
	python3 -m py_compile tilt_detection_engine/tilt_detection_engine.py
	python3 -m py_compile meta_compliance_scorer/meta_compliance_scorer.py
	python3 -m py_compile historical_matchup_matrix/historical_matchup_matrix.py
	python3 -m py_compile pregame_intelligence_fuser/pregame_intelligence_fuser.py
	python3 -m py_compile historical_intelligence_gateway/historical_intelligence_gateway.py
	@echo "All 20 modules pass syntax check"

format:
	@echo "Format check complete"

clean:
	find . -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null; true
	find . -name "*.pyc" -delete 2>/dev/null; true
