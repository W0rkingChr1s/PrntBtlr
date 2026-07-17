"""System page: service control, host information and updates."""

from __future__ import annotations

from fastapi import APIRouter, Form, Request

from ..config import settings
from ..services import system, updater
from ..templating import redirect, render

router = APIRouter(prefix="/system")


@router.get("")
def system_page(request: Request):
    return render(
        request,
        "system.html",
        nav_active="system",
        services=system.services(),
        host=system.host_info(),
        updates=updater.status(),
        update_repo=settings.update_repo,
    )


@router.post("/services/{name}/restart")
def restart(name: str):
    res = system.restart_service(name)
    msg = f"Restarted {name}." if res.ok else f"Failed to restart {name}: {res.output}"
    return redirect("/system", msg, "success" if res.ok else "error")


@router.post("/updates/settings")
def update_settings(beta: str = Form(""), auto: str = Form("")):
    channel = "beta" if beta else "stable"
    try:
        updater.save_prefs(channel=channel, auto_update=bool(auto))
    except OSError as exc:
        return redirect("/system", f"Could not save update settings: {exc}", "error")
    mode = "installed automatically" if auto else "notify only"
    return redirect("/system", f"Update settings saved: {channel} channel, {mode}.")


@router.post("/updates/check")
def update_check():
    st = updater.check_for_update()
    if st.last_error:
        return redirect("/system", f"Update check failed: {st.last_error}", "error")
    if st.available:
        return redirect("/system", f"Update available: {st.available['tag']}.")
    return redirect("/system", f"You are up to date (v{st.current}).")


@router.post("/updates/apply")
def update_apply(tag: str = Form(...)):
    st = updater.status()
    if not st.available or st.available.get("tag") != tag:
        return redirect("/system", "That update is no longer available — check again.", "error")
    res = updater.start_update(tag)
    if res.ok:
        return redirect("/system", f"Updating to {tag} — the panel will restart shortly.")
    return redirect("/system", f"Update could not start: {res.output}", "error")
