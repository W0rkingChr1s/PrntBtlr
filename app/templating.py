"""Shared Jinja2 environment and helpers (flash messages, common context)."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlencode

from fastapi import Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from . import __version__
from .config import settings
from .services import features, updater

TEMPLATE_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))
templates.env.globals.update(
    app_name=settings.app_name,
    version=__version__,
    auth_enabled=settings.auth_enabled,
)


def render(request: Request, name: str, **context):
    """Render *name* with the common context merged in."""
    base = {
        "request": request,
        "tagline": settings.tagline,
        "flash": _read_flash(request),
        "nav_active": context.pop("nav_active", ""),
        # Which top-level functions are switched on (drives the navigation).
        "features": features.states(),
        # Cached "update available" notice (reads a tiny state file, no network).
        # Suppressed when the self-update function is switched off.
        "update_notice": updater.notice() if features.is_enabled("updates") else None,
    }
    base.update(context)
    return templates.TemplateResponse(request, name, base)


def redirect(path: str, message: str = "", level: str = "success") -> RedirectResponse:
    """Redirect (303) carrying an optional one-shot flash message in the query."""
    if message:
        # The query must precede any #fragment (e.g. /system#health), or the
        # browser treats it as part of the fragment and the flash never arrives.
        base, _, fragment = path.partition("#")
        path = f"{base}?{urlencode({'msg': message, 'level': level})}"
        if fragment:
            path += f"#{fragment}"
    return RedirectResponse(path, status_code=303)


def _read_flash(request: Request) -> dict | None:
    msg = request.query_params.get("msg")
    if not msg:
        return None
    return {"message": msg, "level": request.query_params.get("level", "success")}
