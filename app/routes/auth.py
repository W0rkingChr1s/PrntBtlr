"""Login / logout routes (only meaningful when auth is enabled)."""

from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from ..auth import check_credentials, throttle
from ..config import settings
from ..templating import render

router = APIRouter()


def _safe_next(target: str | None) -> str:
    """Only allow same-site relative redirects (no open redirect).

    ``//host`` is protocol-relative, and browsers normalize ``/\\host`` to the
    same thing, so both are rejected.
    """
    if target and target.startswith("/") and not target.startswith(("//", "/\\")):
        return target
    return "/"


@router.get("/login")
def login_form(request: Request, next: str = "/"):
    # Already signed in? Go straight through.
    if request.session.get("user"):
        return RedirectResponse(_safe_next(next), status_code=303)
    return render(request, "login.html", next=_safe_next(next), error=None, hide_nav=True)


@router.post("/login")
def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form("/"),
):
    client = request.client.host if request.client else "unknown"
    wait = throttle.retry_after(client)
    if wait:
        # Blocked clients don't even get their credentials checked.
        return render(
            request,
            "login.html",
            next=_safe_next(next),
            error=f"Too many failed attempts — try again in {wait}s.",
            hide_nav=True,
        )
    if check_credentials(username.strip(), password):
        throttle.note_success(client)
        request.session["user"] = username.strip()
        return RedirectResponse(_safe_next(next), status_code=303)
    throttle.note_failure(client)
    return render(
        request,
        "login.html",
        next=_safe_next(next),
        error="Invalid username or password.",
        hide_nav=True,
    )


@router.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@router.post("/logout")
def logout_post(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


def auth_enabled() -> bool:
    return settings.auth_enabled
