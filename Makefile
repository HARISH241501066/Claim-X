PY := backend/.venv/Scripts/python.exe
ifeq ($(wildcard $(PY)),)
PY := backend/.venv/bin/python
endif

.PHONY: data api web test web-test e2e reset coverage

data:
	$(PY) -m backend.data.generator

api:
	$(PY) -m uvicorn backend.api.main:app --reload --port 8000

web:
	npm --prefix frontend run dev

test:
	$(PY) -m pytest
	$(PY) -m ruff check backend

web-test:
	npm --prefix frontend run lint
	npm --prefix frontend test

# needs the API (make api) and the web app (make web) running
e2e:
	npm --prefix frontend run e2e

# the tests with a coverage report for backend/
coverage:
	$(PY) -m pytest --cov=backend --cov-report=term-missing

# a clean, demo-ready state: archives the audit log, regenerates data, reruns the pipeline,
# clears notifications and the brief cache, prewarms the top 5 briefs (add ARGS=--email to send emails)
reset:
	$(PY) -m backend.demo_reset $(ARGS)
