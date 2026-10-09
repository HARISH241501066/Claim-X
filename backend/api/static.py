"""Serve the built web app from the API, so one address (and one deployment) holds everything.

Only used when asked for (CLAIMX_SERVE_FRONTEND=true, as in the Docker image), so a developer
running `make api` and `make web` is never surprised by a stale build. A browser opening a page
(Accept: text/html) gets the app; the app's own calls (Accept: application/json) reach the API, so
the web app's /queue page and the API's /queue endpoint can share a name without clashing.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse

log = logging.getLogger("claimx.api")
DEFAULT_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
API_PAGES = {"/docs", "/redoc", "/openapi.json", "/health"}  # always the API, even for a browser
LONG_CACHE = {"Cache-Control": "public, max-age=31536000, immutable"}  # hashed files in /assets
NO_CACHE = {"Cache-Control": "no-store"}  # the page that points at them


def _inside(dist: Path, requested: str) -> Path | None:
    """The file under dist for this URL path, or None (never one outside dist)."""
    candidate = (dist / requested.lstrip("/")).resolve()
    try:
        candidate.relative_to(dist.resolve())
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


def serve_frontend(app: FastAPI, dist: Path = DEFAULT_DIST) -> bool:
    """Add the web app to `app`. Returns False (and serves nothing) if it was never built."""
    index = dist / "index.html"
    if not index.is_file():
        log.warning("the web app is not built (%s is missing): serving the API only", index)
        return False

    @app.middleware("http")
    async def web_app(request: Request, call_next):
        if request.method in {"GET", "HEAD"}:
            path = request.url.path
            asset = _inside(dist, path) if path != "/" else None
            if asset is not None:
                headers = LONG_CACHE if path.startswith("/assets/") else NO_CACHE
                return FileResponse(asset, headers=headers)
            wants_page = "text/html" in request.headers.get("accept", "")
            if wants_page and path not in API_PAGES:
                return FileResponse(index, headers=NO_CACHE)  # the app decides what to show
        return await call_next(request)

    log.info("serving the web app from %s", dist)
    return True
