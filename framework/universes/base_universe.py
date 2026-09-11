from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple
import os
from pathlib import Path
from datetime import datetime, timezone
import uuid
import subprocess
import tempfile
import shlex
import asyncio
from urllib.parse import quote, unquote

import chromadb
import requests
import websockets

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field, AliasChoices

from framework.agentic.agentic_tools import NameGenerator
from framework.knowledgebase.data_models import KnowledgeBaseParams, MultimodalKnowledgeBaseParams
from framework.knowledgebase.knowledge_base import KnowledgeBase
from framework.knowledgebase.base_multimodal_knowledgebase import MultimodalKnowledgeBase
from framework.universes.data_models import BaseUniverseModel, BaseUniverseParams, base_universe_params_type
from framework.universes.status_files import write_status_atomic
from framework.tooling.toolbox import ToolBox
from framework.tooling.tools import Tool, ToolCard
from framework.tooling.tool_models import ToolMeta
from framework.universes.app_templates import list_app_templates as _list_app_templates, get_app_template as _get_app_template, render_app_template


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_app_id(value: str | None = None) -> str:
    raw = str(value or "").strip()
    if not raw:
        raw = f"app_{uuid.uuid4().hex[:10]}"
    cleaned = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in raw)
    cleaned = cleaned.strip("_").lower()
    return cleaned or f"app_{uuid.uuid4().hex[:10]}"


class BaseUniverse:
    """Self-contained environment that manages KnowledgeBases and ToolBoxes.
    
    Enhanced features from MADA's ActionBox:
    - Full async support for all operations
    - Enhanced KB and TB proxies with context window support
    - Tool discovery and execution
    - REST API factory with comprehensive endpoints
    - Support for both ToolCard and ToolMeta
    - Support for both text-only and multimodal KnowledgeBases
    """

    # -----------------------------
    # Construction & registries
    # -----------------------------
    def __init__(self, params: BaseUniverseParams):
        self.params = params
        info, kbs, tbs = self.params.info, self.params.kbs, self.params.tbs
        self.KBs: Dict[str, KnowledgeBase | MultimodalKnowledgeBase] = dict(kbs or {})
        self.TBs: Dict[str, ToolBox] = dict(tbs or {})
        self.Apps: Dict[str, UniverseAppManifest] = {}
        self.AppProcesses: Dict[str, subprocess.Popen] = {}
        self.info = info
        self.name = "NAMELESS"
        if info is not None:
            self.name = info.name
        # Initialize db_client for the universe
        self.db_client = chromadb.Client()

    # -----------------------------
    # Allowed actions & discovery
    # -----------------------------
    def allowed_actions(self) -> List[str]:
        """Return the list of supported actions in the Universe."""
        return [
            # Discovery
            "get_allowed_actions",
            "info",
            "get_stats",
            # KB registry ops
            "list_kbs",
            "add_kb",
            "remove_kb",
            # KB operations
            "kb_search",
            "kb_append_texts",
            "kb_upload_dir",
            "kb_add_url",
            "kb_add_urls",
            "kb_add_document",
            "kb_stats",
            "kb_sources",
            "kb_purge",
            "kb_get_document_by_id",
            # TB registry ops
            "list_tbs",
            "add_tb",
            "remove_tb",
            # TB operations
            "tb_search_tools",
            "tb_execute",
            "tb_tool_info",
            "tb_list_tools",
            "tb_append_docs",
            "tb_upload_docs",
            "tb_search_tool_docs",
            "tb_add_tool_from_meta",
            "tb_recursive_upload_tools",
            "tb_get_stats",
            # Tool listings
            "get_available_tools",
            "get_toolbox_tools",
            # Universe app registry/lifecycle ops
            "list_apps",
            "register_app",
            "get_app",
            "start_app",
            "stop_app",
            "restart_app",
            "delete_app",
            "app_logs",
            # Universe app template ops
            "list_app_templates",
            "get_app_template",
            "instantiate_app_template",
        ]

    def get_info(self) -> Dict[str, Any]:
        """High-level discovery: list KBs, TBs, and allowed actions."""
        return {
            "node_info": self.info,
            "kbs": self.list_kbs(),
            "tbs": self.list_tbs(),
            "apps": self.list_apps(),
            "allowed_actions": self.allowed_actions(),
        }

    def get_stats(self) -> Dict[str, Any]:
        """Get comprehensive statistics for all KBs and TBs."""
        kb_stats = {name: kb.get_stats() for name, kb in self.KBs.items()}
        tb_stats = {name: tb.get_stats() for name, tb in self.TBs.items()}
        return {
            "kbs": kb_stats,
            "tbs": tb_stats,
            "num_kbs": len(self.KBs),
            "num_tbs": len(self.TBs),
            "num_apps": len(self.Apps),
        }

    # -----------------------------
    # KB registry ops
    # -----------------------------
    def add_kb(self, name: str, kb: KnowledgeBase | MultimodalKnowledgeBase) -> None:
        self.KBs[name] = kb

    def remove_kb(self, name: str) -> bool:
        return self.KBs.pop(name, None) is not None

    def get_kb(self, name: str) -> KnowledgeBase | MultimodalKnowledgeBase:
        kb = self.KBs.get(name)
        if not kb:
            raise KeyError(f"Unknown KB: {name}")
        return kb

    def list_kbs(self) -> List[str]:
        return sorted(self.KBs.keys())

    # -----------------------------
    # TB registry ops
    # -----------------------------
    def add_tb(self, name: str, tb: ToolBox) -> None:
        self.TBs[name] = tb

    def remove_tb(self, name: str) -> bool:
        return self.TBs.pop(name, None) is not None

    def get_tb(self, name: str) -> ToolBox:
        tb = self.TBs.get(name)
        if not tb:
            raise KeyError(f"Unknown TB: {name}")
        return tb

    def list_tbs(self) -> List[str]:
        return sorted(self.TBs.keys())

    # -----------------------------
    # KB proxies (enhanced)
    # -----------------------------
    def kb_search(self, name: str, query: str, k: int = 5, context_window: int = 1) -> List[Dict[str, Any]]:
        return self.get_kb(name).search(query, k=k, with_score=False, context_window=context_window)

    async def akb_search(self, name: str, query: str, k: int = 5, context_window: int = 1) -> List[Dict[str, Any]]:
        return await self.get_kb(name).asearch(query, k=k, with_score=False, context_window=context_window)

    def kb_append_texts(self, name: str, texts: Sequence[str], doc_source: str = "universe") -> Any:
        return self.get_kb(name).add_text_docs(texts, doc_source=doc_source)

    async def akb_append_texts(self, name: str, texts: Sequence[str], doc_source: str = "universe") -> Any:
        return await self.get_kb(name).aadd_text_docs(texts, doc_source=doc_source)

    def kb_upload_dir(self, name: str, dir_path: str, target_ext: Optional[Sequence[str]] = None) -> Any:
        return self.get_kb(name).upload_dir(dir_path, target_file_ext=target_ext or [])

    async def akb_upload_dir(self, name: str, dir_path: str, target_ext: Optional[Sequence[str]] = None) -> Any:
        return await self.get_kb(name).aupload_dir(dir_path, target_file_ext=target_ext or [])

    def kb_add_url(self, name: str, url: str) -> Any:
        """Add a single HTML document from URL."""
        return self.get_kb(name).add_url_doc(url)

    async def akb_add_url(self, name: str, url: str) -> Any:
        """Add a single HTML document from URL (async)."""
        return await self.get_kb(name).aadd_url_doc(url)

    def kb_add_urls(self, name: str, urls: Sequence[str]) -> Any:
        """Add multiple HTML documents from URLs."""
        return self.get_kb(name).add_url_docs(urls)

    async def akb_add_urls(self, name: str, urls: Sequence[str]) -> Any:
        """Add multiple HTML documents from URLs (async)."""
        return await self.get_kb(name).aadd_url_docs(urls)

    def kb_add_document(self, name: str, content: Any, metadata: Optional[Dict[str, Any]] = None, modality: str = "text") -> Any:
        """Add a single document to a knowledge base (multimodal KB only)."""
        kb = self.get_kb(name)
        if not isinstance(kb, MultimodalKnowledgeBase):
            raise TypeError(f"KB '{name}' is not a multimodal knowledge base")
        return kb.add_document(content, metadata=metadata, modality=modality)

    async def akb_add_document(self, name: str, content: Any, metadata: Optional[Dict[str, Any]] = None, modality: str = "text") -> Any:
        """Add a single document to a knowledge base (multimodal KB only) (async)."""
        kb = self.get_kb(name)
        if not isinstance(kb, MultimodalKnowledgeBase):
            raise TypeError(f"KB '{name}' is not a multimodal knowledge base")
        # MultimodalKnowledgeBase.add_document is sync but uses _run_async_in_thread internally
        return kb.add_document(content, metadata=metadata, modality=modality)

    def kb_stats(self, name: str) -> Dict[str, int]:
        return self.get_kb(name).get_stats()

    def kb_sources(self, name: str) -> List[Dict[str, Any]]:
        return self.get_kb(name).list_sources()

    def kb_purge(self, name: str) -> None:
        self.get_kb(name).purge()

    async def akb_purge(self, name: str) -> None:
        await self.get_kb(name).apurge()

    def kb_get_document_by_id(self, name: str, document_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve a document by its ID."""
        return self.get_kb(name).get_document_by_id(document_id)

    # -----------------------------
    # TB proxies (enhanced)
    # -----------------------------
    def tb_search_tools(self, name: str, query: str, k: int = 5) -> List[Dict[str, Any]]:
        return self.get_tb(name).search_tools(query, k=k)

    async def atb_search_tools(self, name: str, query: str, k: int = 5) -> List[Dict[str, Any]]:
        return await self.get_tb(name).asearch_tools(query, k=k)

    def tb_execute(self, name: str, tool_name: str, *fn_args: Any, **exec_kwargs) -> Dict[str, Any]:
        return self.get_tb(name).execute_tool(tool_name, *fn_args, **exec_kwargs)

    async def atb_execute(self, name: str, tool_name: str, *fn_args: Any, **exec_kwargs) -> Dict[str, Any]:
        return await self.get_tb(name).aexecute_tool(tool_name, *fn_args, **exec_kwargs)

    def tb_tool_info(self, name: str, tool_name: str) -> Optional[Dict[str, Any]]:
        return self.get_tb(name).tool_info(tool_name)

    def tb_list_tools(self, name: str) -> List[str]:
        """List all tools in a toolbox."""
        return self.get_tb(name).list_tools()

    def tb_append_docs(self, name: str, tool_name: str, texts: Sequence[str], doc_source: str = "universe") -> Any:
        return self.get_tb(name).append_tool_docs(tool_name, texts, doc_source=doc_source)

    async def atb_append_docs(self, name: str, tool_name: str, texts: Sequence[str], doc_source: str = "universe") -> Any:
        return await self.get_tb(name).aappend_tool_docs(tool_name, texts, doc_source=doc_source)

    def tb_upload_docs(self, name: str, tool_name: str, dir_path: str, target_ext: Optional[Sequence[str]] = None) -> Any:
        return self.get_tb(name).upload_tool_docs(tool_name, dir_path, target_file_ext=target_ext or [])

    async def atb_upload_docs(self, name: str, tool_name: str, dir_path: str, target_ext: Optional[Sequence[str]] = None) -> Any:
        return await self.get_tb(name).aupload_tool_docs(tool_name, dir_path, target_file_ext=target_ext or [])

    def tb_search_tool_docs(self, name: str, tool_name: str, query: str, k: int = 5, context_window: int = 1) -> List[Dict[str, Any]]:
        """Search tool documentation with context window."""
        return self.get_tb(name).search_tool_docs(tool_name, query, k=k, context_window=context_window)

    async def atb_search_tool_docs(self, name: str, tool_name: str, query: str, k: int = 5, context_window: int = 1) -> List[Dict[str, Any]]:
        """Search tool documentation with context window (async)."""
        return await self.get_tb(name).asearch_tool_docs(tool_name, query, k=k, context_window=context_window)

    def tb_add_tool_from_meta(self, name: str, meta: ToolMeta, kb_persist_dir: str) -> Tuple[str, str]:
        """Add a tool from ToolMeta."""
        return self.get_tb(name).add_tool_from_meta(meta, kb_persist_dir=kb_persist_dir)

    async def atb_add_tool_from_meta(self, name: str, meta: ToolMeta, kb_persist_dir: str) -> Tuple[str, str]:
        """Add a tool from ToolMeta (async)."""
        return await self.get_tb(name).aadd_tool_from_meta(meta, kb_persist_dir=kb_persist_dir)

    async def atb_recursive_upload_tools(self, name: str, path: str, extensions: Optional[List[str]] = None, kb_persist_dir: Optional[str] = None) -> List[Tuple[str, str]]:
        """Recursively discover and upload tools from a directory."""
        return await self.get_tb(name).recursive_upload_tools(path, extensions=extensions, kb_persist_dir=kb_persist_dir)

    def tb_get_stats(self, name: str) -> Dict[str, Any]:
        """Get toolbox statistics."""
        return self.get_tb(name).get_stats()

    # -----------------------------
    # Universe app registry / lifecycle helpers
    # -----------------------------
    def list_apps(self) -> List[Dict[str, Any]]:
        for key in list(self.Apps.keys()):
            self._refresh_app_process_state(key)
        return [self.Apps[name].model_dump(mode="json", exclude_none=True) for name in sorted(self.Apps)]

    def register_app(self, app: "UniverseAppManifest | Dict[str, Any]") -> Dict[str, Any]:
        if isinstance(app, dict):
            data = dict(app)
            if not data.get("app_id"):
                data["app_id"] = data.get("id") or data.get("name") or data.get("title")
            app = UniverseAppManifest.model_validate(data)
        app.app_id = _safe_app_id(app.app_id)
        if not app.title:
            app.title = app.app_id
        if not app.url:
            if app.allow_proxy and (app.proxy_url or app.websocket_url or app.internal_port):
                app.url = f"/apps/{app.app_id}/proxy/"
            else:
                app.url = f"/apps/{app.app_id}/view" if app.static_dir else f"/apps/{app.app_id}"
        now = _utc_now_iso()
        if not app.created_at:
            app.created_at = now
        app.updated_at = now
        if not app.status:
            app.status = "registered"
        self.Apps[app.app_id] = app
        return app.model_dump(mode="json", exclude_none=True)

    def get_app(self, app_id: str) -> Dict[str, Any]:
        key = _safe_app_id(app_id)
        if key not in self.Apps:
            raise KeyError(f"Unknown Universe app: {app_id}")
        self._refresh_app_process_state(key)
        return self.Apps[key].model_dump(mode="json", exclude_none=True)

    def _resolve_static_app_file(self, app_id: str, rel_path: str | None = None) -> Path:
        key = _safe_app_id(app_id)
        if key not in self.Apps:
            raise KeyError(f"Unknown Universe app: {app_id}")
        app = self.Apps[key]
        if not app.static_dir:
            raise FileNotFoundError(f"Universe app {key} does not define static_dir")
        root = Path(app.static_dir).expanduser().resolve()
        if not root.exists() or not root.is_dir():
            raise FileNotFoundError(f"Static app directory not found: {root}")
        requested = rel_path or app.index_file or "index.html"
        requested = str(requested).lstrip("/") or (app.index_file or "index.html")
        target = (root / requested).resolve()
        if target != root and root not in target.parents:
            raise PermissionError("Static app path escapes app root")
        if target.is_dir():
            target = (target / (app.index_file or "index.html")).resolve()
            if target != root and root not in target.parents:
                raise PermissionError("Static app path escapes app root")
        if not target.exists() or not target.is_file():
            raise FileNotFoundError(f"Static app file not found: {requested}")
        return target


    def _app_proxy_base_url(self, app_id: str) -> str:
        key = _safe_app_id(app_id)
        if key not in self.Apps:
            raise KeyError(f"Unknown Universe app: {app_id}")
        app = self.Apps[key]
        if not app.allow_proxy:
            raise PermissionError("Universe app proxying is disabled for this app; set allow_proxy=true in the manifest")
        base = str(app.proxy_url or "").strip()
        if not base and app.internal_port:
            base = f"http://127.0.0.1:{int(app.internal_port)}"
        if not base:
            raise FileNotFoundError(f"Universe app {key} does not define proxy_url or internal_port")
        if not (base.startswith("http://") or base.startswith("https://")):
            raise ValueError("proxy_url must start with http:// or https://")
        return base.rstrip("/")

    def _safe_proxy_rel_path(self, rel_path: str | None) -> str:
        """Return a URL-encoded relative proxy path that cannot escape proxy_url's base path."""
        raw = str(rel_path or "").replace("\\", "/").lstrip("/")
        safe_segments: List[str] = []
        for segment in raw.split("/"):
            decoded = unquote(segment)
            if decoded in {"", "."}:
                continue
            if decoded == "..":
                raise PermissionError("Proxy path may not contain parent-directory segments")
            safe_segments.append(quote(decoded, safe="!$&'()*+,;=:@"))
        return "/".join(safe_segments)

    def _app_websocket_base_url(self, app_id: str) -> str:
        key = _safe_app_id(app_id)
        if key not in self.Apps:
            raise KeyError(f"Unknown Universe app: {app_id}")
        app = self.Apps[key]
        if not app.allow_proxy:
            raise PermissionError("Universe app proxying is disabled for this app; set allow_proxy=true in the manifest")
        base = str(app.websocket_url or "").strip()
        if not base:
            base = self._app_proxy_base_url(key)
        if base.startswith("http://"):
            base = "ws://" + base[len("http://"):]
        elif base.startswith("https://"):
            base = "wss://" + base[len("https://"):]
        elif not (base.startswith("ws://") or base.startswith("wss://")):
            raise ValueError("websocket_url must start with ws://, wss://, http://, or https://")
        return base.rstrip("/")

    def _app_websocket_target_url(self, app_id: str, rel_path: str | None = None, query_string: str = "") -> str:
        base = self._app_websocket_base_url(app_id)
        safe_path = self._safe_proxy_rel_path(rel_path)
        target = f"{base}/" + safe_path
        if query_string:
            target = f"{target}?{query_string}"
        return target

    @staticmethod
    def _websocket_forward_headers(websocket: WebSocket) -> List[tuple[str, str]]:
        hop_by_hop = {
            "host",
            "connection",
            "upgrade",
            "sec-websocket-key",
            "sec-websocket-version",
            "sec-websocket-extensions",
            "sec-websocket-protocol",
            "content-length",
            "proxy-authorization",
            "proxy-authenticate",
        }
        return [(k, v) for k, v in websocket.headers.items() if k.lower() not in hop_by_hop]

    @staticmethod
    def _websocket_requested_subprotocols(websocket: WebSocket) -> List[str]:
        raw = str(websocket.headers.get("sec-websocket-protocol") or "")
        return [part.strip() for part in raw.split(",") if part.strip()]

    async def proxy_app_websocket(self, app_id: str, rel_path: str, websocket: WebSocket, *, query_string: Optional[str] = None) -> None:
        """Bidirectionally proxy one client WebSocket to a Universe app target.

        This is the HTTP-proxy companion needed by Trame/wslink-style apps. The
        app must opt in with allow_proxy=true and provide websocket_url, proxy_url,
        or internal_port. Gateway callers can pass a sanitized query_string so
        Gateway auth tokens are not forwarded to the app.
        """
        target_url = self._app_websocket_target_url(
            app_id,
            rel_path,
            websocket.url.query if query_string is None else query_string,
        )
        requested_subprotocols = self._websocket_requested_subprotocols(websocket)
        try:
            async with websockets.connect(
                target_url,
                additional_headers=self._websocket_forward_headers(websocket),
                proxy=None,
                max_size=None,
                subprotocols=requested_subprotocols or None,
            ) as upstream:
                await websocket.accept(subprotocol=getattr(upstream, "subprotocol", None))
                async def client_to_upstream() -> None:
                    while True:
                        message = await websocket.receive()
                        if message.get("type") == "websocket.disconnect":
                            await upstream.close()
                            return
                        if message.get("bytes") is not None:
                            await upstream.send(message["bytes"])
                        elif message.get("text") is not None:
                            await upstream.send(message["text"])

                async def upstream_to_client() -> None:
                    async for message in upstream:
                        if isinstance(message, bytes):
                            await websocket.send_bytes(message)
                        else:
                            await websocket.send_text(message)

                tasks = {
                    asyncio.create_task(client_to_upstream()),
                    asyncio.create_task(upstream_to_client()),
                }
                done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in pending:
                    task.cancel()
                for task in done:
                    exc = task.exception()
                    if exc and not isinstance(exc, WebSocketDisconnect):
                        raise exc
        except WebSocketDisconnect:
            return
        finally:
            try:
                await websocket.close()
            except Exception:
                pass

    def proxy_app_http(self, app_id: str, rel_path: str, method: str, *, query_string: str = "", body: bytes = b"", headers: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        base = self._app_proxy_base_url(app_id)
        safe_path = self._safe_proxy_rel_path(rel_path)
        target = f"{base}/" + safe_path
        if query_string:
            target = f"{target}?{query_string}"
        forward_headers = {}
        for key, value in (headers or {}).items():
            lk = key.lower()
            if lk in {"host", "content-length", "connection", "transfer-encoding", "upgrade", "proxy-authorization", "proxy-authenticate"}:
                continue
            forward_headers[key] = value
        resp = requests.request(
            method=str(method or "GET").upper(),
            url=target,
            data=body if body else None,
            headers=forward_headers,
            timeout=30,
            allow_redirects=False,
        )
        response_headers = {}
        for key, value in resp.headers.items():
            lk = key.lower()
            if lk in {"content-length", "transfer-encoding", "connection", "content-encoding"}:
                continue
            response_headers[key] = value
        return {
            "status_code": resp.status_code,
            "content": resp.content,
            "headers": response_headers,
            "media_type": resp.headers.get("content-type"),
            "target_url": target,
        }


    def _managed_app_argv(self, app: "UniverseAppManifest") -> Optional[List[str]]:
        if app.command_args:
            return [str(x) for x in app.command_args]
        if app.entrypoint:
            return shlex.split(str(app.entrypoint))
        return None

    def _app_log_dir(self) -> Path:
        root = Path(tempfile.gettempdir()) / "wolf_universe_apps" / _safe_app_id(getattr(self.info, "name", "universe"))
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _refresh_app_process_state(self, key: str) -> None:
        app = self.Apps.get(key)
        proc = self.AppProcesses.get(key)
        if app is None or proc is None:
            return
        rc = proc.poll()
        app.pid = proc.pid
        app.returncode = rc
        if rc is not None and app.status in {"running", "starting", "restarting"}:
            app.status = "exited" if rc == 0 else "failed"
            app.updated_at = _utc_now_iso()
            app.logs.append(f"{app.updated_at} process exited returncode={rc}")
            app.logs = app.logs[-200:]
            self.AppProcesses.pop(key, None)

    def start_app(self, app_id: str) -> Dict[str, Any]:
        key = _safe_app_id(app_id)
        if key not in self.Apps:
            raise KeyError(f"Unknown Universe app: {app_id}")
        self._refresh_app_process_state(key)
        app = self.Apps[key]
        existing = self.AppProcesses.get(key)
        if existing is not None and existing.poll() is None:
            app.status = "running"
            app.pid = existing.pid
            app.returncode = None
            app.updated_at = _utc_now_iso()
            return app.model_dump(mode="json", exclude_none=True)

        argv = self._managed_app_argv(app)
        if not argv:
            return self.set_app_status(key, "running")

        log_dir = self._app_log_dir()
        stdout_path = log_dir / f"{key}.stdout.log"
        stderr_path = log_dir / f"{key}.stderr.log"
        env = os.environ.copy()
        env.update({str(k): str(v) for k, v in (app.env or {}).items()})
        stdout_f = open(stdout_path, "ab")
        stderr_f = open(stderr_path, "ab")
        try:
            proc = subprocess.Popen(
                argv,
                cwd=app.cwd or None,
                env=env,
                stdout=stdout_f,
                stderr=stderr_f,
                stdin=subprocess.DEVNULL,
                shell=False,
                start_new_session=True,
            )
        except Exception:
            stdout_f.close()
            stderr_f.close()
            raise
        stdout_f.close()
        stderr_f.close()
        self.AppProcesses[key] = proc
        app.pid = proc.pid
        app.returncode = None
        app.stdout_log = str(stdout_path)
        app.stderr_log = str(stderr_path)
        app.status = "running"
        app.updated_at = _utc_now_iso()
        app.logs.append(f"{app.updated_at} started pid={proc.pid} argv={argv!r}")
        app.logs = app.logs[-200:]
        return app.model_dump(mode="json", exclude_none=True)

    def stop_app(self, app_id: str, timeout: float = 5.0) -> Dict[str, Any]:
        key = _safe_app_id(app_id)
        if key not in self.Apps:
            raise KeyError(f"Unknown Universe app: {app_id}")
        app = self.Apps[key]
        proc = self.AppProcesses.get(key)
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=max(0.1, float(timeout or 5.0)))
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2)
            app.returncode = proc.returncode
            app.logs.append(f"{_utc_now_iso()} stopped pid={proc.pid} returncode={proc.returncode}")
            self.AppProcesses.pop(key, None)
        app.pid = None
        app.status = "stopped"
        app.updated_at = _utc_now_iso()
        app.logs.append(f"{app.updated_at} status=stopped")
        app.logs = app.logs[-200:]
        return app.model_dump(mode="json", exclude_none=True)

    def restart_app(self, app_id: str) -> Dict[str, Any]:
        key = _safe_app_id(app_id)
        self.stop_app(key)
        app = self.Apps[key]
        app.status = "restarting"
        app.updated_at = _utc_now_iso()
        app.logs.append(f"{app.updated_at} status=restarting")
        return self.start_app(key)

    def set_app_status(self, app_id: str, status: str) -> Dict[str, Any]:
        key = _safe_app_id(app_id)
        if key not in self.Apps:
            raise KeyError(f"Unknown Universe app: {app_id}")
        self._refresh_app_process_state(key)
        app = self.Apps[key]
        app.status = str(status or "unknown")
        app.updated_at = _utc_now_iso()
        app.logs.append(f"{app.updated_at} status={app.status}")
        app.logs = app.logs[-200:]
        return app.model_dump(mode="json", exclude_none=True)

    def delete_app(self, app_id: str) -> bool:
        key = _safe_app_id(app_id)
        if key in self.Apps:
            try:
                self.stop_app(key)
            except Exception:
                pass
        self.AppProcesses.pop(key, None)
        return self.Apps.pop(key, None) is not None

    def app_logs(self, app_id: str, tail: int = 200) -> Dict[str, Any]:
        key = _safe_app_id(app_id)
        if key not in self.Apps:
            raise KeyError(f"Unknown Universe app: {app_id}")
        self._refresh_app_process_state(key)
        app = self.Apps[key]
        limit = max(1, int(tail or 200))
        lines = list(app.logs or [])
        file_logs: Dict[str, List[str]] = {}
        for label, path_s in (("stdout", app.stdout_log), ("stderr", app.stderr_log)):
            if path_s and Path(path_s).exists():
                try:
                    file_lines = Path(path_s).read_text(errors="replace").splitlines()
                except Exception as exc:
                    file_lines = [f"<error reading {label} log: {exc}>"]
                file_logs[label] = file_lines[-limit:]
        return {
            "app_id": key,
            "logs": lines[-limit:],
            "file_logs": file_logs,
            "managed_process": bool(self._managed_app_argv(app)),
            "pid": app.pid,
            "returncode": app.returncode,
            "status": app.status,
        }

    def list_app_templates(self) -> List[Dict[str, Any]]:
        return _list_app_templates()

    def get_app_template(self, template_id: str) -> Dict[str, Any]:
        return _get_app_template(template_id).model_dump(mode="json")

    def instantiate_app_template(
        self,
        template_id: str,
        *,
        app_id: str | None = None,
        title: str | None = None,
        output_dir: str,
        context: Optional[Dict[str, Any]] = None,
        auto_register: bool = True,
        overwrite: bool = False,
    ) -> Dict[str, Any]:
        safe_id = _safe_app_id(app_id or title or template_id)
        rendered = render_app_template(
            template_id,
            output_dir=output_dir,
            app_id=safe_id,
            title=title,
            context=context or {},
            overwrite=overwrite,
        )
        manifest = dict(rendered["manifest"])
        manifest["app_id"] = safe_id
        result: Dict[str, Any] = {
            "ok": True,
            "template_id": template_id,
            "app_id": safe_id,
            "files": rendered["files"],
            "manifest": manifest,
            "registered": False,
        }
        if auto_register:
            app = self.register_app(manifest)
            result["registered"] = True
            result["app"] = app
            result["view_url"] = app.get("url")
            result["asset_base_url"] = f"/apps/{safe_id}/files/"
        return result



    # -----------------------------
    # Tool listing helpers
    # -----------------------------
    def get_available_tools(self) -> List[Dict[str, Any]]:
        """Return ToolCards for all tools across all ToolBoxes."""
        cards: List[Dict[str, Any]] = []
        for tb_name, tb in self.TBs.items():
            for tool_name, tool in tb.tools.items():
                cards.append({
                    "toolbox": tb_name,
                    "tool_name": tool_name,
                    "toolcard": tool.card.__dict__,
                })
        return cards

    def get_toolbox_tools(self, tb_name: str) -> List[Dict[str, Any]]:
        tb = self.get_tb(tb_name)
        return [
            {"toolbox": tb_name, "tool_name": name, "toolcard": tool.card.__dict__}
            for name, tool in tb.tools.items()
        ]


# --------------------
# FastAPI models
# --------------------
class UniverseAppManifest(BaseModel):
    app_id: str = Field(default="", description="Stable app id unique within this Universe")
    title: str = Field(default="", description="Human-readable app title")
    kind: str = Field(default="custom", description="App kind, e.g. plot, table, mesh, cad, trame_mesh, video, audio, report")
    backend: str = Field(default="url", description="App backend/type, e.g. url, static, process, trame, proxy")
    status: str = Field(default="registered", description="App lifecycle status")
    url: str = Field(default="", description="App URL. Relative URLs are resolved against the Universe base URL by clients/actions")
    description: str = ""
    artifacts: List[str] = Field(default_factory=list)
    preferred_panel: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    # Phase 2 managed-process metadata. command_args is preferred because it is
    # executed with shell=False. entrypoint is split with shlex for compatibility.
    command_args: Optional[List[str]] = Field(default=None, description="Managed-process argv executed with shell=False")
    entrypoint: Optional[str] = Field(default=None, description="Compatibility command string, split with shlex and executed with shell=False")
    cwd: Optional[str] = Field(default=None, description="Optional working directory for managed process apps")
    env: Dict[str, str] = Field(default_factory=dict, description="Optional environment overrides for managed process apps")
    internal_port: Optional[int] = Field(default=None, description="Internal app port, if known")
    static_dir: Optional[str] = Field(default=None, description="Directory containing static app files served under /apps/{app_id}/files")
    index_file: str = Field(default="index.html", description="Static app index file served by /apps/{app_id}/view")
    proxy_url: Optional[str] = Field(default=None, description="Base URL for HTTP proxy-backed apps")
    websocket_url: Optional[str] = Field(default=None, description="Optional explicit ws:// or wss:// base URL for WebSocket proxy-backed apps")
    allow_proxy: bool = Field(default=False, description="Explicit opt-in required to proxy HTTP requests to proxy_url/internal_port")
    pid: Optional[int] = Field(default=None, description="Running managed-process PID, if any")
    returncode: Optional[int] = Field(default=None, description="Last managed-process return code")
    stdout_log: Optional[str] = Field(default=None, description="Path to stdout log for managed process apps")
    stderr_log: Optional[str] = Field(default=None, description="Path to stderr log for managed process apps")
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    logs: List[str] = Field(default_factory=list)


class UniverseAppStatusRequest(BaseModel):
    status: Optional[str] = Field(default=None, description="Optional explicit status override")


class InstantiateAppTemplateRequest(BaseModel):
    app_id: Optional[str] = Field(default=None, description="Stable app id; normalized by the Universe")
    title: Optional[str] = Field(default=None, description="Optional generated app title")
    output_dir: str = Field(..., description="Target directory for generated static app files")
    context: Dict[str, Any] = Field(default_factory=dict, description="Template variable values")
    auto_register: bool = Field(default=True, validation_alias=AliasChoices("auto_register", "register"), description="Register the rendered app manifest with this Universe")
    overwrite: bool = Field(default=False, description="Allow replacing existing generated files")


class CreateKBRequest(BaseModel):
    kb_params: KnowledgeBaseParams | MultimodalKnowledgeBaseParams = Field(..., description="Parameters of the KB")
    type: str = Field("text", description="Type of KB: 'text' for text-only or 'multimodal' for multimodal KB")


class CreateTBRequest(BaseModel):
    name: str
    index_persist_dir: str
    embedding_model: str = "all-MiniLM-L6-v2"
    inventory_path: Optional[str] = None
    registry_path: Optional[str] = None
    vrbz: int = 0


class SearchRequest(BaseModel):
    query: str
    k: int = 5
    context_window: int = 1


class AppendTextsRequest(BaseModel):
    texts: List[str]
    doc_source: str = "universe"


class UploadDirRequest(BaseModel):
    dir_path: str
    target_ext: Optional[List[str]] = None


class AddURLRequest(BaseModel):
    url: str


class AddURLsRequest(BaseModel):
    urls: List[str]


class AddDocumentRequest(BaseModel):
    content: str = Field(..., description="Content of the document (text, base64-encoded data, or file path)")
    metadata: Optional[Dict[str, Any]] = Field(None, description="Optional metadata for the document")
    modality: str = Field("text", description="Modality type: 'text', 'image', 'audio', 'video', 'table', 'binary'")


class ExecuteRequest(BaseModel):
    tool_name: str
    args: Optional[List[str]] = None
    fn_args: Optional[List[Any]] = None
    kwargs: Optional[Dict[str, Any]] = None
    env: Optional[Dict[str, str]] = None
    cwd: Optional[str] = None
    timeout: Optional[float] = None
    input_data: Optional[str] = None
    text: bool = True


class AddToolFromMetaRequest(BaseModel):
    meta: Dict[str, Any]  # ToolMeta as dict
    kb_persist_dir: str


class RecursiveUploadToolsRequest(BaseModel):
    path: str
    extensions: Optional[List[str]] = None
    kb_persist_dir: Optional[str] = None


# --------------------
# FastAPI factory
# --------------------
def create_app(universe: BaseUniverse, cors_origins: Optional[List[str]] = None) -> FastAPI:
    app = FastAPI(title="WOLF Universe", version="2.0.0")

    if cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    # -------- Discovery & actions --------
    @app.get("/health")
    def health():
        return {"status": "ok", "kbs": universe.list_kbs(), "tbs": universe.list_tbs(), "apps": universe.list_apps()}

    @app.get("/actions")
    def get_allowed_actions():
        return universe.allowed_actions()

    @app.get("/info")
    def discovery_info():
        return universe.get_info()

    @app.get("/stats")
    def get_stats():
        return universe.get_stats()

    @app.get("/tools")
    def all_tools():
        return universe.get_available_tools()

    # --------------- Universe app template endpoints ---------------
    @app.get("/app-templates")
    def list_app_templates_route():
        templates = universe.list_app_templates()
        return {"ok": True, "templates": templates, "count": len(templates)}

    @app.get("/app-templates/{template_id}")
    def get_app_template_route(template_id: str):
        try:
            return {"ok": True, "template": universe.get_app_template(template_id)}
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc))

    @app.post("/app-templates/{template_id}/instantiate")
    def instantiate_app_template_route(template_id: str, req: InstantiateAppTemplateRequest):
        try:
            return universe.instantiate_app_template(
                template_id,
                app_id=req.app_id,
                title=req.title,
                output_dir=req.output_dir,
                context=req.context,
                auto_register=req.auto_register,
                overwrite=req.overwrite,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except FileExistsError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        except (ValueError, PermissionError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    # --------------- Universe app endpoints ---------------
    @app.get("/apps")
    def list_apps():
        apps = universe.list_apps()
        return {"ok": True, "apps": apps, "count": len(apps)}

    @app.post("/apps")
    def register_app(req: UniverseAppManifest):
        return {"ok": True, "app": universe.register_app(req)}

    @app.post("/apps/register")
    def register_app_alias(req: UniverseAppManifest):
        return {"ok": True, "app": universe.register_app(req)}

    @app.get("/apps/{app_id}")
    def get_app(app_id: str):
        try:
            return {"ok": True, "app": universe.get_app(app_id)}
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc))

    @app.get("/apps/{app_id}/manifest")
    def get_app_manifest(app_id: str):
        try:
            return universe.get_app(app_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc))

    @app.get("/apps/{app_id}/view")
    def view_static_app(app_id: str):
        try:
            return FileResponse(universe._resolve_static_app_file(app_id))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc))
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc))

    @app.get("/apps/{app_id}/files/{asset_path:path}")
    def static_app_file(app_id: str, asset_path: str):
        try:
            return FileResponse(universe._resolve_static_app_file(app_id, asset_path))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc))
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc))

    @app.api_route("/apps/{app_id}/proxy/{proxy_path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    async def proxy_app_http(app_id: str, proxy_path: str, request: Request):
        try:
            result = universe.proxy_app_http(
                app_id,
                proxy_path,
                request.method,
                query_string=request.url.query,
                body=await request.body(),
                headers=dict(request.headers),
            )
            return Response(
                content=result["content"],
                status_code=result["status_code"],
                media_type=result.get("media_type"),
                headers=result.get("headers") or {},
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc))
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except requests.RequestException as exc:
            raise HTTPException(status_code=502, detail=f"Proxy request failed: {exc}")

    @app.websocket("/apps/{app_id}/proxy/{proxy_path:path}")
    async def proxy_app_websocket(app_id: str, proxy_path: str, websocket: WebSocket):
        try:
            await universe.proxy_app_websocket(app_id, proxy_path, websocket)
        except KeyError:
            await websocket.close(code=1008, reason="Unknown app")
        except PermissionError:
            await websocket.close(code=1008, reason="Permission denied")
        except (FileNotFoundError, ValueError):
            await websocket.close(code=1008, reason="Invalid app proxy target")
        except Exception:
            await websocket.close(code=1011, reason="WebSocket proxy failed")

    @app.post("/apps/{app_id}/start")
    def start_app(app_id: str, req: UniverseAppStatusRequest | None = None):
        try:
            return {"ok": True, "app": universe.set_app_status(app_id, req.status) if req and req.status else universe.start_app(app_id)}
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc))

    @app.post("/apps/{app_id}/stop")
    def stop_app(app_id: str, req: UniverseAppStatusRequest | None = None):
        try:
            return {"ok": True, "app": universe.set_app_status(app_id, req.status) if req and req.status else universe.stop_app(app_id)}
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc))

    @app.post("/apps/{app_id}/restart")
    def restart_app(app_id: str):
        try:
            return {"ok": True, "app": universe.restart_app(app_id)}
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc))

    @app.delete("/apps/{app_id}")
    def delete_app(app_id: str):
        if not universe.delete_app(app_id):
            raise HTTPException(status_code=404, detail="App not found")
        return {"ok": True, "app_id": app_id}

    @app.get("/apps/{app_id}/logs")
    def app_logs(app_id: str, tail: int = 200):
        try:
            return {"ok": True, **universe.app_logs(app_id, tail=tail)}
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc))

    # --------------- KB endpoints ---------------
    @app.get("/kbs")
    def list_kbs():
        return universe.list_kbs()

    @app.post("/kbs")
    def create_kb(req: CreateKBRequest):
        kb_type = req.type.lower()

        if kb_type == "text":
            if not isinstance(req.kb_params, KnowledgeBaseParams):
                raise HTTPException(status_code=400, detail="For 'text' type, kb_params must be KnowledgeBaseParams")
            if req.kb_params.name in universe.KBs:
                raise HTTPException(status_code=409, detail=f"KB {req.kb_params.name} already exists")
            kb = KnowledgeBase(req.kb_params, universe.db_client)
            universe.add_kb(req.kb_params.name, kb)
            return {"ok": True, "name": req.kb_params.name, "type": "text"}

        elif kb_type == "multimodal":
            if not isinstance(req.kb_params, MultimodalKnowledgeBaseParams):
                raise HTTPException(status_code=400, detail="For 'multimodal' type, kb_params must be MultimodalKnowledgeBaseParams")
            if req.kb_params.name in universe.KBs:
                raise HTTPException(status_code=409, detail=f"KB {req.kb_params.name} already exists")
            kb = MultimodalKnowledgeBase(req.kb_params, universe.db_client)
            universe.add_kb(req.kb_params.name, kb)
            return {"ok": True, "name": req.kb_params.name, "type": "multimodal"}

        else:
            raise HTTPException(status_code=400, detail=f"Invalid KB type: {req.type}. Must be 'text' or 'multimodal'")

    @app.delete("/kbs/{name}")
    def delete_kb(name: str):
        if not universe.remove_kb(name):
            raise HTTPException(status_code=404, detail="KB not found")
        return {"ok": True}

    @app.post("/kbs/{name}/search")
    async def kb_search(name: str, req: SearchRequest):
        try:
            return await universe.akb_search(name, req.query, k=req.k, context_window=req.context_window)
        except KeyError:
            raise HTTPException(status_code=404, detail="KB not found")

    @app.get("/kbs/{name}/stats")
    def kb_stats(name: str):
        try:
            return universe.kb_stats(name)
        except KeyError:
            raise HTTPException(status_code=404, detail="KB not found")

    @app.get("/kbs/{name}/sources")
    def kb_sources(name: str):
        try:
            return universe.kb_sources(name)
        except KeyError:
            raise HTTPException(status_code=404, detail="KB not found")

    @app.post("/kbs/{name}/append_texts")
    async def kb_append(name: str, req: AppendTextsRequest):
        try:
            return await universe.akb_append_texts(name, req.texts, doc_source=req.doc_source)
        except KeyError:
            raise HTTPException(status_code=404, detail="KB not found")

    @app.post("/kbs/{name}/upload_dir")
    async def kb_upload_dir(name: str, req: UploadDirRequest):
        """Upload directory contents to a knowledge base."""
        try:
            # Get KB to check it exists
            kb = universe.get_kb(name)
        except KeyError:
            raise HTTPException(
                status_code=404,
                detail=f"KB '{name}' not found. Universe is running on host: {universe.info.host if universe.info else 'unknown'}"
            )
        
        # Expand user path and validate directory exists
        dir_path = os.path.expanduser(req.dir_path)
        if not os.path.exists(dir_path):
            raise HTTPException(
                status_code=400,
                detail=f"Directory '{req.dir_path}' does not exist on universe host {universe.info.host if universe.info else 'unknown'}:{universe.info.port if universe.info else 'unknown'}. Please verify the path is accessible from the universe's runtime environment."
            )
        
        if not os.path.isdir(dir_path):
            raise HTTPException(
                status_code=400,
                detail=f"Path '{req.dir_path}' exists but is not a directory on host {universe.info.host if universe.info else 'unknown'}. Please provide a valid directory path."
            )
        
        try:
            # Attempt to upload
            result = await universe.akb_upload_dir(name, dir_path, target_ext=req.target_ext)
            
            # Provide detailed feedback
            return {
                "ok": True,
                "kb_name": name,
                "dir_path": req.dir_path,
                "host": universe.info.host if universe.info else "unknown",
                "port": universe.info.port if universe.info else "unknown",
                "result": result,
                "message": f"Successfully uploaded contents from '{req.dir_path}' to KB '{name}'"
            }
        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail=f"Error uploading directory to KB '{name}' on host {universe.info.host if universe.info else 'unknown'}:{universe.info.port if universe.info else 'unknown'}. Error: {str(e)}"
            )

    @app.post("/kbs/{name}/add_url")
    async def kb_add_url(name: str, req: AddURLRequest):
        try:
            return await universe.akb_add_url(name, req.url)
        except KeyError:
            raise HTTPException(status_code=404, detail="KB not found")

    @app.post("/kbs/{name}/add_urls")
    async def kb_add_urls(name: str, req: AddURLsRequest):
        try:
            return await universe.akb_add_urls(name, req.urls)
        except KeyError:
            raise HTTPException(status_code=404, detail="KB not found")

    @app.post("/kbs/{name}/add_document")
    async def kb_add_document(name: str, req: AddDocumentRequest):
        """Add a single document to a multimodal knowledge base."""
        try:
            # Convert content based on modality
            if req.modality == "text":
                content = req.content
            else:
                # For non-text modalities, assume content is a file path or base64 data
                # Try as file path first
                if os.path.exists(req.content):
                    content = Path(req.content)
                else:
                    # Assume it's base64-encoded bytes
                    import base64
                    try:
                        content = base64.b64decode(req.content)
                    except Exception:
                        raise HTTPException(
                            status_code=400,
                            detail=f"For modality '{req.modality}', content must be a valid file path or base64-encoded data"
                        )

            result = await universe.akb_add_document(name, content, metadata=req.metadata, modality=req.modality)
            return {
                "ok": True,
                "kb_name": name,
                "modality": req.modality,
                "result": result,
                "message": f"Successfully added {req.modality} document to KB '{name}'"
            }
        except KeyError:
            raise HTTPException(status_code=404, detail="KB not found")
        except TypeError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail=f"Error adding document to KB '{name}': {str(e)}"
            )

    @app.post("/kbs/{name}/purge")
    async def kb_purge(name: str):
        try:
            await universe.akb_purge(name)
            return {"ok": True}
        except KeyError:
            raise HTTPException(status_code=404, detail="KB not found")

    @app.get("/kbs/{name}/document/{document_id}")
    def kb_get_document(name: str, document_id: str):
        try:
            doc = universe.kb_get_document_by_id(name, document_id)
            if doc is None:
                raise HTTPException(status_code=404, detail="Document not found")
            return doc
        except KeyError:
            raise HTTPException(status_code=404, detail="KB not found")

    # --------------- TB endpoints ---------------
    @app.get("/tbs")
    def list_tbs():
        return universe.list_tbs()

    @app.post("/tbs")
    def create_tb(req: CreateTBRequest):
        if req.name in universe.TBs:
            raise HTTPException(status_code=409, detail="TB already exists")
        params = {
            "name": req.name,
            "index_persist_dir": req.index_persist_dir,
            "embedding_model": req.embedding_model,
            "inventory_path": req.inventory_path,
            "registry_path": req.registry_path,
            "vrbz": req.vrbz,
        }
        tb = ToolBox(params)
        universe.add_tb(req.name, tb)
        return {"ok": True, "name": req.name}

    @app.delete("/tbs/{name}")
    def delete_tb(name: str):
        if not universe.remove_tb(name):
            raise HTTPException(status_code=404, detail="TB not found")
        return {"ok": True}

    @app.post("/tbs/{name}/search")
    async def tb_search(name: str, req: SearchRequest):
        try:
            return await universe.atb_search_tools(name, req.query, k=req.k)
        except KeyError:
            raise HTTPException(status_code=404, detail="TB not found")

    @app.get("/tbs/{name}/tools")
    def tb_list_tools(name: str):
        try:
            return universe.get_toolbox_tools(name)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e))

    @app.get("/tbs/{name}/stats")
    def tb_get_stats(name: str):
        try:
            return universe.tb_get_stats(name)
        except KeyError:
            raise HTTPException(status_code=404, detail="TB not found")

    @app.get("/tbs/{name}/tools/{tool}/info")
    def tb_tool_info(name: str, tool: str):
        try:
            info = universe.tb_tool_info(name, tool)
            if info is None:
                raise HTTPException(status_code=404, detail="Tool not found in TB")
            return info
        except KeyError:
            raise HTTPException(status_code=404, detail="TB not found")

    @app.post("/tbs/{name}/tools/{tool}/append_texts")
    async def tb_append_docs(name: str, tool: str, req: AppendTextsRequest):
        try:
            return await universe.atb_append_docs(name, tool, req.texts, doc_source=req.doc_source)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e))

    @app.post("/tbs/{name}/tools/{tool}/search_docs")
    async def tb_search_tool_docs(name: str, tool: str, req: SearchRequest):
        try:
            return await universe.atb_search_tool_docs(name, tool, req.query, k=req.k, context_window=req.context_window)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e))

    @app.post("/tbs/{name}/add_tool_from_meta")
    async def tb_add_tool_from_meta(name: str, req: AddToolFromMetaRequest):
        try:
            meta = ToolMeta(**req.meta)
            return await universe.atb_add_tool_from_meta(name, meta, kb_persist_dir=req.kb_persist_dir)
        except KeyError:
            raise HTTPException(status_code=404, detail="TB not found")

    @app.post("/tbs/{name}/recursive_upload_tools")
    async def tb_recursive_upload_tools(name: str, req: RecursiveUploadToolsRequest):
        try:
            return await universe.atb_recursive_upload_tools(name, req.path, extensions=req.extensions, kb_persist_dir=req.kb_persist_dir)
        except KeyError:
            raise HTTPException(status_code=404, detail="TB not found")

    @app.post("/tbs/{name}/execute")
    async def tb_execute(name: str, req: ExecuteRequest):
        try:
            if req.fn_args is not None:
                args_for_fn = list(req.fn_args)
                return await universe.atb_execute(
                    name,
                    req.tool_name,
                    *args_for_fn,
                    args=req.args,
                    kwargs=req.kwargs or {},
                    env=req.env,
                    cwd=req.cwd,
                    timeout=req.timeout,
                    input_data=req.input_data,
                    text=req.text,
                )
            else:
                return await universe.atb_execute(
                    name,
                    req.tool_name,
                    args=req.args,
                    kwargs=req.kwargs or {},
                    env=req.env,
                    cwd=req.cwd,
                    timeout=req.timeout,
                    input_data=req.input_data,
                    text=req.text,
                )
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e))

    return app


def build_default_universe(params: base_universe_params_type|None = None) -> BaseUniverse:
    """Create an base Universe. Extend this in your app bootstrap."""
    if params is not None: 
        return BaseUniverse(params=params)
    else:
        name_generator = NameGenerator()
        info = BaseUniverseModel(name=name_generator.get_name())
        _params = BaseUniverseParams(info=info)
        return BaseUniverse(params=_params)

def create_app_default() -> FastAPI:
    """Zero-argument ASGI factory for uvicorn --factory."""
    universe = build_default_universe()
    return create_app(universe)


def run_app(
    params: BaseUniverseParams | None = None,
    host: str = "127.0.0.1",
    port: int = 0,
    cors: Optional[List[str]] = None,
    status_file: Optional[str] = None,
) -> None:
    import json
    from pathlib import Path
    from datetime import datetime, timezone
    
    host = host.strip()

    if params is None:
        name_generator = NameGenerator()
        info = BaseUniverseModel(
            name=name_generator.get_name(),
            host=host,
            port=port,
        )
        _params = BaseUniverseParams(info=info)
    else:
        _params = params
        if _params.info is None:
            raise ValueError("params.info must not be None")
        _params.info.host = host
        _params.info.port = port

    universe = build_default_universe(_params)
    app = create_app(universe, cors_origins=cors)

    # Pre-bind socket ourselves so we know the real port before Uvicorn starts.
    import socket
    import uvicorn
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, port))          # port=0 => OS picks a free port
    sock.listen()
    actual_port = sock.getsockname()[1]

    _params.info.port = actual_port
    base_url = _params.info.get_base_url()

    # Write status file with complete information after socket binding
    if status_file:
        try:
            status_data = {
                "schema_version": 1,
                "status": "ready",
                "name": getattr(_params.info, "name", None),
                "scheme": "http",
                "host": host,
                "port": actual_port,
                "url": base_url,
                "pid": os.getpid(),
                "started_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            }
            write_status_atomic(status_file, status_data)
        except Exception as e:
            print(f"Warning: Failed to write status file: {e}")

    print(f"Universe available at {base_url}")

    config = uvicorn.Config(app=app, host=host, port=actual_port)
    server = uvicorn.Server(config)
    server.run(sockets=[sock])
