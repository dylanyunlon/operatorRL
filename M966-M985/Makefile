# M966-M985 Makefile
.PHONY: test lint generate clean

test:
	python -m pytest tests/ -v

lint:
	python -m py_compile generate_all_modules.py

generate:
	python generate_all_modules.py

clean:
	rm -rf logs/*.log logs/*.jsonl __pycache__
