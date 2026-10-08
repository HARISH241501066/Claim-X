PY := backend/.venv/Scripts/python.exe
ifeq ($(wildcard $(PY)),)
PY := backend/.venv/bin/python
endif

.PHONY: data api web test web-test e2e

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
