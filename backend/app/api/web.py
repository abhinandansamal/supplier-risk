"""The single-page UI: plain HTML, CSS and JS from app/web/static (no build step, no CDN)."""

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

STATIC_DIR = Path(__file__).resolve().parents[1] / "web" / "static"

router = APIRouter()


@router.get("/", include_in_schema=False)
async def index() -> FileResponse:
    """Return the UI page."""
    return FileResponse(STATIC_DIR / "index.html", media_type="text/html")


def static_files() -> StaticFiles:
    """Return the app for /static (app.js, app.css)."""
    return StaticFiles(directory=STATIC_DIR)
