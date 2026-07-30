"""Dashboard: at-a-glance status of printers, jobs, scans and services."""

from __future__ import annotations

from fastapi import APIRouter, Request

from ..services import cups, scan, shell, system
from ..templating import render

router = APIRouter()


@router.get("/")
def dashboard(request: Request):
    # These four are independent and each blocks on its own shell-outs; running
    # them concurrently makes the dashboard load in about the time of its
    # slowest source rather than the sum of all four.
    cups_status, services, host, scans = shell.gather(
        [
            cups.status,
            system.services,
            system.host_info,
            lambda: scan.list_scans()[:5],
        ]
    )
    return render(
        request,
        "dashboard.html",
        nav_active="dashboard",
        cups=cups_status,
        services=services,
        host=host,
        scans=scans,
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
