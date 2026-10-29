PY ?= python

.PHONY: install lint format test demo report dashboard clean

install:
	$(PY) -m pip install -r requirements-dev.txt
	$(PY) -m pip install -e .

lint:
	ruff check .
	ruff format --check .

format:
	ruff check --fix .
	ruff format .

test:
	pytest --cov=ipa --cov-report=term-missing

demo:
	ipa demo --reset

report:
	ipa weekly-report

dashboard:
	streamlit run src/ipa/dashboard/app.py

clean:
	rm -rf .pytest_cache .ruff_cache .coverage htmlcov output data/*.db
