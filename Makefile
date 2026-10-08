PY := backend/.venv/Scripts/python.exe
ifeq ($(wildcard $(PY)),)
PY := backend/.venv/bin/python
endif

.PHONY: data api web test

data:
	@echo "data module not built yet (later module)"

api:
	$(PY) -m uvicorn backend.api.main:app --reload --port 8000

web:
	npm --prefix frontend run dev

test:
	$(PY) -m pytest
	$(PY) -m ruff check backend
