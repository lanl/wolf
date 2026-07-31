# framework/tooling/tools_v1.py
from __future__ import annotations

import asyncio
import importlib
import inspect
import json
import os
import sys
import time
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Union

from rich.console import Console
from framework.knowledgebase.knowledge_base import KnowledgeBase
from framework.tooling.tool_models import ToolMeta, FuncArg

console = Console()

# ------------------------------
# ToolCard: Enhanced metadata
# ------------------------------

@dataclass
class ToolCard:
    name: str
    language: str = "python"               # python, js, go, lua, shell, ruby, perl, java, rust, ...
    kind: str = "function"                 # function | script | binary | service
    version: str = "0.1.0"
    description: str = ""
    usage: str = ""
    args_schema: Dict[str, Any] = field(default_factory=dict)
    entrypoint: Optional[str] = None       # dotted function path OR script/binary path
    tags: List[str] = field(default_factory=list)
    author: Optional[str] = None
    homepage: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00","Z"))
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00","Z"))

    def to_json(self) -> str:
        self.updated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00","Z")
        return json.dumps(asdict(self), indent=2, sort_keys=True)

    @staticmethod
    def from_json(data: str | Dict[str, Any]) -> "ToolCard":
        if isinstance(data, str):
            obj = json.loads(data)
        else:
            obj = data
        return ToolCard(**obj)

    @classmethod
    def from_tool_meta(cls, meta: ToolMeta) -> "ToolCard":
        """Convert a ToolMeta to a ToolCard."""
        # Determine language and kind from tool_type
        tool_type = meta.tool_type
        if "_func" in tool_type:
            kind = "function"
            language = tool_type.replace("_func", "")
        elif "_script" in tool_type:
            kind = "script"
            language = tool_type.replace("_script", "")
        elif "_executable" in tool_type or tool_type == "binary":
            kind = "binary"
            language = tool_type.replace("_executable", "") if "_executable" in tool_type else "binary"
        else:
            kind = "function"
            language = "python"

        # Build args_schema from FuncArg list
        args_schema = {}
        for arg in meta.args:
            args_schema[arg.arg_name] = {
                "type": arg.arg_type,
                "description": arg.description
            }

        return cls(
            name=meta.name,
            language=language,
            kind=kind,
            version="0.1.0",
            description=meta.description,
            usage=meta.purpose,
            args_schema=args_schema,
            entrypoint=meta.path or (f"__dynamic__:{meta.name}" if meta.body else None),
            tags=[],
            author=None,
            homepage=None,
        )


# ----------------------------------
# ToolDoc: wrapper around KB for docs
# ----------------------------------

class ToolDoc(KnowledgeBase):
    """A KnowledgeBase specialized for tool documentation."""
    pass


# --------------
# Tool primitive
# --------------

@dataclass
class Tool:
    card: ToolCard
    doc: ToolDoc
    meta: Optional[ToolMeta] = None  # Optional ToolMeta for enhanced functionality

    # ----- factories -----
    @staticmethod
    def create(
        card: ToolCard,
        kb_persist_dir: str,
        db_name: Optional[str] = None,
        embedding: Optional[Dict[str, Any]] = None,
        inventory_path: Optional[str] = None,
        vrbz: int = 0,
        meta: Optional[ToolMeta] = None,
    ) -> "Tool":
        vstore_params = {
            "collection_name": db_name or f"{card.name}_docstore",
            "persist_directory": kb_persist_dir,
            "embedding_model": embedding.get("embedding_model", "all-MiniLM-L6-v2") if embedding else "all-MiniLM-L6-v2",
        }
        kb_params = {
            "name": f"{card.name}_kb",
            "vstore_params": vstore_params,
            "inventory_path": inventory_path or os.path.join(kb_persist_dir, f"{card.name}_inventory.sqlite"),
            "vrbz": vrbz,
        }
        tool_doc = ToolDoc(kb_params)
        return Tool(card=card, doc=tool_doc, meta=meta)

    @classmethod
    def from_tool_meta(
        cls,
        meta: ToolMeta,
        kb_persist_dir: str,
        db_name: Optional[str] = None,
        embedding: Optional[Dict[str, Any]] = None,
        inventory_path: Optional[str] = None,
        vrbz: int = 0,
    ) -> "Tool":
        """Create a Tool from a ToolMeta."""
        card = ToolCard.from_tool_meta(meta)
        return cls.create(
            card=card,
            kb_persist_dir=kb_persist_dir,
            db_name=db_name,
            embedding=embedding,
            inventory_path=inventory_path,
            vrbz=vrbz,
            meta=meta,
        )

    # ----- card I/O -----
    def save_card(self, path: str) -> None:
        Path(os.path.dirname(path) or ".").mkdir(parents=True, exist_ok=True)
        Path(path).write_text(self.card.to_json(), encoding="utf-8")

    @staticmethod
    def load_card(path: str) -> ToolCard:
        return ToolCard.from_json(Path(path).read_text(encoding="utf-8"))

    # ----- routed document ops (sync) -----
    def add_text_docs(self, texts: Sequence[str], doc_source: str = "tool") -> Any:
        return self.doc.add_text_docs(texts, doc_source=doc_source)

    def add_supported_doc(self, path: str, doc_type: Optional[str] = None) -> Any:
        return self.doc.add_supported_doc(path, doc_type=doc_type)

    def add_supported_docs(self, paths: Sequence[str]) -> Any:
        return self.doc.add_supported_docs(paths)

    def upload_dir(self, dir_path: str, target_file_ext: Sequence[str] | None = None) -> Any:
        return self.doc.upload_dir(dir_path, target_file_ext=target_file_ext)

    def rebuild(self, sources: Sequence[str] | str, target_file_ext: Sequence[str] | None = None) -> Any:
        self.doc.purge()
        if isinstance(sources, str):
            return self.doc.upload_dir(sources, target_file_ext=target_file_ext)
        return self.doc.add_supported_docs(sources)

    def search(self, query: str, k: int = 5, with_score: bool = True, max_score: float | None = 0.95, context_window: int = 1):
        return self.doc.search(query, k=k, with_score=with_score, max_score=max_score, context_window=context_window)

    def search_by_source(self, source_path: str):
        return self.doc.search_by_source(source_path)

    def get_chunks_for_source(self, source_path: str):
        return self.doc.get_chunks_for_source(source_path)

    def neighbor_chunks(self, v_id: str, window: int = 2):
        return self.doc.neighbor_chunks(v_id, window=window)

    def get_document_by_id(self, document_id: str):
        return self.doc.get_document_by_id(document_id)

    def purge(self) -> None:
        self.doc.purge()

    # ----- routed document ops (async) -----
    async def aadd_text_docs(self, texts: Sequence[str], doc_source: str = "tool") -> Any:
        return await self.doc.aadd_text_docs(texts, doc_source=doc_source)

    async def aadd_supported_doc(self, path: str, doc_type: Optional[str] = None) -> Any:
        return await self.doc.aadd_supported_doc(path, doc_type=doc_type)

    async def aadd_supported_docs(self, paths: Sequence[str], concurrency: int = 8) -> Any:
        return await self.doc.aadd_supported_docs(paths, concurrency=concurrency)

    async def aupload_dir(self, dir_path: str, target_file_ext: Sequence[str] | None = None) -> Any:
        return await self.doc.aupload_dir(dir_path, target_file_ext=target_file_ext)

    async def arebuild(self, sources: Sequence[str] | str, target_file_ext: Sequence[str] | None = None) -> Any:
        await self.doc.apurge()
        if isinstance(sources, str):
            return await self.doc.aupload_dir(sources, target_file_ext=target_file_ext)
        return await self.doc.aadd_supported_docs(sources)

    async def asearch(self, query: str, k: int = 5, with_score: bool = True, max_score: float | None = 0.95, context_window: int = 1):
        return await self.doc.asearch(query, k=k, with_score=with_score, max_score=max_score, context_window=context_window)

    # ----- convenience -----
    def info(self) -> Dict[str, Any]:
        return {
            "card": asdict(self.card),
            "inventory_stats": self.doc.inventory_stats(),
            "persist_directory": self.doc.vstore.persist_directory,
        }

    def describe(self) -> str:
        """Return a human-readable description of the tool."""
        desc = f"🔧 Tool: {self.card.name}\n"
        desc += f"Language: {self.card.language}\n"
        desc += f"Kind: {self.card.kind}\n"
        desc += f"Version: {self.card.version}\n"
        desc += f"Description: {self.card.description}\n"
        if self.card.usage:
            desc += f"Usage: {self.card.usage}\n"
        if self.card.args_schema:
            desc += f"Arguments:\n"
            for arg_name, arg_info in self.card.args_schema.items():
                desc += f"  - {arg_name} ({arg_info.get('type', 'any')}): {arg_info.get('description', '')}\n"
        if self.card.entrypoint:
            desc += f"Entrypoint: {self.card.entrypoint}\n"
        if self.card.tags:
            desc += f"Tags: {', '.join(self.card.tags)}\n"
        return desc.strip()

    # ------------------
    # Execution support
    # ------------------
    _INTERPRETERS = {
        "python": sys.executable,
        "node": "node",
        "javascript": "node",
        "js": "node",
        "ruby": "ruby",
        "perl": "perl",
        "lua": "lua",
        "bash": "bash",
        "sh": "sh",
        "shell": "sh",
        "php": "php",
        "go": "go run",
        "rust": "cargo run",
    }

    def _build_command(self, args: Sequence[str] | None) -> List[str]:
        if not self.card.entrypoint:
            raise ValueError("ToolCard.entrypoint is required to execute the tool.")
        ep = self.card.entrypoint
        lang = (self.card.language or "python").lower()
        kind = (self.card.kind or "function").lower()

        if kind == "function":
            # Executed in-process, no external command
            return []

        # Script or binary
        if kind in {"script", "binary", "service"}:
            if lang in {"binary"} or (kind == "binary"):
                cmd = [ep]
            else:
                interp = self._INTERPRETERS.get(lang)
                if not interp:
                    # Attempt to run directly
                    cmd = [ep]
                else:
                    if " " in interp:  # e.g., "go run"
                        cmd = interp.split() + [ep]
                    else:
                        cmd = [interp, ep]
            if args:
                cmd.extend(list(map(str, args)))
            return cmd

        # Fallback: try to execute entrypoint as a command
        cmd = [ep]
        if args:
            cmd.extend(list(map(str, args)))
        return cmd

    def execute(
        self,
        *fn_args: Any,
        args: Optional[Sequence[str]] = None,
        kwargs: Optional[Dict[str, Any]] = None,
        env: Optional[Dict[str, str]] = None,
        cwd: Optional[str | os.PathLike] = None,
        timeout: Optional[float] = None,
        text: bool = True,
        input_data: Optional[Union[str, bytes]] = None,
    ) -> Dict[str, Any]:
        """Execute the tool.

        - If kind == "function" (language=python), imports & calls the function entrypoint (sync or async).
        - Otherwise, runs the script/binary via subprocess.

        Returns a dict with keys: ok, returncode, duration, stdout, stderr, result (for functions).
        """
        started = time.perf_counter()
        kind = (self.card.kind or "function").lower()
        lang = (self.card.language or "python").lower()
        kwargs = kwargs or {}

        try:
            if kind == "function" and lang == "python":
                if not self.card.entrypoint:
                    raise ValueError("Function execution requires a dotted entrypoint, e.g. 'pkg.mod:func'")
                
                # Check if using dynamic body from ToolMeta
                if self.meta and self.meta.body and self.card.entrypoint.startswith("__dynamic__:"):
                    # Execute from body
                    ns = {}
                    exec(self.meta.body, ns)
                    func_name = self.card.entrypoint.split(":", 1)[1]
                    if func_name in ns:
                        fn = ns[func_name]
                    else:
                        raise ValueError(f"Function {func_name} not found in body")
                else:
                    # Import from module
                    module_path, func_name = self.card.entrypoint.split(":")
                    mod = importlib.import_module(module_path)
                    fn = getattr(mod, func_name)
                
                if inspect.iscoroutinefunction(fn):
                    # Run async function in a fresh loop
                    result = asyncio.run(fn(*fn_args, **kwargs))
                else:
                    result = fn(*fn_args, **kwargs)
                duration = time.perf_counter() - started
                return {
                    "ok": True,
                    "returncode": 0,
                    "duration": duration,
                    "stdout": None,
                    "stderr": None,
                    "result": result,
                    "kind": "function",
                }

            # External script/binary
            cmd = self._build_command(args=args)
            import subprocess

            completed = subprocess.run(
                cmd,
                input=input_data,
                capture_output=True,
                text=text,
                env={**os.environ, **(env or {})},
                cwd=str(cwd) if cwd else None,
                timeout=timeout,
                check=False,
            )
            duration = time.perf_counter() - started
            return {
                "ok": completed.returncode == 0,
                "returncode": int(completed.returncode),
                "duration": duration,
                "stdout": completed.stdout,
                "stderr": completed.stderr,
                "cmd": cmd,
                "kind": "script" if kind != "binary" else "binary",
            }

        except Exception as e:
            duration = time.perf_counter() - started
            return {
                "ok": False,
                "returncode": -1,
                "duration": duration,
                "stdout": None,
                "stderr": str(e),
                "kind": kind,
            }

    async def aexecute(
        self,
        *fn_args: Any,
        args: Optional[Sequence[str]] = None,
        kwargs: Optional[Dict[str, Any]] = None,
        env: Optional[Dict[str, str]] = None,
        cwd: Optional[str | os.PathLike] = None,
        timeout: Optional[float] = None,
        text: bool = True,
        input_data: Optional[Union[str, bytes]] = None,
    ) -> Dict[str, Any]:
        """Async variant of execute(). Uses asyncio subprocess and awaits coroutine functions."""
        started = time.perf_counter()
        kind = (self.card.kind or "function").lower()
        lang = (self.card.language or "python").lower()
        kwargs = kwargs or {}

        try:
            if kind == "function" and lang == "python":
                if not self.card.entrypoint:
                    raise ValueError("Function execution requires a dotted entrypoint, e.g. 'pkg.mod:func'")
                
                # Check if using dynamic body from ToolMeta
                if self.meta and self.meta.body and self.card.entrypoint.startswith("__dynamic__:"):
                    # Execute from body
                    ns = {}
                    exec(self.meta.body, ns)
                    func_name = self.card.entrypoint.split(":", 1)[1]
                    if func_name in ns:
                        fn = ns[func_name]
                    else:
                        raise ValueError(f"Function {func_name} not found in body")
                else:
                    # Import from module
                    module_path, func_name = self.card.entrypoint.split(":")
                    mod = importlib.import_module(module_path)
                    fn = getattr(mod, func_name)
                
                if inspect.iscoroutinefunction(fn):
                    result = await fn(*fn_args, **kwargs)
                else:
                    # Run sync function in a thread to avoid blocking loop
                    loop = asyncio.get_running_loop()
                    result = await loop.run_in_executor(None, lambda: fn(*fn_args, **kwargs))
                duration = time.perf_counter() - started
                return {
                    "ok": True,
                    "returncode": 0,
                    "duration": duration,
                    "stdout": None,
                    "stderr": None,
                    "result": result,
                    "kind": "function",
                }

            # External script/binary
            cmd = self._build_command(args=args)
            stdin = asyncio.subprocess.PIPE if input_data is not None else None

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=stdin,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={**os.environ, **(env or {})},
                cwd=str(cwd) if cwd else None,
            )

            try:
                input_bytes = None
                if input_data is not None:
                    if isinstance(input_data, bytes):
                        input_bytes = input_data
                    else:
                        input_bytes = input_data.encode() if input_data else None
                
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(input=input_bytes),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.communicate()
                raise

            duration = time.perf_counter() - started
            if text:
                stdout = stdout.decode() if isinstance(stdout, (bytes, bytearray)) else stdout
                stderr = stderr.decode() if isinstance(stderr, (bytes, bytearray)) else stderr

            return {
                "ok": proc.returncode == 0,
                "returncode": int(proc.returncode),
                "duration": duration,
                "stdout": stdout,
                "stderr": stderr,
                "cmd": cmd,
                "kind": "script" if kind != "binary" else "binary",
            }

        except Exception as e:
            duration = time.perf_counter() - started
            return {
                "ok": False,
                "returncode": -1,
                "duration": duration,
                "stdout": None,
                "stderr": str(e),
                "kind": kind,
            }
