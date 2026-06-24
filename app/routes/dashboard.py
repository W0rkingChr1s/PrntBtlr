"""Dashboard: at-a-glance status of printers, jobs, scans and services."""

from __future__ import annotations

from fastapi import APIRouter, Request

from ..services import cups, scan, system
from ..templating import render

router = APIRouter()


@router.get("/")
def dashboard(request: Request):
    cups_status = cups.status()
    return render(
        request,
        "dashboard.html",
        nav_active="dashboard",
        cups=cups_status,
        services=system.services(),
        host=system.host_info(),
        scans=scan.list_scans()[:5],
        scan_available=scan.available(),
    )


@router.get("/partials/services")
def services_partial(request: Request):
    """Polled fragment so the dashboard badges update without a full reload."""
    return render(
        request,
        "partials/services.html",
        services=system.services(),
    )


@router.get("/partials/jobs")
def jobs_partial(request: Request):
    return render(request, "partials/jobs.html", jobs=cups.list_jobs())
