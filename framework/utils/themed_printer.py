# themed_printer.py (patched)
from __future__ import annotations
from typing import Optional, Tuple
import time, re
from rich.console import Console
from rich.panel import Panel
from rich.markdown import Markdown
from rich.rule import Rule
from rich.text import Text
from rich.theme import Theme
from rich import box

# ---------- Theme ----------
NICE_THEME = Theme({
    "meta.time": "dim",
    "accent": "bold cyan",
    "rule.dim": "dim",
    "panel.border": "cyan",
    "title": "bold",
    "markdown.h1": "bold bright_cyan",
    "markdown.h2": "bold cyan",
    "markdown.h3": "bold",
    "markdown.code": "bold",
    "markdown.item.bullet": "cyan",
})

# ---------- Typography helpers ----------
SMART_QUOTES = [
    (r"(?<!\w)'(?!\w)", "’"),
    (r'"([^"]+)"', r'“\1”'),
    (r"\'", "’"),
]
DASHES = [
    (r"\s--\s", " — "),
    (r"\s-\s",  " – "),
]
BULLETS = [
    (r"(?m)^\s*-\s", "• "),
    (r"(?m)^\s*\*\s", "• "),
]

def typographize(s: str) -> str:
    parts = re.split(r"(```.*?```)", s, flags=re.S)
    for i in range(0, len(parts), 2):
        seg = parts[i]
        for pat, rep in SMART_QUOTES: seg = re.sub(pat, rep, seg)
        for pat, rep in DASHES:       seg = re.sub(pat, rep, seg)
        for pat, rep in BULLETS:      seg = re.sub(pat, rep, seg)
        parts[i] = seg
    return "".join(parts)

# ---------- Detection & normalization ----------
def _normalize_newlines(s: str) -> str:
    if "\n" in s:
        return s
    return s.replace("\\n", "\n").replace("\\t", "\t").replace("\\r", "\r")

def _looks_structured(s: str) -> bool:
    if not s:
        return False
    st = s.strip()
    if "\n" in s:
        return True
    if "```" in s or "`" in s:
        return True
    if re.match(r"^(\d+\.\s|[-*•]\s)", st):
        return True
    return len(s) > 120

# ---------- Gradient helpers (backward compatible) ----------
def _hex_to_rgb(h: str) -> Tuple[int,int,int]:
    h = h.lstrip("#")
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))  # type: ignore

def _interp(a: int, b: int, t: float) -> int:
    return int(round(a + (b - a) * t))

def _manual_gradient_text(text: Text, start_hex: str, end_hex: str) -> Text:
    """Build a new Text with per-character RGB gradient (fallback for older Rich)."""
    s_r, s_g, s_b = _hex_to_rgb(start_hex)
    e_r, e_g, e_b = _hex_to_rgb(end_hex)
    src = text.plain
    n = max(len(src), 1)
    out = Text()
    # preserve original style (bold, etc.)
    base_style = text.style
    for i, ch in enumerate(src):
        t = 0 if n == 1 else i / (n - 1)
        r = _interp(s_r, e_r, t)
        g = _interp(s_g, e_g, t)
        b = _interp(s_b, e_b, t)
        style = f"rgb({r},{g},{b})"
        if base_style:
            out.append(ch, f"{base_style} {style}")
        else:
            out.append(ch, style)
    return out

def _apply_title_gradient(text: Text, start_hex: str, end_hex: str) -> Text:
    """Use Text.apply_gradient if available; otherwise manual gradient."""
    if hasattr(Text, "apply_gradient"):
        t = text.copy()
        # type: ignore[attr-defined]
        t.apply_gradient(start_hex, end_hex)  # available on newer Rich versions
        return t
    else:
        return _manual_gradient_text(text, start_hex, end_hex)

# ---------- Pretty printer ----------
def rich_print(
    message: str,
    *,
    console: Optional[Console] = None,
    width: Optional[int] = None,
    mode: str = "auto",             # "auto" | "line" | "panel"
    markdown: bool = True,
    title: Optional[str] = None,    # panel title (optional)
    gradient_title: bool = True,
    border_style: str = "panel.border",
    code_theme: str = "monokai",
    show_time: bool = True,
    show_rules: bool = True,
    pad: tuple[int, int] = (1, 2),
    typographic_flow: bool = True,
):
    console = console or Console(theme=NICE_THEME, width=width)

    msg = _normalize_newlines(str(message))
    if typographic_flow:
        msg = typographize(msg)

    use_panel = (_looks_structured(msg) if mode == "auto" else (mode == "panel"))
    timestamp = time.strftime("%H:%M:%S %m/%d/%y %Z")

    if not use_panel:
        line = f"[{timestamp}] {msg}" if show_time else msg
        console.print(line, markup=True)
        return

    if show_rules:
        console.print(Rule(style="rule.dim"))

    if show_time:
        console.print(Text(f"[{timestamp}]", style="meta.time"))

    body = Markdown(msg, code_theme=code_theme) if markdown else Text(msg)

    panel_title = None
    if title:
        base_title = Text(title, style="title")
        panel_title = (
            _apply_title_gradient(base_title, "#7dd3fc", "#34d399")
            if gradient_title else base_title
        )

    console.print(
        Panel(
            body,
            title=panel_title,
            title_align="left",
            border_style=border_style,
            box=box.ROUNDED,
            expand=True,
            padding=pad,
        )
    )

    if show_rules:
        console.print(Rule(style="rule.dim"))

