"""Printer & print-job management (CUPS)."""

from __future__ import annotations

from fastapi import APIRouter, Form, Request

from ..services import cups
from ..templating import redirect, render

router = APIRouter(prefix="/printers")


@router.get("")
def printers_page(request: Request):
    status = cups.status()
    return render(
        request,
        "printers.html",
        nav_active="printers",
        cups=status,
        dup_serials=cups.duplicate_device_serials(status.printers),
    )


@router.get("/add")
def add_form(request: Request):
    return render(
        request,
        "printer_add.html",
        nav_active="printers",
        devices=cups.list_devices(),
        drivers=cups.list_drivers(),
    )


@router.post("/add")
def add_printer(
    request: Request,
    name: str = Form(...),
    uri: str = Form(...),
    ppd: str = Form(...),
    shared: bool = Form(False),
    retry: bool = Form(True),
):
    name = name.strip()
    if not cups.is_valid_printer_name(name):
        return redirect(
            "/printers/add",
            "Invalid name. Use letters, digits, '.', '_' or '-' (no spaces, no leading '-').",
            "error",
        )
    res = cups.add_printer(name, uri.strip(), ppd.strip(), shared=shared, retry=retry)
    if res.ok:
        return redirect("/printers", f"Printer '{name}' created.")
    return redirect("/printers/add", f"Failed to add printer: {res.output}", "error")


@router.post("/{name}/delete")
def delete_printer(name: str):
    res = cups.delete_printer(name)
    if res.ok:
        return redirect("/printers", f"Deleted '{name}'.")
    return redirect("/printers", f"Could not delete '{name}': {res.output}", "error")


@router.post("/{name}/enable")
def enable_printer(name: str):
    res = cups.set_enabled(name, True)
    msg = f"Resumed '{name}'." if res.ok else f"Failed: {res.output}"
    return redirect("/printers", msg, "success" if res.ok else "error")


@router.post("/{name}/disable")
def disable_printer(name: str):
    res = cups.set_enabled(name, False)
    msg = f"Paused '{name}'." if res.ok else f"Failed: {res.output}"
    return redirect("/printers", msg, "success" if res.ok else "error")


@router.post("/{name}/error-policy")
def error_policy(name: str, policy: str = Form(...)):
    res = cups.set_error_policy(name, policy)
    msg = f"Error policy for '{name}' set to {policy}." if res.ok else res.output
    return redirect("/printers", msg, "success" if res.ok else "error")


@router.post("/{name}/test")
def test_page(name: str):
    res = cups.print_test_page(name)
    msg = f"Test page sent to '{name}'." if res.ok else f"Failed: {res.output}"
    return redirect("/printers", msg, "success" if res.ok else "error")


@router.post("/{name}/cancel-all")
def cancel_all(name: str):
    res = cups.cancel_all(name)
    msg = f"Cleared the queue for '{name}'." if res.ok else res.output
    return redirect("/printers", msg, "success" if res.ok else "error")


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str):
    res = cups.cancel_job(job_id)
    msg = f"Cancelled job {job_id}." if res.ok else res.output
    return redirect("/printers", msg, "success" if res.ok else "error")


@router.post("/sharing")
def sharing(enabled: bool = Form(...)):
    res = cups.set_sharing(enabled)
    state = "enabled" if enabled else "disabled"
    msg = f"Printer sharing (AirPrint) {state}." if res.ok else res.output
    return redirect("/printers", msg, "success" if res.ok else "error")
