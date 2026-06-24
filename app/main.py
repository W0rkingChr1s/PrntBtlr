"""PrntBtlr application entry point."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .config import settings
from .routes import dashboard, printers, scans, system_routes

logging.basicConfig(level=logging.DEBUG if settings.debug else logging.INFO)

app = FastAPI(title=settings.app_name, version=__version__)

STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

app.include_router(dashboard.router)
app.include_router(printers.router)
app.include_router(scans.router)
app.include_router(system_routes.router)


@app.get("/healthz")
def healthz():
    return JSONResponse({"status": "ok", "app": settings.app_name, "version": __version__})


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
