"""Shared Jinja2 environment and helpers (flash messages, common context)."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlencode

from fastapi import Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from . import __version__
from .config import settings

TEMPLATE_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))
templates.env.globals.update(app_name=settings.app_name, version=__version__)


def render(request: Request, name: str, **context):
    """Render *name* with the common context merged in."""
    base = {
        "request": request,
        "tagline": settings.tagline,
        "flash": _read_flash(request),
        "nav_active": context.pop("nav_active", ""),
    }
    base.update(context)
    return templates.TemplateResponse(request, name, base)


def redirect(path: str, message: str = "", level: str = "success") -> RedirectResponse:
    """Redirect (303) carrying an optional one-shot flash message in the query."""
    if message:
        path = f"{path}?{urlencode({'msg': message, 'level': level})}"
    return RedirectResponse(path, status_code=303)


def _read_flash(request: Request) -> dict | None:
    msg = request.query_params.get("msg")
    if not msg:
        return None
    return {"message": msg, "level": request.query_params.get("level", "success")}
