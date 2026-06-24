"""System page: service control and host information."""

from __future__ import annotations

from fastapi import APIRouter, Request

from ..services import system
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
    )


@router.post("/services/{name}/restart")
def restart(name: str):
    res = system.restart_service(name)
    msg = f"Restarted {name}." if res.ok else f"Failed to restart {name}: {res.output}"
    return redirect("/system", msg, "success" if res.ok else "error")
