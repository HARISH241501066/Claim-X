# ClaimShield Nexus

Healthcare payer FWA platform (synthetic data only). The system recommends and explains; a human reviewer always decides.

## Setup
```
python3.11 -m venv backend/.venv
backend/.venv/Scripts/python -m pip install -r backend/requirements.txt   # Windows
npm --prefix frontend install
cp .env.example .env
```

## Run
| Target | Linux/Mac | Windows (no make) |
|---|---|---|
| API (http://localhost:8000/health) | `make api` | `.\make.ps1 api` |
| Web (http://localhost:5173) | `make web` | `.\make.ps1 web` |
| Backend tests + lint | `make test` | `.\make.ps1 test` |
| Frontend lint + unit tests | `make web-test` | `.\make.ps1 web-test` |
| End-to-end run in Chrome (API and web must be running) | `make e2e` | `.\make.ps1 e2e` |
| Data | `make data` | `.\make.ps1 data` |

## Using the workbench
Start the API (`make api`, about 5 seconds to build the data and analysis) and the web app (`make web`), then open http://localhost:5173.
Overview shows the headline numbers, Queue ranks cases against your team's hours, and each case opens with its evidence, network, timeline, risk estimate and brief. Every decision or priority change needs your name and a reason and is written to the audit log (`backend/audit.db`; set `CLAIMSHIELD_AUDIT_PATH` to use another file). Set `LLM_API_KEY` to let Claude write briefs; without it, or if its text fails validation, the built-in template is used.
