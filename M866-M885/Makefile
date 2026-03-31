.PHONY: test lint check all
all: check test
check:
	python3 -c "import ast,os;[ast.parse(open(os.path.join(r,f)).read()) for r,d,fs in os.walk('.') for f in fs if f.endswith('.py')]"
	@echo "All syntax OK"
test:
	python3 run_all_tests.py
lint:
	python3 -m py_compile generate_all_modules.py
