"""Scanning UI: devices, ad-hoc scans, and the saved-scan library."""

from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import FileResponse

from ..config import settings
from ..services import scan
from ..templating import redirect, render

router = APIRouter(prefix="/scans")


@router.get("")
def scans_page(request: Request):
    return render(
        request,
        "scans.html",
        nav_active="scans",
        available=scan.available(),
        ocr_available=scan.ocr_available(),
        devices=scan.list_devices() if scan.available() else [],
        paper_choices=scan.PAPER_CHOICES,
        paper_default=settings.scan_paper,
        scans=scan.list_scans(),
    )


@router.post("/new")
def new_scan(
    device: str = Form(""),
    source: str = Form("Flatbed"),
    mode: str = Form("Color"),
    resolution: int = Form(300),
    paper: str = Form(""),
    ocr: bool = Form(False),
):
    ok, message, _ = scan.scan_now(
        device=device or None,
        source=source,
        mode=mode,
        resolution=resolution,
        paper=paper or None,
        ocr=ocr,
    )
    return redirect("/scans", message, "success" if ok else "error")


@router.get("/file/{name}")
def download_scan(name: str):
    path = scan.resolve_scan(name)
    if path is None:
        return redirect("/scans", "Scan not found.", "error")
    return FileResponse(path, media_type="application/pdf", filename=path.name)


@router.get("/view/{name}")
def view_scan(name: str):
    path = scan.resolve_scan(name)
    if path is None:
        return redirect("/scans", "Scan not found.", "error")
    # Inline disposition so the browser renders the PDF instead of downloading it.
    return FileResponse(path, media_type="application/pdf")


@router.post("/file/{name}/delete")
def delete_scan(name: str):
    ok = scan.delete_scan(name)
    return redirect(
        "/scans",
        f"Deleted {name}." if ok else "Could not delete scan.",
        "success" if ok else "error",
    )
