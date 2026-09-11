#!/usr/bin/env python3
"""
update_dash.py — command-line controller for the View Files Dashboard.

USER GUIDE FOR AGENTS
=====================

Purpose:
  Control the FastAPI dashboard in this directory from Python/CLI without opening
  the browser manually. Each CLI argument mirrors a dashboard endpoint name.

Basic pattern:
  ./update_dash.py --host 127.0.0.1 --port 8000 [actions...]

Common examples:
  # Load a local media/Markdown/PDF/table/audio/video file
  ./update_dash.py --payload /path/to/file.md

  # Load a file and start/loop it
  ./update_dash.py --payload /path/to/video.mp4 --control loop

  # Stop playback
  ./update_dash.py --control stop

  # Start playback
  ./update_dash.py --control start

  # Clear dashboard
  ./update_dash.py --clear

  # Set background using a friendly color name
  ./update_dash.py --background white
  ./update_dash.py --background black
  ./update_dash.py --background grey
  ./update_dash.py --background red
  ./update_dash.py --background blue
  ./update_dash.py --background orange

  # Set background using CSS color/code
  ./update_dash.py --background '#002b36'
  ./update_dash.py --background 'rgb(20,20,20)'

  # Zoom controls
  ./update_dash.py --zoom in
  ./update_dash.py --zoom out
  ./update_dash.py --zoom reset
  ./update_dash.py --zoom rest     # accepted alias for reset
  ./update_dash.py --zoom 1.5      # absolute zoom level
  ./update_dash.py --zoom +0.25    # relative zoom delta
  ./update_dash.py --zoom -0.25    # relative zoom delta

  # Combine actions; calls are applied in this order: payload, control, clear, background, zoom
  ./update_dash.py --payload /path/to/image.png --background black --zoom 1.25

Endpoint mapping:
  --payload VALUE     -> POST /payload
       If VALUE is an existing file path, sends {"path": VALUE}.
       If VALUE starts with data:, sends {"data_url": VALUE}.
       If --payload-as text is used, sends file content or literal text as {"text": ...}.
       If --payload-as base64 is used, sends file bytes base64-encoded or literal base64 as {"base64": ...}.

  --control VALUE     -> POST /control/{VALUE}
       Accepted: start, stop, loop.
       For loop, optional --loop true|false controls whether loop is enabled or toggled.

  --clear             -> POST /clear

  --background VALUE  -> POST /background
       Friendly names: white, black, grey/gray, red, blue, orange.
       Any CSS color string is also accepted.

  --zoom VALUE        -> POST /zoom/in, /zoom/out, /zoom/reset, or /zoom
       Accepted: in, out, reset/rest, absolute number such as 1.5, relative +N/-N.

Optional payload metadata:
  --mime-type VALUE   Sets payload mime_type.
  --filename VALUE    Sets payload filename.
  --title VALUE       Sets payload title.
  --autoplay true|false
  --loop true|false   Used with payload or control loop.

Notes:
  - The dashboard server must already be running, e.g.:
      uvicorn main:app --host 127.0.0.1 --port 8000 --reload
  - Uses only Python standard library modules.
  - Prints JSON responses from the webapp.
"""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


COLOR_ALIASES = {
    "white": "white",
    "black": "black",
    "grey": "grey",
    "gray": "grey",
    "red": "red",
    "blue": "blue",
    "orange": "orange",
}


def parse_bool(value: str | bool | None) -> bool | None:
    if value is None or isinstance(value, bool):
        return value
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    raise argparse.ArgumentTypeError(f"Expected boolean true/false, got: {value!r}")


def api_url(host: str, port: int, path: str) -> str:
    host = host.strip()
    if host.startswith("http://") or host.startswith("https://"):
        base = host.rstrip("/")
        parsed = urllib.parse.urlparse(base)
        if parsed.port is None and port:
            netloc = f"{parsed.hostname}:{port}"
            if parsed.username:
                auth = parsed.username
                if parsed.password:
                    auth += f":{parsed.password}"
                netloc = f"{auth}@{netloc}"
            base = urllib.parse.urlunparse((parsed.scheme, netloc, parsed.path, "", "", ""))
    else:
        base = f"http://{host}:{port}"
    return base.rstrip("/") + path


def request_json(method: str, url: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url=url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            raw = response.read().decode("utf-8", errors="replace")
            if not raw:
                return {"status": response.status, "body": None}
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return {"status": response.status, "body": raw}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            detail = json.loads(raw)
        except json.JSONDecodeError:
            detail = raw
        raise RuntimeError(f"HTTP {exc.code} from {url}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not connect to {url}: {exc.reason}") from exc


def guess_mime(file_path: Path | None, filename: str | None, explicit: str | None) -> str | None:
    if explicit:
        return explicit
    target = str(file_path or filename or "")
    guessed, _ = mimetypes.guess_type(target)
    return guessed


def build_payload_body(args: argparse.Namespace) -> dict[str, Any]:
    value = args.payload
    payload_as = args.payload_as
    path = Path(value).expanduser()

    body: dict[str, Any] = {}

    if payload_as == "path":
        body["path"] = value
    elif payload_as == "data_url":
        body["data_url"] = value
    elif payload_as == "text":
        if path.exists() and path.is_file():
            body["text"] = path.read_text(encoding=args.encoding)
            body.setdefault("filename", args.filename or path.name)
        else:
            body["text"] = value
    elif payload_as == "base64":
        if path.exists() and path.is_file():
            body["base64"] = base64.b64encode(path.read_bytes()).decode("ascii")
            body.setdefault("filename", args.filename or path.name)
        else:
            body["base64"] = value
    elif payload_as == "auto":
        if value.strip().startswith("data:"):
            body["data_url"] = value
        elif path.exists() and path.is_file():
            body["path"] = value
        else:
            # Default fallback: treat unknown non-file payloads as literal text.
            body["text"] = value
    else:
        raise ValueError(f"Unsupported --payload-as value: {payload_as}")

    if args.mime_type:
        body["mime_type"] = args.mime_type
    elif "path" not in body:
        inferred_path = path if path.exists() and path.is_file() else None
        inferred = guess_mime(inferred_path, args.filename, None)
        if inferred:
            body["mime_type"] = inferred

    if args.filename and "filename" not in body:
        body["filename"] = args.filename
    if args.title:
        body["title"] = args.title
    if args.autoplay is not None:
        body["autoplay"] = args.autoplay
    if args.loop is not None:
        body["loop"] = args.loop

    return body


def apply_payload(args: argparse.Namespace, results: list[dict[str, Any]]) -> None:
    if not args.payload:
        return
    body = build_payload_body(args)
    url = api_url(args.host, args.port, "/payload")
    results.append({"endpoint": "POST /payload", "response": request_json("POST", url, body)})


def apply_control(args: argparse.Namespace, results: list[dict[str, Any]]) -> None:
    if not args.control:
        return
    command = args.control.strip().lower()
    if command not in {"start", "stop", "loop"}:
        raise ValueError("--control must be one of: start, stop, loop")

    query = ""
    if command == "loop" and args.loop is not None:
        query = "?" + urllib.parse.urlencode({"loop": str(args.loop).lower()})

    url = api_url(args.host, args.port, f"/control/{command}{query}")
    results.append({"endpoint": f"POST /control/{command}", "response": request_json("POST", url)})


def apply_clear(args: argparse.Namespace, results: list[dict[str, Any]]) -> None:
    if not args.clear:
        return
    url = api_url(args.host, args.port, "/clear")
    results.append({"endpoint": "POST /clear", "response": request_json("POST", url)})


def apply_background(args: argparse.Namespace, results: list[dict[str, Any]]) -> None:
    if args.background is None:
        return
    raw = args.background.strip()
    color = COLOR_ALIASES.get(raw.lower(), raw)
    url = api_url(args.host, args.port, "/background")
    results.append({"endpoint": "POST /background", "response": request_json("POST", url, {"color": color})})


def apply_zoom(args: argparse.Namespace, results: list[dict[str, Any]]) -> None:
    if args.zoom is None:
        return
    value = args.zoom.strip().lower()
    if value in {"in", "zoom-in", "+"}:
        url = api_url(args.host, args.port, "/zoom/in")
        results.append({"endpoint": "POST /zoom/in", "response": request_json("POST", url)})
        return
    if value in {"out", "zoom-out", "-"}:
        url = api_url(args.host, args.port, "/zoom/out")
        results.append({"endpoint": "POST /zoom/out", "response": request_json("POST", url)})
        return
    if value in {"reset", "rest", "default", "1x"}:
        url = api_url(args.host, args.port, "/zoom/reset")
        results.append({"endpoint": "POST /zoom/reset", "response": request_json("POST", url)})
        return

    try:
        number = float(value)
    except ValueError as exc:
        raise ValueError("--zoom must be one of: in, out, reset/rest, absolute number, +delta, -delta") from exc

    body: dict[str, float]
    if value.startswith("+") or value.startswith("-"):
        body = {"delta": number}
    else:
        body = {"level": number}
    url = api_url(args.host, args.port, "/zoom")
    results.append({"endpoint": "POST /zoom", "response": request_json("POST", url, body)})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="update_dash.py",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Control the View Files Dashboard FastAPI webapp. See file header for agent user guide.",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Dashboard host or base URL. Default: 127.0.0.1")
    parser.add_argument("--port", type=int, default=8000, help="Dashboard port. Default: 8000")

    parser.add_argument("--payload", help="Payload value for POST /payload: file path, data URL, base64, or text.")
    parser.add_argument(
        "--payload-as",
        choices=["auto", "path", "data_url", "base64", "text"],
        default="auto",
        help="How to interpret --payload. Default: auto.",
    )
    parser.add_argument("--mime-type", help="Optional mime_type for /payload, e.g. text/markdown or image/png.")
    parser.add_argument("--filename", help="Optional filename for /payload.")
    parser.add_argument("--title", help="Optional title for /payload.")
    parser.add_argument("--encoding", default="utf-8", help="Encoding when reading --payload-as text files. Default: utf-8")
    parser.add_argument("--autoplay", type=parse_bool, nargs="?", const=True, help="Payload autoplay true/false.")

    parser.add_argument("--control", choices=["start", "stop", "loop"], help="Control endpoint value: start, stop, loop.")
    parser.add_argument("--loop", type=parse_bool, nargs="?", const=True, help="Loop true/false for payload or control loop.")
    parser.add_argument("--clear", action="store_true", help="Call POST /clear.")
    parser.add_argument("--background", help="Background color name or CSS color/code.")
    parser.add_argument("--zoom", help="Zoom value: in, out, reset/rest, absolute number, +delta, -delta.")

    parser.add_argument("--state", action="store_true", help="Also fetch and print GET /api/state after actions.")
    parser.add_argument("--pretty", action="store_true", default=True, help="Pretty-print JSON output. Default: true.")
    parser.add_argument("--compact", action="store_true", help="Compact JSON output.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    results: list[dict[str, Any]] = []

    try:
        apply_payload(args, results)
        apply_control(args, results)
        apply_clear(args, results)
        apply_background(args, results)
        apply_zoom(args, results)

        if args.state or not results:
            url = api_url(args.host, args.port, "/api/state")
            results.append({"endpoint": "GET /api/state", "response": request_json("GET", url)})

        if args.compact:
            print(json.dumps(results, separators=(",", ":")))
        else:
            print(json.dumps(results, indent=2, sort_keys=True))
        return 0
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())