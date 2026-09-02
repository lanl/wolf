from __future__ import annotations

import pathlib
import asyncio
import re
import concurrent.futures
import json
import os
import sqlite3
import uuid
import traceback
import hashlib
from typing import Any, Dict, Iterable, List, Optional, Union, Tuple
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Core imports
# ---------------------------------------------------------------------------
import chromadb
from framework.data_store.data_models import MultimodalVectorStoreParams
from framework.data_store.multimodal_vstore import MultimodalVectorStore
from framework.knowledgebase.data_models import MultimodalKnowledgeBaseParams

# PDF parsing imports
try:
    import fitz  # PyMuPDF
    PYMUPDF_AVAILABLE = True
except ImportError:
    PYMUPDF_AVAILABLE = False

# ---------------------------------------------------------------------------
# Inventory (SQLite) schema for multimodal KB
# ---------------------------------------------------------------------------

MULTIMODAL_INVENTORY_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_path TEXT,
    doc_type   TEXT,
    modality   TEXT,
    v_ids_json TEXT,
    n_chunks   INTEGER,
    n_tokens   INTEGER,
    added_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_documents_source_path ON documents(source_path);
CREATE INDEX IF NOT EXISTS idx_documents_modality ON documents(modality);

CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    v_id       TEXT,
    source_path TEXT,
    modality   TEXT,
    line_start INTEGER,
    line_end   INTEGER,
    position   INTEGER,
    n_tokens   INTEGER,
    metadata_json TEXT,
    added_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_chunks_v_id ON chunks(v_id);
CREATE INDEX IF NOT EXISTS idx_chunks_source ON chunks(source_path);
CREATE INDEX IF NOT EXISTS idx_chunks_modality ON chunks(modality);
"""


FIGURE_CAPTION_START_RE = re.compile(
    r"^\s*((?:Fig\.?|Figure|FIGURE|Image)\s*\d+[A-Za-z]?(?:\s*[.:\-]|\s*\([A-Za-z0-9]+\))?)",
    re.IGNORECASE,
)
OTHER_CAPTION_START_RE = re.compile(
    r"^\s*(?:Fig\.?|Figure|FIGURE|Image|Table)\s*\d+[A-Za-z]?",
    re.IGNORECASE,
)


def _now_iso() -> str:
    dt = datetime.now(timezone.utc).replace(microsecond=0)
    return dt.isoformat().replace("+00:00", "Z")


def _count_tokens(text: str) -> int:
    return len(str(text).split())


SupportedModalities = Union[
    str,
    pathlib.Path,
    bytes,
]


# ---------------------------------------------------------------------------
# Enhanced PDF extraction helpers with FLAT metadata
# ---------------------------------------------------------------------------

def _flatten_spatial_metadata(nested: Dict[str, Any], prefix: str = "") -> Dict[str, Any]:
    flat = {}
    for key, value in nested.items():
        new_key = f"{prefix}_{key}" if prefix else key

        if isinstance(value, dict):
            flat.update(_flatten_spatial_metadata(value, new_key))
        elif isinstance(value, (str, int, float, bool)) or value is None:
            flat[new_key] = value
        elif isinstance(value, list):
            try:
                if all(isinstance(item, (str, int, float, bool)) or item is None for item in value):
                    flat[new_key] = value
                else:
                    flat[new_key] = json.dumps(value)
            except Exception:
                flat[new_key] = str(value)
        else:
            flat[new_key] = str(value)

    return flat


def _rows_to_csv(rows: list[list[str]]) -> str:
    return "\n".join(
        [",".join("" if cell is None else str(cell) for cell in r) for r in rows]
    )


def _normalize_caption_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _bbox_vertical_gap(a: Tuple[float, float, float, float], b: Tuple[float, float, float, float]) -> float:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    if by0 >= ay1:
        return by0 - ay1
    if ay0 >= by1:
        return ay0 - by1
    return 0.0


def _bbox_horizontal_overlap_ratio(a: Tuple[float, float, float, float], b: Tuple[float, float, float, float]) -> float:
    ax0, _ay0, ax1, _ay1 = a
    bx0, _by0, bx1, _by1 = b
    overlap = max(0.0, min(ax1, bx1) - max(ax0, bx0))
    base = max(1.0, min(ax1 - ax0, bx1 - bx0))
    return overlap / base


def _extract_text_blocks(page: "fitz.Page") -> List[Dict[str, Any]]:
    blocks = page.get_text("dict").get("blocks", [])
    out: List[Dict[str, Any]] = []

    for block_index, block in enumerate(blocks):
        lines = block.get("lines") or []
        if not lines:
            continue

        line_texts: List[str] = []
        font_sizes: List[float] = []
        for line in lines:
            spans = line.get("spans") or []
            line_text = " ".join(span.get("text", "") for span in spans).strip()
            if line_text:
                line_texts.append(line_text)
            for span in spans:
                size = span.get("size")
                if isinstance(size, (int, float)):
                    font_sizes.append(float(size))

        text = _normalize_caption_text(" ".join(line_texts))
        if not text:
            continue

        bbox = tuple(block.get("bbox", [0, 0, 0, 0]))
        out.append({
            "block_index": block_index,
            "text": text,
            "bbox": bbox,
            "y_center": (bbox[1] + bbox[3]) / 2,
            "x_center": (bbox[0] + bbox[2]) / 2,
            "font_size_max": max(font_sizes) if font_sizes else 0.0,
        })

    out.sort(key=lambda b: (b["bbox"][1], b["bbox"][0], b["block_index"]))
    return out


def _extract_section_context(page: "fitz.Page", page_text: str) -> Dict[str, Any]:
    try:
        blocks = page.get_text("dict")["blocks"]
        sections = []
        font_sizes = []
        for block in blocks:
            if "lines" in block:
                for line in block["lines"]:
                    for span in line["spans"]:
                        font_sizes.append(span.get("size", 0))

        if not font_sizes:
            return {"sections": [], "hierarchy": []}

        avg_size = sum(font_sizes) / len(font_sizes)
        heading_threshold = avg_size * 1.2

        for block in blocks:
            if "lines" in block:
                for line in block["lines"]:
                    line_text = " ".join(span["text"] for span in line["spans"])
                    if not line_text.strip():
                        continue
                    max_font_size = max((span.get("size", 0) for span in line["spans"]), default=0)
                    if max_font_size >= heading_threshold:
                        if re.match(r'^\d+\.', line_text) or re.match(r'^[A-Z][A-Za-z\s]+$', line_text):
                            sections.append({
                                "text": line_text.strip(),
                                "font_size": max_font_size,
                                "bbox": block.get("bbox", [0, 0, 0, 0])
                            })

        return {"sections": sections, "hierarchy": sections}
    except Exception as e:
        return {"sections": [], "hierarchy": [], "error": str(e)}


def _get_page_region(bbox: Tuple[float, float, float, float], page_height: float, page_width: float) -> str:
    x0, y0, x1, y1 = bbox
    header_threshold = page_height * 0.1
    footer_threshold = page_height * 0.9
    left_margin_threshold = page_width * 0.1
    right_margin_threshold = page_width * 0.9

    if y0 < header_threshold:
        return "header"
    elif y1 > footer_threshold:
        return "footer"
    elif x0 < left_margin_threshold:
        return "left_margin"
    elif x1 > right_margin_threshold:
        return "right_margin"
    else:
        return "body"


def _extract_surrounding_text(page: "fitz.Page", element_bbox: Tuple[float, float, float, float],
                             context_chars: int = 200) -> Dict[str, str]:
    try:
        blocks = page.get_text("dict")["blocks"]
        all_text_blocks = []

        for block in blocks:
            if "lines" in block:
                block_bbox = block.get("bbox", [0, 0, 0, 0])
                block_text = " ".join(
                    " ".join(span["text"] for span in line["spans"])
                    for line in block["lines"]
                )
                if block_text.strip():
                    all_text_blocks.append({
                        "text": block_text,
                        "bbox": block_bbox,
                        "y_center": (block_bbox[1] + block_bbox[3]) / 2
                    })

        all_text_blocks.sort(key=lambda b: b["y_center"])
        element_y_center = (element_bbox[1] + element_bbox[3]) / 2
        preceding_blocks = [b for b in all_text_blocks if b["y_center"] < element_y_center]
        following_blocks = [b for b in all_text_blocks if b["y_center"] > element_y_center]

        preceding_text = ""
        if preceding_blocks:
            preceding_text = " ".join(b["text"] for b in preceding_blocks[-3:])
            if len(preceding_text) > context_chars:
                preceding_text = "..." + preceding_text[-context_chars:]

        following_text = ""
        if following_blocks:
            following_text = " ".join(b["text"] for b in following_blocks[:3])
            if len(following_text) > context_chars:
                following_text = following_text[:context_chars] + "..."

        return {
            "preceding_text": preceding_text.strip(),
            "following_text": following_text.strip()
        }
    except Exception as e:
        return {
            "preceding_text": "",
            "following_text": "",
            "error": str(e)
        }


def _find_text_anchors(page_text: str, element_type: str = "image") -> List[str]:
    anchors = []
    if element_type == "image":
        fig_pattern = r'(?:Figure|Fig\.?|Image)\s*\d+'
        anchors.extend(re.findall(fig_pattern, page_text, re.IGNORECASE))
    elif element_type == "table":
        table_pattern = r'Table\s*\d+'
        anchors.extend(re.findall(table_pattern, page_text, re.IGNORECASE))

    generic_patterns = [
        r'shown\s+(?:below|above|in\s+(?:Figure|Table)\s*\d+)',
        r'illustrated\s+(?:below|above|in)',
        r'depicted\s+(?:below|above|in)',
        r'as\s+shown',
        r'see\s+(?:Figure|Table)\s*\d+'
    ]

    for pattern in generic_patterns:
        anchors.extend(re.findall(pattern, page_text, re.IGNORECASE))

    return list(set(anchors))


def _extract_figure_caption(page: "fitz.Page", element_bbox: Tuple[float, float, float, float]) -> Dict[str, Any]:
    try:
        text_blocks = _extract_text_blocks(page)
        if not text_blocks:
            return {}

        page_height = float(page.rect.height or 0.0)
        if page_height <= 0:
            page_height = 1000.0

        x0, y0, x1, y1 = element_bbox
        search_specs = [
            ("below", lambda b: b["bbox"][1] >= y1 - 2.0),
            ("above", lambda b: b["bbox"][3] <= y0 + 2.0),
        ]

        for position, predicate in search_specs:
            candidates: List[Tuple[float, Dict[str, Any]]] = []
            for block in text_blocks:
                if not predicate(block):
                    continue
                text = block["text"]
                if not FIGURE_CAPTION_START_RE.match(text):
                    continue

                gap = _bbox_vertical_gap(element_bbox, block["bbox"])
                overlap = _bbox_horizontal_overlap_ratio(element_bbox, block["bbox"])
                center_distance = abs(((block["bbox"][0] + block["bbox"][2]) / 2) - ((x0 + x1) / 2))
                score = gap - (120.0 * overlap) + (0.05 * center_distance)
                candidates.append((score, block))

            if not candidates:
                continue

            candidates.sort(key=lambda item: item[0])
            start_block = candidates[0][1]
            start_text = start_block["text"]
            label_match = FIGURE_CAPTION_START_RE.match(start_text)
            caption_label = _normalize_caption_text(label_match.group(1)) if label_match else ""

            caption_blocks = [start_block]
            start_index = next((i for i, b in enumerate(text_blocks) if b["block_index"] == start_block["block_index"]), None)
            if start_index is None:
                start_index = text_blocks.index(start_block)

            max_gap = max(18.0, page_height * 0.025)
            caption_left = start_block["bbox"][0]
            caption_right = start_block["bbox"][2]
            caption_font = start_block.get("font_size_max", 0.0)

            for next_block in text_blocks[start_index + 1:]:
                if position == "below" and next_block["bbox"][1] < start_block["bbox"][1]:
                    continue
                if position == "above" and next_block["bbox"][1] < start_block["bbox"][1]:
                    continue

                next_text = next_block["text"]
                if OTHER_CAPTION_START_RE.match(next_text):
                    break

                prev_block = caption_blocks[-1]
                vertical_gap = max(0.0, next_block["bbox"][1] - prev_block["bbox"][3])
                if vertical_gap > max_gap:
                    break

                horizontal_overlap = _bbox_horizontal_overlap_ratio(prev_block["bbox"], next_block["bbox"])
                left_shift = abs(next_block["bbox"][0] - caption_left)
                right_shift = abs(next_block["bbox"][2] - caption_right)
                similar_font = abs(float(next_block.get("font_size_max", 0.0)) - float(caption_font)) <= max(1.5, caption_font * 0.2 if caption_font else 2.0)

                if horizontal_overlap < 0.15 and left_shift > 80 and right_shift > 80:
                    break
                if not similar_font and vertical_gap > (max_gap * 0.5):
                    break

                caption_blocks.append(next_block)
                caption_left = min(caption_left, next_block["bbox"][0])
                caption_right = max(caption_right, next_block["bbox"][2])

            caption_text = _normalize_caption_text(" ".join(block["text"] for block in caption_blocks))
            if not caption_text:
                continue

            confidence = "high"
            start_gap = _bbox_vertical_gap(element_bbox, start_block["bbox"])
            start_overlap = _bbox_horizontal_overlap_ratio(element_bbox, start_block["bbox"])
            if start_gap > max_gap or start_overlap < 0.2:
                confidence = "medium"
            if start_gap > max_gap * 2 or start_overlap < 0.05:
                confidence = "low"

            return {
                "caption": caption_text,
                "caption_label": caption_label,
                "caption_position": position,
                "caption_blocks": len(caption_blocks),
                "caption_source": "pdf_geometric_extraction",
                "caption_confidence": confidence,
            }

        return {}
    except Exception as e:
        return {"caption_extraction_error": str(e)}


def _get_element_spatial_metadata(page: "fitz.Page", element_bbox: Tuple[float, float, float, float],
                                 element_type: str, page_num: int) -> Dict[str, Any]:
    page_rect = page.rect
    page_width = page_rect.width
    page_height = page_rect.height
    page_text = page.get_text("text")
    x0, y0, x1, y1 = element_bbox

    spatial_metadata = {
        "bbox_x0": round(x0, 2),
        "bbox_y0": round(y0, 2),
        "bbox_x1": round(x1, 2),
        "bbox_y1": round(y1, 2),
        "bbox_width": round(x1 - x0, 2),
        "bbox_height": round(y1 - y0, 2),
        "page_region": _get_page_region(element_bbox, page_height, page_width),
        "normalized_x_center": round((x0 + x1) / (2 * page_width), 3),
        "normalized_y_center": round((y0 + y1) / (2 * page_height), 3),
        "page_width": round(page_width, 2),
        "page_height": round(page_height, 2)
    }

    surrounding = _extract_surrounding_text(page, element_bbox)
    spatial_metadata.update(surrounding)

    text_anchors = _find_text_anchors(page_text, element_type)
    if text_anchors:
        spatial_metadata["text_anchors"] = text_anchors

    section_info = _extract_section_context(page, page_text)
    if section_info["sections"]:
        element_y = (y0 + y1) / 2
        nearest_section = None
        min_distance = float('inf')

        for section in section_info["sections"]:
            section_y = (section["bbox"][1] + section["bbox"][3]) / 2
            distance = abs(element_y - section_y)
            if distance < min_distance and section_y < element_y:
                min_distance = distance
                nearest_section = section["text"]

        if nearest_section:
            spatial_metadata["section_context"] = nearest_section

    return spatial_metadata


def _detect_query_intent(query: str) -> Dict[str, bool]:
    q = str(query or "")
    visual_patterns = [
        r"\bimage\b", r"\bimages\b", r"\bpicture\b", r"\bpictures\b", r"\bphoto\b", r"\bphotos\b",
        r"\bfigure\b", r"\bfigures\b", r"\bdiagram\b", r"\bdiagrams\b", r"\billustration\b", r"\billustrations\b",
        r"\bscreenshot\b", r"\bshow\b", r"\bdisplay\b", r"\bdepict\b", r"\bdepicts\b", r"\bshown\b",
        r"which\s+(?:picture|image|figure|diagram)",
        r"describe\s+the\s+(?:picture|image|figure|diagram)",
        r"what\s+does\s+the\s+(?:picture|image|figure|diagram)\s+show",
    ]
    table_patterns = [
        r"\btable\b", r"\btables\b", r"\brow\b", r"\brows\b", r"\bcolumn\b", r"\bcolumns\b",
        r"\bspreadsheet\b", r"\btabular\b",
    ]

    is_visual = any(re.search(pattern, q, re.IGNORECASE) for pattern in visual_patterns)
    is_table = any(re.search(pattern, q, re.IGNORECASE) for pattern in table_patterns)
    return {
        "is_visual": is_visual,
        "is_table": is_table,
        "is_text": not is_visual and not is_table,
    }


class MultimodalKnowledgeBase:
    def __init__(self, params: MultimodalKnowledgeBaseParams, db_client: chromadb.Client):
        self.params = params
        self.name = params.name
        self.VRBZ = int(params.vrbz)

        vs_params = MultimodalVectorStoreParams(
            collection_name=f"kb_{params.name}_collection",
            chunk_size=params.chunk_size,
            chunk_overlap=params.chunk_overlap,
            persist_directory=params.persist_dir or "./chroma_db",
            rebuild_vstore=params.rebuild_vstore,
            embedding=params.embedding,
            use_bm25=params.use_bm25,
            use_rrf=params.use_rrf,
            rrf_k=params.rrf_k,
            use_reranker=params.use_reranker,
            reranker_model=params.reranker_model,
            allow_online=params.allow_online,
            http_timeout=params.http_timeout,
            vs_VRBZ=params.vrbz
        )

        self.store = MultimodalVectorStore(
            params=vs_params,
            client=db_client
        )
        self.default_collection: str = vs_params.collection_name

        import logging
        self._logger = logging.getLogger(__name__)
        if not self._logger.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter("[%(levelname)s] %(message)s")
            handler.setFormatter(formatter)
            self._logger.addHandler(handler)
            self._logger.setLevel(logging.INFO)

        self.client = db_client
        inv_dir = params.persist_dir or "./chroma_db"
        pathlib.Path(inv_dir).mkdir(parents=True, exist_ok=True)
        self.inventory_path = os.path.join(inv_dir, f"{self.name}_inventory.sqlite")
        self._init_inventory()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.inventory_path, isolation_level=None)

    def _init_inventory(self) -> None:
        schema_error = None
        with self._connect() as cx:
            try:
                cx.execute("PRAGMA journal_mode=WAL;")
            except sqlite3.OperationalError as e:
                schema_error = e
                self._logger.warning(
                    "SQLite WAL mode unavailable for inventory %s; falling back to DELETE mode. Error: %s",
                    self.inventory_path,
                    e,
                )
                try:
                    cx.execute("PRAGMA journal_mode=DELETE;")
                except sqlite3.OperationalError as fallback_error:
                    self._logger.warning(
                        "SQLite DELETE journal mode also failed for inventory %s; continuing with default mode. Error: %s",
                        self.inventory_path,
                        fallback_error,
                    )
            cx.executescript(MULTIMODAL_INVENTORY_SCHEMA)

        if schema_error is not None:
            self._logger.info(
                "Inventory schema initialized without WAL for %s",
                self.inventory_path,
            )

    def _record_document(self, source_path: str, doc_type: str, modality: str, results: List[Dict[str, Any]]) -> Dict[str, Any]:
        n_chunks = len(results)
        n_tokens = sum(_count_tokens(str(r.get("document", ""))) for r in results)
        v_ids = [r.get("id", f"{source_path}_chunk_{i}") for i, r in enumerate(results)]
        added_at = _now_iso()

        with self._connect() as cx:
            cx.execute(
                "INSERT INTO documents (source_path, doc_type, modality, v_ids_json, n_chunks, n_tokens, added_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (source_path, doc_type, modality, json.dumps(v_ids), n_chunks, n_tokens, added_at),
            )
            for i, result in enumerate(results):
                meta = result.get("metadata", {})
                v_id = result.get("id", f"{source_path}_chunk_{i}")
                line_start = meta.get("line_start", 0)
                line_end = meta.get("line_end", 0)
                chunk_id = meta.get("chunk_id", i)
                content = result.get("document", "")

                cx.execute(
                    "INSERT INTO chunks (v_id, source_path, modality, line_start, line_end, position, n_tokens, metadata_json, added_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (v_id, source_path, modality, line_start, line_end, chunk_id, _count_tokens(str(content)), json.dumps(meta), added_at),
                )

        return {"source_path": source_path, "doc_type": doc_type, "modality": modality, "n_chunks": n_chunks, "n_tokens": n_tokens, "v_ids": v_ids}

    def inventory_stats(self) -> Dict[str, Any]:
        with self._connect() as cx:
            cur = cx.execute("SELECT COUNT(*), COALESCE(SUM(n_chunks),0), COALESCE(SUM(n_tokens),0) FROM documents")
            n_docs, n_chunks, n_tokens = cur.fetchone()
            cur = cx.execute("SELECT modality, COUNT(*), COALESCE(SUM(n_chunks),0) FROM documents GROUP BY modality")
            modality_stats = {row[0]: {"n_docs": row[1], "n_chunks": row[2]} for row in cur.fetchall()}

        return {
            "n_sources": int(n_docs),
            "n_chunks": int(n_chunks),
            "n_tokens": int(n_tokens),
            "modalities": modality_stats
        }

    def list_sources(self) -> List[Dict[str, Any]]:
        with self._connect() as cx:
            cur = cx.execute("SELECT source_path, doc_type, modality, n_chunks, n_tokens, added_at FROM documents ORDER BY added_at DESC")
            return [
                {"source_path": r[0], "doc_type": r[1], "modality": r[2], "n_chunks": r[3], "n_tokens": r[4], "added_at": r[5]}
                for r in cur.fetchall()
            ]

    def _resolve_effective_persist_extracted_images(self, persist_extracted_images: Optional[bool] = None) -> bool:
        if persist_extracted_images is None:
            return bool(getattr(self.params, "persist_extracted_pdf_images", False))
        return bool(persist_extracted_images)

    def _resolve_effective_extracted_images_base_dir(self, extracted_image_dir: Optional[str] = None) -> pathlib.Path:
        if extracted_image_dir:
            return pathlib.Path(os.path.expanduser(extracted_image_dir))

        configured_dir = getattr(self.params, "extracted_pdf_images_dir", None)
        if configured_dir:
            return pathlib.Path(os.path.expanduser(configured_dir))

        persist_dir = self.params.persist_dir or "./chroma_db"
        return pathlib.Path(persist_dir) / f"{self.name}_assets" / "images"

    def _get_extracted_pdf_images_dir(self, document_id: str, extracted_image_dir: Optional[str] = None) -> pathlib.Path:
        base_dir = self._resolve_effective_extracted_images_base_dir(extracted_image_dir=extracted_image_dir)
        doc_dir = base_dir / document_id
        doc_dir.mkdir(parents=True, exist_ok=True)
        return doc_dir

    def _persist_pdf_image(
        self,
        image_bytes: bytes,
        document_id: str,
        page_number: int,
        image_index: int,
        image_ext: str,
        persist_extracted_images: Optional[bool] = None,
        extracted_image_dir: Optional[str] = None,
    ) -> Dict[str, Any]:
        image_sha256 = hashlib.sha256(image_bytes).hexdigest()
        effective_persist = self._resolve_effective_persist_extracted_images(
            persist_extracted_images=persist_extracted_images
        )

        result: Dict[str, Any] = {
            "image_sha256": image_sha256,
            "persisted_image": False,
        }

        if not effective_persist:
            return result

        ext = (image_ext or "png").lstrip(".").lower() or "png"
        images_dir = self._get_extracted_pdf_images_dir(
            document_id,
            extracted_image_dir=extracted_image_dir,
        )
        file_name = f"page_{page_number:03d}_img_{image_index:03d}.{ext}"
        file_path = images_dir / file_name
        file_path.write_bytes(image_bytes)

        result.update({
            "persisted_image": True,
            "stored_file_path": str(file_path.resolve()),
            "stored_file_name": file_name,
            "asset_rel_path": str(file_path.relative_to(images_dir.parent.parent)) if images_dir.parent.parent in file_path.parents else str(file_path),
            "stored_file_size": len(image_bytes),
        })
        return result

    def add_pdf_document(
        self,
        pdf_content: Union[str, pathlib.Path, bytes],
        metadata: Optional[Dict[str, Any]] = None,
        extract_images: bool = True,
        extract_tables: bool = True,
        persist_extracted_images: Optional[bool] = None,
        extracted_image_dir: Optional[str] = None,
        collection: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not PYMUPDF_AVAILABLE:
            raise ImportError(
                "PyMuPDF (fitz) is required for PDF parsing. Install with: pip install PyMuPDF"
            )

        document_id = str(uuid.uuid4())
        source_name = "bytes"
        effective_persist_extracted_images = self._resolve_effective_persist_extracted_images(
            persist_extracted_images=persist_extracted_images
        )
        effective_extracted_images_base_dir = self._resolve_effective_extracted_images_base_dir(
            extracted_image_dir=extracted_image_dir
        )

        try:
            if isinstance(pdf_content, (str, pathlib.Path)):
                pdf_path = pathlib.Path(pdf_content)
                if not pdf_path.exists():
                    raise ValueError(f"PDF file not found: {pdf_content}")
                source_name = str(pdf_path)
                doc = fitz.open(pdf_path)
            elif isinstance(pdf_content, bytes):
                doc = fitz.open(stream=pdf_content, filetype="pdf")
            else:
                raise ValueError(
                    f"pdf_content must be str, pathlib.Path, or bytes, got {type(pdf_content)}"
                )
        except Exception as e:
            raise ValueError(f"Failed to open PDF: {e}")

        base_metadata = metadata or {}
        base_metadata["document_id"] = document_id
        base_metadata["source"] = source_name
        base_metadata["doc_type"] = "pdf"
        base_metadata["extracted_at"] = _now_iso()

        extraction_errors = []
        n_pages = len(doc)
        all_text_results: List[Dict[str, Any]] = []
        all_image_results: List[Dict[str, Any]] = []
        all_table_results: List[Dict[str, Any]] = []

        self._logger.info(f"Parsing PDF with {n_pages} pages: {source_name}")
        self._logger.info(
            "PDF ingestion start kb=%s source=%s extract_images=%s extract_tables=%s persist_extracted_images=%s extracted_image_base_dir=%s collection=%s",
            self.name,
            source_name,
            extract_images,
            extract_tables,
            effective_persist_extracted_images,
            str(effective_extracted_images_base_dir),
            collection or self.default_collection,
        )

        for page_num in range(n_pages):
            page = doc[page_num]
            page_metadata = dict(base_metadata)
            page_metadata["page_number"] = page_num + 1
            page_metadata["page"] = page_num + 1

            try:
                text = page.get_text("text")
                if text.strip():
                    self._logger.info(
                        "PDF page %s text extraction produced %s chars; attempting add_document(text)",
                        page_num + 1,
                        len(text),
                    )
                    text_metadata = dict(page_metadata)
                    text_metadata["modality"] = "text"
                    text_metadata["element_type"] = "page_text"
                    text_results = self.add_document(
                        content=text,
                        metadata=text_metadata,
                        modality="text",
                        collection=collection
                    )
                    all_text_results.extend(text_results)
                    self._logger.info(
                        "PDF page %s text add_document succeeded; chunks_added=%s cumulative_text_chunks=%s",
                        page_num + 1,
                        len(text_results),
                        len(all_text_results),
                    )
            except Exception as e:
                error_msg = f"Failed to extract text from page {page_num + 1}: {e}"
                self._logger.warning(error_msg)
                self._logger.warning(traceback.format_exc())
                extraction_errors.append(error_msg)

            if extract_images:
                try:
                    image_list = page.get_images(full=True)
                    self._logger.info(
                        "PDF page %s image candidates=%s",
                        page_num + 1,
                        len(image_list),
                    )
                    for img_index, img in enumerate(image_list):
                        try:
                            xref = img[0]
                            base_image = doc.extract_image(xref)
                            image_bytes = base_image["image"]
                            image_ext = base_image["ext"]
                            caption_meta: Dict[str, Any] = {}
                            if img_rects := page.get_image_rects(xref):
                                img_bbox = img_rects[0]
                                bbox_tuple = (img_bbox.x0, img_bbox.y0, img_bbox.x1, img_bbox.y1)
                                spatial_meta = _get_element_spatial_metadata(
                                    page,
                                    bbox_tuple,
                                    "image",
                                    page_num + 1
                                )
                                caption_meta = _extract_figure_caption(page, bbox_tuple)
                            else:
                                spatial_meta = {"bbox_note": "bbox not available"}

                            persistence_meta = self._persist_pdf_image(
                                image_bytes=image_bytes,
                                document_id=document_id,
                                page_number=page_num + 1,
                                image_index=img_index,
                                image_ext=image_ext,
                                persist_extracted_images=persist_extracted_images,
                                extracted_image_dir=extracted_image_dir,
                            )

                            img_metadata = dict(page_metadata)
                            img_metadata["modality"] = "image"
                            img_metadata["element_type"] = "extracted_image"
                            img_metadata["image_index"] = img_index
                            img_metadata["image_format"] = image_ext
                            img_metadata.update(spatial_meta)
                            img_metadata.update(persistence_meta)
                            if caption_meta.get("caption"):
                                img_metadata.update(caption_meta)
                            elif caption_meta.get("caption_extraction_error"):
                                img_metadata["caption_extraction_error"] = caption_meta["caption_extraction_error"]

                            self._logger.info(
                                "PDF page %s image %s extracted bytes=%s format=%s caption_found=%s caption_label=%s caption_chars=%s; attempting add_document(image)",
                                page_num + 1,
                                img_index,
                                len(image_bytes),
                                image_ext,
                                bool(img_metadata.get("caption")),
                                img_metadata.get("caption_label", ""),
                                len(img_metadata.get("caption", "") or ""),
                            )
                            image_results = self.add_document(
                                content=image_bytes,
                                metadata=img_metadata,
                                modality="image",
                                collection=collection
                            )
                            all_image_results.extend(image_results)
                            self._logger.info(
                                "PDF page %s image %s add_document succeeded; items_added=%s cumulative_images=%s",
                                page_num + 1,
                                img_index,
                                len(image_results),
                                len(all_image_results),
                            )
                        except Exception as e:
                            error_msg = f"Failed to extract image {img_index} from page {page_num + 1}: {e}"
                            self._logger.warning(error_msg)
                            self._logger.warning(traceback.format_exc())
                            extraction_errors.append(error_msg)
                except Exception as e:
                    error_msg = f"Failed to get images from page {page_num + 1}: {e}"
                    self._logger.warning(error_msg)
                    self._logger.warning(traceback.format_exc())
                    extraction_errors.append(error_msg)

            if extract_tables:
                try:
                    if hasattr(page, "find_tables"):
                        found_tables = page.find_tables()
                        table_iter = found_tables.tables if hasattr(found_tables, "tables") else found_tables
                        table_iter = list(table_iter)
                        self._logger.info(
                            "PDF page %s table candidates=%s",
                            page_num + 1,
                            len(table_iter),
                        )

                        for table_index, tbl in enumerate(table_iter):
                            try:
                                rows = tbl.extract()
                                if not rows:
                                    continue

                                table_text = _rows_to_csv(rows)
                                if not table_text.strip():
                                    continue

                                table_bbox = None
                                if hasattr(tbl, "bbox") and tbl.bbox:
                                    table_bbox = tbl.bbox
                                elif hasattr(tbl, "rect") and tbl.rect:
                                    r = tbl.rect
                                    table_bbox = (r.x0, r.y0, r.x1, r.y1)

                                if table_bbox is not None:
                                    spatial_meta = _get_element_spatial_metadata(
                                        page,
                                        tuple(table_bbox),
                                        "table",
                                        page_num + 1,
                                    )
                                else:
                                    spatial_meta = {"bbox_note": "table bbox not available"}

                                table_metadata = dict(page_metadata)
                                table_metadata["modality"] = "table"
                                table_metadata["element_type"] = "extracted_table"
                                table_metadata["table_index"] = table_index
                                table_metadata["table_rows"] = len(rows)
                                table_metadata["table_cols"] = max((len(r) for r in rows), default=0)
                                table_metadata.update(spatial_meta)

                                self._logger.info(
                                    "PDF page %s table %s rows=%s cols=%s csv_chars=%s; attempting add_document(table)",
                                    page_num + 1,
                                    table_index,
                                    len(rows),
                                    max((len(r) for r in rows), default=0),
                                    len(table_text),
                                )
                                table_results = self.add_document(
                                    content=table_text,
                                    metadata=table_metadata,
                                    modality="table",
                                    collection=collection,
                                )
                                all_table_results.extend(table_results)
                                self._logger.info(
                                    "PDF page %s table %s add_document succeeded; items_added=%s cumulative_tables=%s",
                                    page_num + 1,
                                    table_index,
                                    len(table_results),
                                    len(all_table_results),
                                )
                            except Exception as e:
                                error_msg = (
                                    f"Failed to ingest table {table_index} "
                                    f"from page {page_num + 1}: {e}"
                                )
                                self._logger.warning(error_msg)
                                self._logger.warning(traceback.format_exc())
                                extraction_errors.append(error_msg)
                    else:
                        self._logger.warning(
                            "PyMuPDF page.find_tables() not available in this version; skipping table extraction"
                        )
                except Exception as e:
                    error_msg = f"Failed to extract tables from page {page_num + 1}: {e}"
                    self._logger.warning(error_msg)
                    self._logger.warning(traceback.format_exc())
                    extraction_errors.append(error_msg)

        doc.close()

        if all_text_results:
            self._record_document(source_name, "pdf", "text", all_text_results)
        if all_image_results:
            self._record_document(source_name, "pdf", "image", all_image_results)
        if all_table_results:
            self._record_document(source_name, "pdf", "table", all_table_results)

        summary = {
            "document_id": document_id,
            "source": source_name,
            "n_pages": n_pages,
            "n_text_chunks": len(all_text_results),
            "n_images": len(all_image_results),
            "n_tables": len(all_table_results),
            "persisted_images": effective_persist_extracted_images,
            "extracted_image_base_dir": str(effective_extracted_images_base_dir.resolve()) if effective_persist_extracted_images else None,
            "status": "success" if not extraction_errors else "partial_success",
        }

        if extraction_errors:
            summary["errors"] = extraction_errors
            summary["n_errors"] = len(extraction_errors)

        self._logger.info(
            f"PDF ingestion complete: {len(all_text_results)} text chunks, "
            f"{len(all_image_results)} images, {len(all_table_results)} tables from {n_pages} pages"
        )

        return summary

    async def add_text_docs(
        self,
        texts: List[str],
        doc_source: str = "user",
        pbar: Optional[str] = None,
    ) -> None:
        results = await self.store.add_text_docs(texts, doc_source=doc_source)
        if results:
            self._record_document(
                source_path=f"text://{doc_source}",
                doc_type="text",
                modality="text",
                results=results,
            )

    async def query(
        self,
        query: str,
        n_results: int = 5,
        filter: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> List[Dict[str, Any]]:
        intent = _detect_query_intent(query)

        channel_weights: Optional[Dict[str, float]] = None
        if filter is not None:
            filter = dict(filter)
            if "channel_weights" in filter:
                channel_weights = filter.pop("channel_weights")
                if not filter:
                    filter = None

        if intent["is_visual"] and channel_weights is None:
            channel_weights = {"vision": 2.5, "dense": 0.6, "bm25": 0.4, "table": 0.3}
        elif intent["is_table"] and channel_weights is None:
            channel_weights = {"table": 2.0, "dense": 0.8, "bm25": 0.6, "vision": 0.3}

        store_kwargs: Dict[str, Any] = {
            "query": query,
            "k": n_results,
            "filter": filter,
            "query_intent": intent,
            **kwargs,
        }
        if channel_weights is not None:
            store_kwargs["channel_weights"] = channel_weights

        return await self.store.query_hybrid(**store_kwargs)

    async def close(self) -> None:
        await self.store.close()

    async def asearch(
        self,
        query: str,
        k: int = 5,
        with_score: bool = False,
        context_window: int = 1,
        **kwargs: Any,
    ) -> List[Dict[str, Any]]:
        return await self.query(query, n_results=k, **kwargs)

    def _run_async_in_thread(self, coro):
        def runner():
            self._logger.info("_run_async_in_thread runner starting")
            try:
                result = asyncio.run(coro)
                self._logger.info("_run_async_in_thread runner completed")
                return result
            except Exception:
                self._logger.error("_run_async_in_thread runner failed\n%s", traceback.format_exc())
                raise

        self._logger.info("_run_async_in_thread submit start")
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(runner)
            try:
                result = future.result()
                self._logger.info("_run_async_in_thread future.result completed")
                return result
            except Exception:
                self._logger.error("_run_async_in_thread future.result failed\n%s", traceback.format_exc())
                raise

    def add_document(
        self,
        content: SupportedModalities,
        metadata: Optional[Dict[str, Any]] = None,
        modality: str = "text",
        collection: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        _ = collection or self.default_collection
        metadata_keys = sorted(list(metadata.keys())) if metadata else []
        self._logger.info(
            "add_document start kb=%s modality=%s content_type=%s metadata_keys=%s collection=%s",
            self.name,
            modality,
            type(content).__name__,
            metadata_keys,
            collection or self.default_collection,
        )

        if modality == "text":
            try:
                if isinstance(content, pathlib.Path):
                    self._logger.info("add_document text branch using pathlib.Path source=%s", str(content))
                    coro = self.store.add_documents([str(content)])
                    result = self._run_async_in_thread(coro)
                    self._logger.info("add_document text/path branch success results=%s", len(result))
                    return result
                else:
                    doc_source = metadata.get("source", "user") if metadata else "user"
                    self._logger.info(
                        "add_document text branch using add_text_docs doc_source=%s text_chars=%s",
                        doc_source,
                        len(str(content)),
                    )
                    coro = self.store.add_text_docs(
                        [str(content)],
                        doc_source=doc_source,
                        metadatas=[metadata] if metadata is not None else None,
                    )
                    result = self._run_async_in_thread(coro)
                    self._logger.info("add_document text branch success results=%s", len(result))
                    return result
            except Exception:
                self._logger.error(
                    "add_document text branch failed modality=%s content_type=%s metadata_keys=%s\n%s",
                    modality,
                    type(content).__name__,
                    metadata_keys,
                    traceback.format_exc(),
                )
                raise

        try:
            if isinstance(content, pathlib.Path):
                data = content.read_bytes()
                if metadata is None:
                    metadata = {}
                if "source_file" not in metadata:
                    metadata["source_file"] = content.name
                self._logger.info(
                    "add_document binary branch loaded pathlib.Path bytes=%s source_file=%s",
                    len(data),
                    metadata.get("source_file"),
                )
            elif isinstance(content, bytes):
                data = content
                self._logger.info("add_document binary branch received raw bytes=%s", len(data))
            elif isinstance(content, str):
                data = content.encode("utf-8")
                self._logger.info("add_document binary branch encoded str to bytes=%s", len(data))
            else:
                raise ValueError(
                    f"Unsupported content type for modality '{modality}': {type(content)}"
                )

            payload = {modality: data}
            if metadata:
                payload["metadata"] = metadata
            self._logger.info(
                "Adding %s document via store.add_documents payload_keys=%s metadata_keys=%s bytes=%s",
                modality,
                sorted(payload.keys()),
                sorted(list(metadata.keys())) if metadata else [],
                len(data),
            )
            coro = self.store.add_documents([], binary_payload=payload)
            result = self._run_async_in_thread(coro)
            self._logger.info("add_document binary branch success modality=%s results=%s", modality, len(result))
            return result
        except Exception:
            self._logger.error(
                "add_document binary branch failed modality=%s content_type=%s metadata_keys=%s\n%s",
                modality,
                type(content).__name__,
                sorted(list(metadata.keys())) if metadata else [],
                traceback.format_exc(),
            )
            raise

    def add_documents(
        self,
        contents: Iterable[SupportedModalities],
        metadata: Optional[Iterable[Dict[str, Any]]] = None,
        modality: str = "text",
        collection: Optional[str] = None,
    ) -> List[List[Dict[str, Any]]]:
        col = collection or self.default_collection
        meta_iter = metadata or (None for _ in contents)
        ids: List[List[Dict[str, Any]]] = []
        for content, meta in zip(contents, meta_iter):
            ids.append(self.add_document(content, meta, modality, col))
        return ids

    def add_texts(
        self,
        texts: Iterable[str],
        metadatas: Optional[Iterable[Dict[str, Any]]] = None,
        collection: Optional[str] = None,
    ) -> List[List[Dict[str, Any]]]:
        col = collection or self.default_collection
        meta_iter = metadatas or (None for _ in texts)
        ids: List[List[Dict[str, Any]]] = []
        for txt, meta in zip(texts, meta_iter):
            ids.append(self.add_document(txt, meta, modality="text", collection=col))
        return ids

    def delete_collection(self, collection_name: str) -> None:
        try:
            self.client.delete_collection(name=collection_name)
        except Exception as e:
            self._logger.warning(f"Failed to delete collection {collection_name}: {e}")

    def list_collections(self) -> List[str]:
        collections = self.client.list_collections()
        return [c.name for c in collections]

    def get_collection_info(self, collection_name: str) -> Dict[str, Any]:
        try:
            coll = self.client.get_collection(name=collection_name)
            return {"name": coll.name, "count": coll.count()}
        except Exception as e:
            return {"error": str(e)}

    def add_text_docs_sync(
        self,
        texts: List[str],
        doc_source: str = "user",
        pbar: Optional[str] = None,
    ) -> None:
        try:
            asyncio.get_running_loop()
            raise RuntimeError(
                "add_text_docs_sync cannot be used inside an async context – use the async 'await kb.add_text_docs(...)' instead."
            )
        except RuntimeError:
            asyncio.run(self.add_text_docs(texts, doc_source=doc_source, pbar=pbar))

    def list_modalities(self) -> Dict[str, int]:
        stats = self.inventory_stats()
        return {k: v.get("n_chunks", 0) for k, v in stats.get("modalities", {}).items()}

    def get_stats(self) -> Dict[str, Any]:
        inventory_stats = self.inventory_stats()
        vstore_stats = self.store.get_stats()
        return {
            **inventory_stats,
            **vstore_stats,
            "name": self.name
        }
