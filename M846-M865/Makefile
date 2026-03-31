.PHONY: test clean

test:
	python3 run_all_tests.py

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null; true
	find . -type d -name cache -exec rm -rf {} + 2>/dev/null; true
