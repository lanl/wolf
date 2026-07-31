from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from framework.gui.capture_models import CaptureResult, CaptureUrlRequest
from framework.gui.capture_policy import CapturePolicy, validate_capture_url
from framework.gui.capture_storage import CaptureStorage


def _playwright_missing_result(capture_id: str, request: CaptureUrlRequest, policy: dict) -> CaptureResult:
    return CaptureResult(
        ok=False,
        status="error",
        capture_id=capture_id,
        source_url=request.url,
        format=request.format,
        error="Playwright is not installed. Install it with `pip install playwright` and run `python -m playwright install chromium`.",
        policy=policy,
        metadata={"dependency": "playwright"},
    )


async def capture_url_async(
    request: CaptureUrlRequest,
    session_id: str = "default",
    storage: CaptureStorage | None = None,
    policy: CapturePolicy | None = None,
) -> CaptureResult:
    storage = storage or CaptureStorage()
    storage.cleanup_expired()
    decision = validate_capture_url(request.url, policy)
    capture_id = storage.new_capture_id()
    if not decision.allowed:
        result = CaptureResult(
            ok=False,
            status="blocked",
            capture_id=capture_id,
            source_url=request.url,
            format=request.format,
            error=decision.reason,
            policy=decision.as_dict(),
        )
        meta_path = storage.write_metadata(session_id, capture_id, result.model_dump(mode="json"))
        result.metadata_path = str(meta_path)
        return result

    try:
        from playwright.async_api import TimeoutError as PlaywrightTimeoutError
        from playwright.async_api import async_playwright
    except Exception:
        result = _playwright_missing_result(capture_id, request, decision.as_dict())
        meta_path = storage.write_metadata(session_id, capture_id, result.model_dump(mode="json"))
        result.metadata_path = str(meta_path)
        return result

    image_path = storage.image_path(session_id, capture_id, request.format)
    width = int(request.viewport.width)
    height = int(request.viewport.height)
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                viewport={"width": width, "height": height},
                device_scale_factor=float(request.viewport.device_scale_factor),
                ignore_https_errors=True,
            )
            page = await context.new_page()
            await page.goto(request.url, wait_until=request.wait_until, timeout=int(request.timeout_ms))
            if request.extra_wait_ms:
                await page.wait_for_timeout(int(request.extra_wait_ms))
            screenshot_kwargs: Dict[str, Any] = {"path": str(image_path), "full_page": bool(request.full_page), "type": request.format}
            if request.format == "jpeg":
                screenshot_kwargs["quality"] = int(request.quality)
            await page.screenshot(**screenshot_kwargs)
            try:
                dimensions = await page.evaluate("() => ({width: window.innerWidth, height: window.innerHeight, title: document.title, url: location.href})")
            except Exception:
                dimensions = {"width": width, "height": height}
            await context.close()
            await browser.close()
        result = CaptureResult(
            ok=True,
            status="success",
            capture_id=capture_id,
            source_url=request.url,
            image_path=str(image_path),
            width=int(dimensions.get("width") or width),
            height=int(dimensions.get("height") or height),
            format=request.format,
            policy=decision.as_dict(),
            metadata={"reason": request.reason, "page": dimensions, **(request.metadata or {})},
        )
    except Exception as exc:
        status = "timeout" if exc.__class__.__name__.lower().endswith("timeouterror") else "error"
        result = CaptureResult(
            ok=False,
            status=status,
            capture_id=capture_id,
            source_url=request.url,
            format=request.format,
            error=f"{type(exc).__name__}: {exc}",
            policy=decision.as_dict(),
            metadata={"reason": request.reason, **(request.metadata or {})},
        )
    meta_path = storage.write_metadata(session_id, capture_id, result.model_dump(mode="json"))
    result.metadata_path = str(meta_path)
    return result


def capture_url(request: CaptureUrlRequest, session_id: str = "default", storage: CaptureStorage | None = None, policy: CapturePolicy | None = None) -> CaptureResult:
    return asyncio.run(capture_url_async(request=request, session_id=session_id, storage=storage, policy=policy))
