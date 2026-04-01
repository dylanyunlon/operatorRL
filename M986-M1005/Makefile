.PHONY: test lint clean

test:
	python3 generate_all_modules.py

lint:
	python3 -m py_compile logging_system.py

clean:
	rm -rf logs/*.log logs/*.json __pycache__ */__pycache__
