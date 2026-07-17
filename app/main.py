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
from .services import health, repair, system, updater

log = logging.getLogger("prntbtlr")
logging.basicConfig(level=logging.DEBUG if settings.debug else logging.INFO)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Long-running background workers, cancelled cleanly on shutdown.
    tasks: list[asyncio.Task] = []
    # Update checker (channel + auto/notify are set on the System page);
    # PRNTBTLR_UPDATE_CHECK_INTERVAL=0 disables it.
    if settings.update_check_interval > 0:
        tasks.append(asyncio.create_task(updater.background_loop()))
    # Optional autonomous self-repair (PRNTBTLR_SELF_REPAIR_ENABLED=1).
    if settings.self_repair_enabled:
        log.info(
            "self-repair: background sweeps enabled (every %ss)", settings.self_repair_interval
        )
        tasks.append(asyncio.create_task(repair.background_loop()))
    yield
    for task in tasks:
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
    # Includes the systemd units from the dashboard so external monitoring
    # (e.g. PRTG's REST Custom / HTTP Data Advanced sensors) can build one
    # channel per service off this endpoint. ``value`` is 1 when the unit is
    # active, 0 otherwise — keyed by unit name for stable JSONPath lookups.
    svc = system.services()
    report = health.run_checks()
    return JSONResponse(
        {
            "status": "ok",
            "app": settings.app_name,
            "version": __version__,
            "services": {
                s.name: {
                    "status": s.status,
                    "active": s.active,
                    "enabled": s.enabled,
                    "value": 1 if s.active else 0,
                }
                for s in svc
            },
            "services_active": sum(1 for s in svc if s.active),
            "services_total": len(svc),
            # The control-instance verdicts. ``value`` is 1 for a healthy check
            # (ok/skip), 0 otherwise, so monitoring can alert per check.
            "health": {
                "overall": report.overall,
                "ok": report.count(health.OK),
                "warn": report.count(health.WARN),
                "fail": report.count(health.FAIL),
                "repairable": len(report.repairable),
                "checks": {
                    c.key: {
                        "title": c.title,
                        "status": c.status,
                        "detail": c.detail,
                        "repairable": c.repairable,
                        "value": 0 if c.status in (health.WARN, health.FAIL) else 1,
                    }
                    for c in report.checks
                },
            },
        }
    )


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
