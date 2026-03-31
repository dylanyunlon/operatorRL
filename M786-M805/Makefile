.PHONY: test clean lint

test:
	python3 run_all_tests.py

clean:
	find . -name "*.db" -delete
	find . -name "__pycache__" -exec rm -rf {} +
	find . -name "*.pyc" -delete

lint:
	python3 -m py_compile logging_system/core_logger.py
	python3 -m py_compile historical_battle_data/historical_battle_data.py
	@echo "Lint passed"
