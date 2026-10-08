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
| Tests + lint | `make test` | `.\make.ps1 test` |
| Data | `make data` | `.\make.ps1 data` |
