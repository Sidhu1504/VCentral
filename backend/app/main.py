"""vCentral API. Run with:  uvicorn app.main:app"""
import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import func, select

from .api import (activity, agent_api, agents_admin, auth, dashboard, network, onboarding, owners_api, remote_api, reports_api, system,
                  tasks_api, users, vms)
from .config import get_settings
from .db import SessionLocal, init_db
from .models import User
from .security import hash_password

log = logging.getLogger("vcentral")
settings = get_settings()

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
MAX_AGENT_BODY = 8 << 20


def bootstrap_admin() -> None:
    with SessionLocal() as db:
        if db.scalar(select(func.count()).select_from(User)):
            return
        password = settings.admin_password
        if not password:
            password = secrets.token_urlsafe(12)
            log.warning("Created initial admin '%s' with generated password: %s  (change it at first login)",
                        settings.admin_username, password)
        db.add(User(username=settings.admin_username, full_name="Administrator", role="admin",
                    password_hash=hash_password(password), must_change_password=True))
        db.commit()


@asynccontextmanager
async def lifespan(_: FastAPI):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if len(settings.secret_key) < 32:
        raise RuntimeError("VCENTRAL_SECRET_KEY must be set to a random value of at least 32 characters")
    init_db()
    bootstrap_admin()
    yield


app = FastAPI(title="vCentral", version="0.6.1", lifespan=lifespan,
              docs_url="/api/docs", openapi_url="/api/openapi.json", redoc_url=None)


@app.middleware("http")
async def csrf_and_headers(request: Request, call_next):
    path = request.url.path
    if path.startswith("/api/agent/"):
        # Agent protocol: bearer-token auth (no cookies, so no CSRF exposure); cap the body size.
        length = request.headers.get("content-length", "")
        if length.isdigit() and int(length) > MAX_AGENT_BODY:
            return JSONResponse({"detail": "Request too large"}, status_code=413)
    elif (request.method in UNSAFE_METHODS and path.startswith("/api/")
            and request.headers.get("x-requested-with") != "vcentral"):
        # Cookies are SameSite=Strict; additionally require a custom header on state-changing API calls,
        # which a cross-site HTML form cannot send.
        return JSONResponse({"detail": "Missing X-Requested-With header"}, status_code=403)
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    if request.url.path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


for r in (auth.router, dashboard.router, vms.router, network.router, activity.router, users.router,
          system.router, agent_api.router, tasks_api.router, agents_admin.router, onboarding.router,
          owners_api.router, remote_api.router, reports_api.router):
    app.include_router(r)


@app.get("/api/health", include_in_schema=False)
def health():
    return {"status": "ok"}


# -- frontend (single-page app) ----------------------------------------------------------------
_static = Path(settings.static_dir).resolve() if settings.static_dir else None
if _static and (_static / "index.html").is_file():

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith("api/"):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        candidate = (_static / path).resolve()
        if path and candidate.is_file() and candidate.is_relative_to(_static):
            return FileResponse(candidate)
        return FileResponse(_static / "index.html", headers={"Cache-Control": "no-cache"})
