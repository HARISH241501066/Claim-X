# Deploying Claim-X

One container serves the API and the built web app from a single address, so there is no cross-site setup. All data is synthetic.

## Fastest: Render (about 10 minutes)

| Step | Do |
|---|---|
| 1 | Push the repository to GitHub (already done) |
| 2 | On render.com choose **New + → Blueprint** and select this repository |
| 3 | Render reads `render.yaml`, builds the `Dockerfile` and creates the service `claim-x` |
| 4 | When asked, enter **DEMO_PASSWORD** (the password for the demo users; 8+ characters). `JWT_SECRET` is generated for you |
| 5 | Wait for the first deploy (the first start takes about a minute while it builds the data and models) |
| 6 | Open the `https://claim-x.onrender.com`-style address Render shows and sign in as `admin`, `south_lead` or `south_inv1` |

## Other hosts

| Host | How |
|---|---|
| Railway, Fly.io, Google Cloud Run, Azure Container Apps | Deploy the `Dockerfile` from the repository; set the settings below as environment variables; the container listens on `$PORT` (default 8000) |
| Any server with Docker | `docker build -t claim-x .` then `docker run -p 8000:8000 -e JWT_SECRET=... -e DEMO_PASSWORD=... claim-x` |

## Settings on the host

| Setting | Value | Required |
|---|---|---|
| `JWT_SECRET` | 32+ random characters, new for each deployment | yes |
| `DEMO_PASSWORD` | A password you choose for the demo users | yes |
| `CLAIMX_SERVE_FRONTEND` | `true` (the Dockerfile sets it) | set by the image |
| `LLM_PROVIDER` | `none` (default) or `groq` with `LLM_API_KEY` | no |
| `NOTIFY_EMAIL_ENABLED`, `SNS_TOPIC_ARN`, `AWS_REGION`, `APP_BASE_URL`, AWS keys | Only to send the urgent-case emails; `APP_BASE_URL` should be the public address | no |
| `CORS_ORIGINS` | Only if the web app is hosted at a different address from the API | no |

Never put these in git. Enter them in the host's dashboard.

## Before sharing the link

| Check | Why |
|---|---|
| Use a password you don't mind sharing | Anyone with the link and the password can sign in as a demo user |
| Keep `LLM_PROVIDER=none` unless you want the key used | A public site could spend your LLM quota |
| Know that free hosts forget files | The audit log, decisions and users live in SQLite; a restart resets them to the clean demo state (users are re-created from `DEMO_PASSWORD`) |
| Give it about 1 GB of memory | pandas and scikit-learn; very small plans may run out |
| Wait for the first start | `/health` answers `ready` after about 10 to 60 seconds |

## Checking it works

| Check | Expected |
|---|---|
| Open the address | The Claim-X login page |
| Open `/health` | `"ready": true` |
| Sign in as `south_lead`, open **Unit Queue** | The unit's cases, with evidence and briefs |
| Open `/docs` | The API documentation |

## Trying the same setup on your own machine (no Docker)

```powershell
cd frontend
$env:VITE_API_URL = ""; npm run build
cd ..
$env:CLAIMX_SERVE_FRONTEND = "true"
backend\.venv\Scripts\python -m uvicorn backend.api.main:app --port 8020
```
Then open http://localhost:8020. Pages come from the app and the app's own calls reach the API on the same address.
