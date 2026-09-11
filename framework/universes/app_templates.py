from __future__ import annotations

import html
import json
import mimetypes
import re
import shutil
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import quote

from pydantic import BaseModel, Field


_VAR_RE = re.compile(r"{{\s*([A-Za-z_][A-Za-z0-9_]*)\s*}}")


class AppTemplateVariable(BaseModel):
    name: str
    description: str = ""
    required: bool = False
    default: Any = None


class AppTemplateSpec(BaseModel):
    template_id: str
    title: str
    kind: str = "custom"
    backend: str = "static"
    description: str = ""
    variables: List[AppTemplateVariable] = Field(default_factory=list)
    files: Dict[str, str] = Field(default_factory=dict)
    manifest_defaults: Dict[str, Any] = Field(default_factory=dict)


def _static_report_template() -> AppTemplateSpec:
    return AppTemplateSpec(
        template_id="static_report",
        title="Static Report",
        kind="report",
        backend="static",
        description="Minimal static report app with HTML, CSS, and JSON data.",
        variables=[
            AppTemplateVariable(name="title", description="Page title", default="Universe Report"),
            AppTemplateVariable(name="heading", description="Main heading", default="Universe Report"),
            AppTemplateVariable(name="body", description="Main body text", default="Generated from a Universe App template."),
        ],
        manifest_defaults={"kind": "report", "backend": "static", "index_file": "index.html"},
        files={
            "index.html": """<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>{{ title }}</title>
  <link rel=\"stylesheet\" href=\"files/style.css\" />
</head>
<body>
  <main class=\"card\">
    <h1>{{ heading }}</h1>
    <p>{{ body }}</p>
    <p class=\"hint\">Static assets are served under <code>files/&lt;relative-path&gt;</code>.</p>
  </main>
</body>
</html>
""",
            "style.css": """html, body { margin: 0; min-height: 100%; font-family: system-ui, sans-serif; background: #111827; color: #f9fafb; }
body { display: grid; place-items: center; padding: 2rem; }
.card { max-width: 840px; padding: 2rem; border-radius: 1rem; background: #1f2937; box-shadow: 0 20px 60px rgba(0,0,0,.35); }
h1 { margin-top: 0; }
.hint { color: #9ca3af; }
code { color: #93c5fd; }
""",
            "data.json": """{"template": "static_report", "ok": true}
""",
        },
    )


def _static_dashboard_template() -> AppTemplateSpec:
    return AppTemplateSpec(
        template_id="static_dashboard",
        title="Static Dashboard",
        kind="dashboard",
        backend="static",
        description="Simple static dashboard shell for Universe app/dashboard embedding.",
        variables=[
            AppTemplateVariable(name="title", description="Dashboard title", default="Universe Dashboard"),
            AppTemplateVariable(name="summary", description="Dashboard summary", default="Generated dashboard shell."),
        ],
        manifest_defaults={"kind": "dashboard", "backend": "static", "index_file": "index.html"},
        files={
            "index.html": """<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>{{ title }}</title>
  <style>
    body { margin: 0; font-family: system-ui, sans-serif; background: #020617; color: #e5e7eb; }
    header { padding: 1rem 1.5rem; background: #0f172a; border-bottom: 1px solid #334155; }
    section { padding: 1.5rem; display: grid; gap: 1rem; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); }
    article { padding: 1rem; border: 1px solid #334155; border-radius: .75rem; background: #111827; }
  </style>
</head>
<body>
  <header><h1>{{ title }}</h1><p>{{ summary }}</p></header>
  <section>
    <article><h2>Status</h2><p>Ready.</p></article>
    <article><h2>Template</h2><p>static_dashboard</p></article>
  </section>
</body>
</html>
""",
        },
    )


def _file_viewer_template() -> AppTemplateSpec:
    return AppTemplateSpec(
        template_id="file_viewer",
        title="File Viewer",
        kind="file_viewer",
        backend="static",
        description="Static single-file viewer that copies a source file and renders it using MIME-aware HTML.",
        variables=[
            AppTemplateVariable(name="source_path", description="Path to the local file to copy into the app", required=True),
            AppTemplateVariable(name="title", description="Viewer title", default="File Viewer"),
            AppTemplateVariable(name="display_name", description="Display name for the copied file", default=None),
        ],
        manifest_defaults={"kind": "file_viewer", "backend": "static", "index_file": "index.html"},
        files={
            "index.html": """<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>{{ title_html }}</title>
  <style>
    :root { color-scheme: light dark; }
    body { margin: 0; font-family: system-ui, sans-serif; background: #0f172a; color: #e5e7eb; }
    header, main { max-width: 1100px; margin: 0 auto; padding: 1.25rem; }
    header { border-bottom: 1px solid #334155; }
    h1 { margin: 0 0 .5rem 0; }
    .meta { display: flex; flex-wrap: wrap; gap: .75rem; color: #cbd5e1; font-size: .95rem; }
    .viewer { margin-top: 1rem; padding: 1rem; border: 1px solid #334155; border-radius: .75rem; background: #111827; overflow: auto; }
    pre { white-space: pre-wrap; overflow-wrap: anywhere; margin: 0; line-height: 1.45; }
    img, video, audio, iframe, object { max-width: 100%; }
    iframe, object { width: 100%; min-height: 75vh; border: 0; background: white; }
    a { color: #93c5fd; }
  </style>
</head>
<body>
  <header>
    <h1>{{ title_html }}</h1>
    <div class=\"meta\">
      <span><strong>File:</strong> {{ file_name_html }}</span>
      <span><strong>MIME:</strong> {{ mime_type_html }}</span>
      <span><strong>Size:</strong> {{ file_size_html }} bytes</span>
      <span><a href=\"{{ file_url }}\" download>Download {{ file_name_html }}</a></span>
    </div>
  </header>
  <main>
    <section class=\"viewer\">
{{ viewer_markup }}
    </section>
  </main>
</body>
</html>
""",
        },
    )


BUILTIN_TEMPLATES: Dict[str, AppTemplateSpec] = {
    "file_viewer": _file_viewer_template(),
    "static_report": _static_report_template(),
    "static_dashboard": _static_dashboard_template(),
}


def list_app_templates() -> List[Dict[str, Any]]:
    return [BUILTIN_TEMPLATES[name].model_dump(mode="json") for name in sorted(BUILTIN_TEMPLATES)]


def get_app_template(template_id: str) -> AppTemplateSpec:
    key = str(template_id or "").strip()
    if key not in BUILTIN_TEMPLATES:
        raise KeyError(f"Unknown app template: {template_id}")
    return BUILTIN_TEMPLATES[key]


def _render(text: str, context: Dict[str, Any]) -> str:
    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        value = context.get(key, "")
        if isinstance(value, (dict, list)):
            return json.dumps(value, sort_keys=True)
        return str(value)
    return _VAR_RE.sub(replace, text)


def _safe_asset_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._ -]+", "_", name or "file").strip(" .")
    return cleaned or "file"


def _is_textual_mime(mime_type: str) -> bool:
    return (
        mime_type.startswith("text/")
        or mime_type in {
            "application/json",
            "application/javascript",
            "application/xml",
            "application/xhtml+xml",
            "application/x-yaml",
            "application/yaml",
            "image/svg+xml",
        }
        or mime_type.endswith("+json")
        or mime_type.endswith("+xml")
    )


def _file_viewer_markup(source: Path, file_url: str, mime_type: str) -> str:
    escaped_url = html.escape(file_url, quote=True)
    if mime_type.startswith("image/") and mime_type != "image/svg+xml":
        return f'      <img src="{escaped_url}" alt="Rendered file" />'
    if mime_type.startswith("video/"):
        return f'      <video src="{escaped_url}" controls></video>'
    if mime_type.startswith("audio/"):
        return f'      <audio src="{escaped_url}" controls></audio>'
    if mime_type == "application/pdf":
        return f'      <iframe src="{escaped_url}" title="PDF preview"></iframe>'
    if _is_textual_mime(mime_type):
        text = source.read_text(encoding="utf-8", errors="replace")
        if mime_type == "application/json" or mime_type.endswith("+json"):
            try:
                text = json.dumps(json.loads(text), indent=2, sort_keys=True)
            except Exception:
                pass
        return "      <pre>" + html.escape(text) + "</pre>"
    return (
        '      <p>No inline preview is available for this file type. '
        f'<a href="{escaped_url}" download>Download the file</a>.</p>'
    )


def _prepare_file_viewer_context(
    *,
    root: Path,
    context: Dict[str, Any],
    title: str | None,
    overwrite: bool,
) -> List[str]:
    source_value = context.get("source_path")
    if not source_value:
        raise ValueError("Missing required template variable: source_path")
    source = Path(str(source_value)).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"File viewer source does not exist or is not a file: {source}")

    asset_name = _safe_asset_name(source.name)
    target = (root / asset_name).resolve()
    if target != root and root not in target.parents:
        raise ValueError(f"Unsafe copied file path: {asset_name}")
    if target.exists() and not overwrite:
        raise FileExistsError(f"Refusing to overwrite existing file: {target}")
    if source != target:
        shutil.copyfile(source, target)

    mime_type = mimetypes.guess_type(str(source))[0] or "application/octet-stream"
    file_url = "files/" + quote(asset_name)
    display_title = str(title or context.get("display_name") or context.get("title") or source.name)
    context.update({
        "title": display_title,
        "title_html": html.escape(display_title),
        "file_name": asset_name,
        "file_name_html": html.escape(asset_name),
        "file_url": file_url,
        "mime_type": mime_type,
        "mime_type_html": html.escape(mime_type),
        "file_size": source.stat().st_size,
        "file_size_html": html.escape(str(source.stat().st_size)),
        "viewer_markup": _file_viewer_markup(source, file_url, mime_type),
    })
    return [str(target)]


def render_app_template(
    template_id: str,
    *,
    output_dir: str | Path,
    app_id: str,
    title: str | None = None,
    context: Dict[str, Any] | None = None,
    overwrite: bool = False,
) -> Dict[str, Any]:
    spec = get_app_template(template_id)
    safe_context: Dict[str, Any] = {}
    for var in spec.variables:
        if var.default is not None:
            safe_context[var.name] = var.default
        if var.required and (context or {}).get(var.name) is None and var.default is None:
            raise ValueError(f"Missing required template variable: {var.name}")
    safe_context.update(context or {})
    # Explicit API title should override template defaults and context title.
    # Without this, templates whose variable schema has a default title render
    # that default even while the registered app manifest uses the requested
    # title, causing static_dashboard to render "Universe Dashboard" while the
    # manifest says "Smoke Static Dashboard".
    if title:
        safe_context["title"] = title
    else:
        safe_context.setdefault("title", spec.title)

    root = Path(output_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    written: List[str] = []
    if spec.template_id == "file_viewer":
        written.extend(_prepare_file_viewer_context(root=root, context=safe_context, title=title, overwrite=overwrite))

    for rel_path, content in spec.files.items():
        rel = Path(rel_path)
        if rel.is_absolute() or ".." in rel.parts:
            raise ValueError(f"Unsafe template file path: {rel_path}")
        target = (root / rel).resolve()
        if target != root and root not in target.parents:
            raise ValueError(f"Template output escapes target directory: {rel_path}")
        if target.exists() and not overwrite:
            raise FileExistsError(f"Refusing to overwrite existing file: {target}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(_render(content, safe_context), encoding="utf-8")
        written.append(str(target))

    manifest = dict(spec.manifest_defaults)
    manifest.update({
        "app_id": app_id,
        "title": title or str(safe_context.get("title") or spec.title),
        "kind": manifest.get("kind") or spec.kind,
        "backend": manifest.get("backend") or spec.backend,
        "static_dir": str(root),
        "index_file": manifest.get("index_file") or "index.html",
        "description": spec.description,
        "metadata": {
            "template_id": spec.template_id,
            "template_context": safe_context,
            "generated_files": written,
        },
    })
    return {"template": spec.model_dump(mode="json"), "manifest": manifest, "files": written}
