# One image that serves the API and the built web app from a single address.
#   docker build -t claim-x .
#   docker run -p 8000:8000 -e JWT_SECRET=... -e DEMO_PASSWORD=... claim-x

# ---- 1. build the web app (relative API address: same origin) ----
FROM node:22-alpine AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
ENV VITE_API_URL=
RUN npm run build

# ---- 2. the API, with the built web app next to it ----
FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 CLAIMX_SERVE_FRONTEND=true
WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt
COPY backend/ backend/
COPY pyproject.toml ./
COPY --from=web /web/dist frontend/dist
# keep the demo database and the audit log on a mounted volume if you want them to survive restarts
RUN useradd --create-home app && chown -R app /app
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s CMD python -c "import urllib.request,os; urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8000')+'/health')"
# JWT_SECRET and DEMO_PASSWORD must be provided by the host; nothing secret is baked in
CMD ["sh", "-c", "exec uvicorn backend.api.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
