.PHONY: test lint format clean

test:
	python -m pytest -xvs .

lint:
	python -m flake8 --max-line-length=120 .

format:
	python -m black --line-length=120 .

clean:
	find . -name __pycache__ -exec rm -rf {} +
	find . -name "*.pyc" -delete
