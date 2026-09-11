from __future__ import annotations

import base64
import binascii
import mimetypes
import re
import threading
import time
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"
UPLOAD_DIR = APP_DIR / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

DATA_URL_RE = re.compile(r"^data:(?P<mime>[^;,]+)?(?:;charset=[^;,]+)?;base64,(?P<data>.*)$", re.DOTALL)


class PayloadRequest(BaseModel):
    """Payload accepted by /payload.

    Send either:
      - {"path": "/path/to/file.png"}
      - {"base64": "...", "mime_type": "image/png", "filename": "image.png"}
      - {"data_url": "data:image/png;base64,...", "filename": "image.png"}
      - {"text": "a,b\n1,2", "mime_type": "text/csv", "filename": "table.csv"}
    """

    path: str | None = None
    base64: str | None = Field(default=None, description="Raw base64 payload, without data: prefix")
    data_url: str | None = Field(default=None, description="A data URL such as data:image/png;base64,...")
    text: str | None = None
    mime_type: str | None = None
    filename: str | None = None
    title: str | None = None
    autoplay: bool = True
    loop: bool | None = None


class ControlRequest(BaseModel):
    command: Literal["start", "stop", "loop"]
    loop: bool | None = None


class BackgroundRequest(BaseModel):
    color: str = Field(default="#111111")


class ZoomRequest(BaseModel):
    level: float | None = Field(default=None, description="Absolute zoom level, e.g. 1.25")
    delta: float | None = Field(default=None, description="Relative zoom delta, e.g. 0.1")


class DashboardState:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.version = 0
        self.media_id: str | None = None
        self.source_type: str | None = None
        self.path: str | None = None
        self.filename: str | None = None
        self.mime_type: str | None = None
        self.title: str | None = None
        self.playback: Literal["playing", "stopped"] = "stopped"
        self.loop: bool = False
        self.background: str = "#111111"
        self.zoom: float = 1.0
        self.updated_at = time.time()

    def bump(self) -> None:
        self.version += 1
        self.updated_at = time.time()

    def public(self) -> dict[str, Any]:
        with self.lock:
            return {
                "version": self.version,
                "media_id": self.media_id,
                "has_payload": self.path is not None,
                "source_type": self.source_type,
                "filename": self.filename,
                "mime_type": self.mime_type,
                "title": self.title,
                "playback": self.playback,
                "loop": self.loop,
                "background": self.background,
                "zoom": self.zoom,
                "media_url": f"/media/current?v={self.version}" if self.path else None,
                "updated_at": self.updated_at,
            }


state = DashboardState()

app = FastAPI(
    title="View Files Dashboard",
    description="Self-contained dashboard for displaying images, audio, video, tables, PDFs, text, and other media files.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def _guess_mime(path: str | Path, explicit: str | None = None) -> str:
    if explicit:
        return explicit
    guessed, _ = mimetypes.guess_type(str(path))
    return guessed or "application/octet-stream"


def _safe_suffix(filename: str | None, mime_type: str | None) -> str:
    if filename:
        suffix = Path(filename).suffix
        if suffix:
            return suffix[:32]
    if mime_type:
        guessed = mimetypes.guess_extension(mime_type)
        if guessed:
            return guessed
    return ".bin"


def _decode_base64(value: str) -> bytes:
    cleaned = "".join(value.strip().split())
    try:
        return base64.b64decode(cleaned, validate=True)
    except binascii.Error as exc:
        raise HTTPException(status_code=400, detail=f"Invalid base64 payload: {exc}") from exc


def _store_bytes(data: bytes, filename: str | None, mime_type: str | None) -> Path:
    suffix = _safe_suffix(filename, mime_type)
    output_path = UPLOAD_DIR / f"payload_{int(time.time() * 1000)}{suffix}"
    output_path.write_bytes(data)
    return output_path


def _set_payload(
    *,
    file_path: Path,
    source_type: str,
    filename: str | None,
    mime_type: str | None,
    title: str | None,
    autoplay: bool,
    loop: bool | None,
    original_path: str | None = None,
    bump: bool = True,
) -> dict[str, Any]:
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail=f"Media file not found: {file_path}")

    resolved = file_path.resolve()
    final_mime = _guess_mime(resolved, mime_type)
    with state.lock:
        state.media_id = str(int(time.time() * 1000))
        state.source_type = source_type
        state.path = str(resolved)
        state.filename = filename or resolved.name
        state.mime_type = final_mime
        state.title = title or state.filename
        state.playback = "playing" if autoplay else "stopped"
        if loop is not None:
            state.loop = bool(loop)
        if bump:
            state.bump()
        return state.public()


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    index_path = STATIC_DIR / "index.html"
    return HTMLResponse(index_path.read_text(encoding="utf-8"))


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/state")
def get_state() -> dict[str, Any]:
    return state.public()


@app.post("/payload")
def set_payload(payload: PayloadRequest) -> dict[str, Any]:
    provided = [payload.path is not None, payload.base64 is not None, payload.data_url is not None, payload.text is not None]
    if sum(provided) != 1:
        raise HTTPException(
            status_code=400,
            detail="Provide exactly one payload source: path, base64, data_url, or text.",
        )

    if payload.path is not None:
        path = Path(payload.path).expanduser()
        return _set_payload(
            file_path=path,
            source_type="path",
            filename=payload.filename,
            mime_type=payload.mime_type,
            title=payload.title,
            autoplay=payload.autoplay,
            loop=payload.loop,
            original_path=payload.path,
        )

    if payload.data_url is not None:
        match = DATA_URL_RE.match(payload.data_url.strip())
        if not match:
            raise HTTPException(status_code=400, detail="Invalid data_url. Expected data:<mime>;base64,<payload>.")
        mime_type = payload.mime_type or match.group("mime") or "application/octet-stream"
        data = _decode_base64(match.group("data"))
        path = _store_bytes(data, payload.filename, mime_type)
        return _set_payload(
            file_path=path,
            source_type="data_url",
            filename=payload.filename or path.name,
            mime_type=mime_type,
            title=payload.title,
            autoplay=payload.autoplay,
            loop=payload.loop,
        )

    if payload.base64 is not None:
        mime_type = payload.mime_type or _guess_mime(payload.filename or "payload.bin")
        data = _decode_base64(payload.base64)
        path = _store_bytes(data, payload.filename, mime_type)
        return _set_payload(
            file_path=path,
            source_type="base64",
            filename=payload.filename or path.name,
            mime_type=mime_type,
            title=payload.title,
            autoplay=payload.autoplay,
            loop=payload.loop,
        )

    assert payload.text is not None
    mime_type = payload.mime_type or _guess_mime(payload.filename or "payload.txt", "text/plain")
    path = _store_bytes(payload.text.encode("utf-8"), payload.filename or "payload.txt", mime_type)
    return _set_payload(
        file_path=path,
        source_type="text",
        filename=payload.filename or path.name,
        mime_type=mime_type,
        title=payload.title,
        autoplay=payload.autoplay,
        loop=payload.loop,
    )


@app.get("/payload")
def payload_info() -> dict[str, Any]:
    return state.public()


@app.post("/control")
def control(body: ControlRequest) -> dict[str, Any]:
    return _apply_control(body.command, body.loop)


@app.post("/control/{command}")
def control_path(command: Literal["start", "stop", "loop"], loop: bool | None = Query(default=None)) -> dict[str, Any]:
    return _apply_control(command, loop)


def _apply_control(command: Literal["start", "stop", "loop"], loop: bool | None = None) -> dict[str, Any]:
    with state.lock:
        if command == "start":
            state.playback = "playing"
        elif command == "stop":
            state.playback = "stopped"
        elif command == "loop":
            state.loop = (not state.loop) if loop is None else bool(loop)
            state.playback = "playing"
        state.bump()
        return state.public()


@app.post("/clear")
def clear() -> dict[str, Any]:
    with state.lock:
        state.media_id = None
        state.source_type = None
        state.path = None
        state.filename = None
        state.mime_type = None
        state.title = None
        state.playback = "stopped"
        state.bump()
        return state.public()


@app.post("/background")
def set_background(body: BackgroundRequest) -> dict[str, Any]:
    with state.lock:
        state.background = body.color
        state.bump()
        return state.public()


@app.post("/zoom")
def set_zoom(body: ZoomRequest) -> dict[str, Any]:
    with state.lock:
        if body.level is not None:
            state.zoom = body.level
        elif body.delta is not None:
            state.zoom += body.delta
        else:
            raise HTTPException(status_code=400, detail="Provide level or delta.")
        state.zoom = max(0.1, min(8.0, round(state.zoom, 3)))
        state.bump()
        return state.public()


@app.post("/zoom/in")
def zoom_in(step: float = Query(default=0.1, ge=0.01, le=2.0)) -> dict[str, Any]:
    with state.lock:
        state.zoom = max(0.1, min(8.0, round(state.zoom + step, 3)))
        state.bump()
        return state.public()


@app.post("/zoom/out")
def zoom_out(step: float = Query(default=0.1, ge=0.01, le=2.0)) -> dict[str, Any]:
    with state.lock:
        state.zoom = max(0.1, min(8.0, round(state.zoom - step, 3)))
        state.bump()
        return state.public()


@app.post("/zoom/reset")
def zoom_reset() -> dict[str, Any]:
    with state.lock:
        state.zoom = 1.0
        state.bump()
        return state.public()


@app.get("/media/current", response_model=None)
def media_current() -> Response:
    public_state = state.public()
    media_path = state.path
    if not media_path:
        return JSONResponse({"detail": "No payload loaded"}, status_code=404)
    path = Path(media_path)
    if not path.exists() or not path.is_file():
        return JSONResponse({"detail": "Current payload file no longer exists"}, status_code=404)
    return FileResponse(
        path=str(path),
        media_type=public_state.get("mime_type") or "application/octet-stream",
        filename=public_state.get("filename") or path.name,
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)