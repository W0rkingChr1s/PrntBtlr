"""PrntBtlr application entry point."""

from __future__ import annotations

import asyncio
import logging
import secrets
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from . import __version__
from .auth import auth_is_usable
from .config import settings
from .routes import auth as auth_routes
from .routes import dashboard, printers, scans, system_routes
from .services import updater

log = logging.getLogger("prntbtlr")
logging.basicConfig(level=logging.DEBUG if settings.debug else logging.INFO)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Background update checker (channel + auto/notify are set on the System
    # page); PRNTBTLR_UPDATE_CHECK_INTERVAL=0 disables it.
    task = None
    if settings.update_check_interval > 0:
        task = asyncio.create_task(updater.background_loop())
    yield
    if task:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


app = FastAPI(title=settings.app_name, version=__version__, lifespan=lifespan)

# Paths reachable without a session even when auth is on.
_PUBLIC_PREFIXES = ("/login", "/logout", "/static", "/healthz", "/favicon")

if settings.auth_enabled:
    if not auth_is_usable():
        log.warning(
            "PRNTBTLR_AUTH_ENABLED is set but no password is configured — "
            "the panel will stay LOCKED. Set PRNTBTLR_AUTH_PASSWORD (or _HASH)."
        )

    # Registered FIRST so it ends up INNERMOST: SessionMiddleware (added last,
    # below) wraps it, so request.session is already populated here.
    @app.middleware("http")
    async def require_login(request: Request, call_next):
        path = request.url.path
        if path.startswith(_PUBLIC_PREFIXES) or request.session.get("user"):
            return await call_next(request)
        nxt = request.url.path
        if request.url.query:
            nxt += f"?{request.url.query}"
        return RedirectResponse(f"/login?next={nxt}", status_code=303)

    secret = settings.session_secret or secrets.token_urlsafe(48)
    if not settings.session_secret:
        log.warning(
            "auth enabled without PRNTBTLR_SESSION_SECRET — using an ephemeral key; "
            "sessions will reset on restart. Set a stable secret in production."
        )
    # Added LAST → outermost in the stack → runs before require_login.
    app.add_middleware(
        SessionMiddleware,
        secret_key=secret,
        max_age=settings.session_max_age,
        same_site="lax",
        https_only=False,
    )

    app.include_router(auth_routes.router)

STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

app.include_router(dashboard.router)
app.include_router(printers.router)
app.include_router(scans.router)
app.include_router(system_routes.router)


@app.get("/healthz")
def healthz():
    return JSONResponse({"status": "ok", "app": settings.app_name, "version": __version__})


def run() -> None:
    """Console-script entry point (``prntbtlr``)."""
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
    )


if __name__ == "__main__":
    run()
