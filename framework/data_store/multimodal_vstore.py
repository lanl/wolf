import asyncio
import glob, json, csv, pdfplumber, nbformat
import hashlib
import mimetypes
import os
import pickle
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from alive_progress import alive_bar

import chromadb
from chromadb.config import Settings
from framework.data_store.data_models import MultimodalEmbeddingParams, MultimodalVectorStoreParams


# ============================================================
# Optional dependencies (auto-detected)
# ============================================================
def _try_import(name: str):
    try:
        return __import__(name)
    except Exception:
        return None


np = _try_import("numpy")
st = _try_import("sentence_transformers")
rank_bm25 = _try_import("rank_bm25")
requests = _try_import("requests")
bs4 = _try_import("bs4")
open_clip = _try_import("open_clip")
PIL = _try_import("PIL")
faster_whisper = _try_import("faster_whisper")
transformers = _try_import("transformers")


# ============================================================
# Utilities
# ============================================================
def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def guess_mime(path: Path) -> str:
    mime, _ = mimetypes.guess_type(str(path))
    return mime or "application/octet-stream"


def simple_tokenize(text: str) -> List[str]:
    import re

    toks = re.findall(r"[A-Za-z0-9_]+", text.lower())
    return [t for t in toks if len(t) > 1]


def rrf_fuse(
    ranked_lists: List[List[str]],
    k: int = 20,
    rrf_k: int = 60,
    weights: Optional[List[float]] = None,
) -> List[str]:
    if weights is None:
        weights = [1.0] * len(ranked_lists)

    scores: Dict[str, float] = {}
    for w, lst in zip(weights, ranked_lists):
        for rank, doc_id in enumerate(lst, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + (w / (rrf_k + rank))

    return [
        doc_id
        for doc_id, _ in sorted(scores.items(), key=lambda x: x[1], reverse=True)[:k]
    ]


# ============================================================
# BM25 Sidecar (persistent)
# ============================================================
class PersistentBM25:
    def __init__(self, persist_dir: str, index_name: str = "bm25"):
        self.persist_dir = Path(persist_dir)
        self.persist_dir.mkdir(parents=True, exist_ok=True)
        self.index_path = self.persist_dir / f"{index_name}.pkl"

        self.doc_ids: List[str] = []
        self.corpus_tokens: List[List[str]] = []
        self.doc_text: Dict[str, str] = {}
        self.doc_meta: Dict[str, Dict[str, Any]] = {}

        self._bm25 = None
        self._dirty = False
        self._load()

    def _load(self):
        if self.index_path.exists():
            try:
                with open(self.index_path, "rb") as f:
                    data = pickle.load(f)
                self.doc_ids = data.get("doc_ids", [])
                self.corpus_tokens = data.get("corpus_tokens", [])
                self.doc_text = data.get("doc_text", {})
                self.doc_meta = data.get("doc_meta", {})
                self._rebuild()
            except Exception:
                self.doc_ids = []
                self.corpus_tokens = []
                self.doc_text = {}
                self.doc_meta = {}
                self._bm25 = None

    def _save(self):
        if not self._dirty:
            return

        data = {
            "doc_ids": self.doc_ids,
            "corpus_tokens": self.corpus_tokens,
            "doc_text": self.doc_text,
            "doc_meta": self.doc_meta,
        }
        tmp = str(self.index_path) + ".tmp"
        with open(tmp, "wb") as f:
            pickle.dump(data, f)
        os.replace(tmp, self.index_path)
        self._dirty = False

    def _rebuild(self):
        if rank_bm25 is not None and hasattr(rank_bm25, "BM25Okapi"):
            self._bm25 = rank_bm25.BM25Okapi(self.corpus_tokens)
        else:
            self._bm25 = _BasicBM25(self.corpus_tokens)

    def add_many(self, items: List[Tuple[str, str, Optional[Dict[str, Any]]]]):
        for doc_id, text, meta in items:
            if doc_id in self.doc_text:
                self.delete([doc_id])

            self.doc_ids.append(doc_id)
            self.corpus_tokens.append(simple_tokenize(text))
            self.doc_text[doc_id] = text
            if meta is not None:
                self.doc_meta[doc_id] = meta

        self._dirty = True
        self._rebuild()

    def delete(self, doc_ids: List[str]):
        if not doc_ids:
            return

        to_remove = set(doc_ids)
        new_doc_ids = []
        new_tokens = []

        for did, toks in zip(self.doc_ids, self.corpus_tokens):
            if did not in to_remove:
                new_doc_ids.append(did)
                new_tokens.append(toks)
            else:
                self.doc_text.pop(did, None)
                self.doc_meta.pop(did, None)

        self.doc_ids = new_doc_ids
        self.corpus_tokens = new_tokens
        self._dirty = True
        self._rebuild()

    def search(self, query: str, k: int = 20) -> List[str]:
        if not self.doc_ids:
            return []

        q_tokens = simple_tokenize(query)
        scores = self._bm25.get_scores(q_tokens)
        idxs = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:k]
        return [self.doc_ids[i] for i in idxs]

    def close(self):
        self._save()


class _BasicBM25:
    def __init__(self, corpus_tokens: List[List[str]], k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.corpus = corpus_tokens
        self.N = len(corpus_tokens)
        self.avgdl = sum(len(d) for d in corpus_tokens) / max(1, self.N)
        self.df = {}
        self.idf = {}

        for doc in corpus_tokens:
            for t in set(doc):
                self.df[t] = self.df.get(t, 0) + 1

        import math
        for t, df in self.df.items():
            self.idf[t] = math.log(1 + (self.N - df + 0.5) / (df + 0.5))

    def get_scores(self, query_tokens: List[str]) -> List[float]:
        scores = [0.0] * self.N
        for i, doc in enumerate(self.corpus):
            dl = len(doc)
            if dl == 0:
                continue

            tf = {}
            for t in doc:
                tf[t] = tf.get(t, 0) + 1

            for q in query_tokens:
                if q not in tf:
                    continue
                idf = self.idf.get(q, 0.0)
                freq = tf[q]
                denom = freq + self.k1 * (1 - self.b + self.b * (dl / self.avgdl))
                scores[i] += idf * ((freq * (self.k1 + 1)) / denom)
        return scores


# ============================================================
# Embedders
# ============================================================
class MultimodalEmbedder:
    def __init__(self, embedding_params: MultimodalEmbeddingParams):
        self.params = embedding_params
        self.device = embedding_params.device
        self.text_model_name = embedding_params.text_model

        self._text_encoder = None
        if st is not None:
            try:
                self._text_encoder = st.SentenceTransformer(embedding_params.text_model, device=self.device)
            except Exception:
                self._text_encoder = None

        self._clip = None
        self._clip_tokenizer = None
        self._clip_preprocess = None
        if open_clip is not None and PIL is not None:
            try:
                model, _, preprocess = open_clip.create_model_and_transforms(
                    embedding_params.clip_model, pretrained=embedding_params.clip_pretrained
                )
                tokenizer = open_clip.get_tokenizer(embedding_params.clip_model)
                self._clip = model
                self._clip_tokenizer = tokenizer
                self._clip_preprocess = preprocess

                if self.device:
                    self._clip = self._clip.to(self.device)
                self._clip.eval()
            except Exception:
                self._clip = None

        self._whisper = None
        if faster_whisper is not None:
            try:
                self._whisper = faster_whisper.WhisperModel(
                    "base",
                    device=(self.device or "cpu"),
                    compute_type="int8" if (self.device is None or self.device == "cpu") else "float16",
                )
            except Exception:
                self._whisper = None

        #DistilBERT encoder (lazy loaded)
        self._table_encoder = None
        self._table_tokenizer = None

    def _ensure_numpy(self):
        if np is None:
            raise RuntimeError("numpy is required for embedding fallbacks. Please install numpy.")

    def embed_text(self, texts: List[str]) -> List[List[float]]:
        self._ensure_numpy()

        if self._text_encoder is not None:
            vecs = self._text_encoder.encode(
                texts, normalize_embeddings=True, show_progress_bar=False
            )
            return vecs.tolist()

        return self._hash_embed(texts, dim=384)

    def embed_text_for_vision_space(self, texts: List[str]) -> List[List[float]]:
        if self._clip is None:
            return self._hash_embed(texts, dim=512)

        import torch

        tokens = self._clip_tokenizer(texts)
        with torch.no_grad():
            if self.device:
                tokens = tokens.to(self.device)
            feats = self._clip.encode_text(tokens)
            feats = feats / feats.norm(dim=-1, keepdim=True)
        return feats.cpu().numpy().tolist()

    def embed_images(self, image_paths: List[str]) -> List[List[float]]:
        self._ensure_numpy()

        if self._clip is None or PIL is None:
            texts = [f"image:{Path(p).name}" for p in image_paths]
            return self._hash_embed(texts, dim=512)

        import torch
        from PIL import Image

        imgs = []
        for p in image_paths:
            try:
                im = Image.open(p).convert("RGB")
                imgs.append(self._clip_preprocess(im))
            except Exception:
                imgs.append(None)

        valid_idx = [i for i, x in enumerate(imgs) if x is not None]
        out = [None] * len(imgs)

        if valid_idx:
            batch = torch.stack([imgs[i] for i in valid_idx])
            if self.device:
                batch = batch.to(self.device)
            with torch.no_grad():
                feats = self._clip.encode_image(batch)
                feats = feats / feats.norm(dim=-1, keepdim=True)
            feats = feats.cpu().numpy().tolist()
            for i, v in zip(valid_idx, feats):
                out[i] = v

        dim = len(out[valid_idx[0]]) if valid_idx else 512
        for i in range(len(out)):
            if out[i] is None:
                out[i] = [0.0] * dim

        return out

    def transcribe_audio(self, audio_path: str) -> str:
        if self._whisper is None:
            return ""
        try:
            segments, _info = self._whisper.transcribe(audio_path, beam_size=5)
            parts = []
            for s in segments:
                if s.text:
                    parts.append(s.text.strip())
            return " ".join(parts).strip()
        except Exception:
            return ""

    def caption_image(self, image_path: str) -> str:
        return ""

    def _hash_embed(self, texts: List[str], dim: int = 384) -> List[List[float]]:
        self._ensure_numpy()
        vecs = []
        for t in texts:
            v = np.zeros(dim, dtype=np.float32)
            for tok in simple_tokenize(t):
                h = int(hashlib.md5(tok.encode("utf-8")).hexdigest(), 16)
                idx = h % dim
                sign = -1.0 if (h >> 1) & 1 else 1.0
                v[idx] += sign
            norm = float(np.linalg.norm(v))
            if norm > 0:
                v /= norm
            vecs.append(v.tolist())
        return vecs

    def _load_table_encoder(self):
        """Lazy‑load the DistilBERT  model and tokenizer.
        It ia a lighter‑weight version of Google's BERT."""
        if self._table_encoder is not None:
            return
        if transformers is None:
            raise RuntimeError("transformers library is required for table encoding but is not installed.")
        from transformers import AutoTokenizer, AutoModel

        self._table_tokenizer = AutoTokenizer.from_pretrained("distilbert-base-uncased")
        self._table_encoder = AutoModel.from_pretrained("distilbert-base-uncased")
        if self.device:
            self._table_encoder = self._table_encoder.to(self.device)
        self._table_encoder.eval()

    def embed_table(self, table_text: str) -> List[float]:
        """Return a single dense vector for *table_text*.
        The method tokenises the textual representation of the table (e.g. CSV
        or markdown) and mean‑pools the last‑hidden‑state to obtain a fixed‑size
        embedding."""
        self._load_table_encoder()
        import torch

        inputs = self._table_tokenizer(
            table_text, return_tensors="pt", truncation=True, max_length=512
        )
        if self.device:
            inputs = {k: v.to(self.device) for k, v in inputs.items()}
        with torch.no_grad():
            outputs = self._table_encoder(**inputs)
        # Mean‑pool over token dimension
        vec = outputs.last_hidden_state.mean(dim=1).squeeze().cpu().numpy().tolist()
        return vec


# ============================================================
# Data classes
# ============================================================
@dataclass
class IngestItem:
    id: str
    document: str
    embedding: List[float]
    metadata: Dict[str, Any]


# ============================================================
# Multi-modal Vector Store
# ============================================================
class MultimodalVectorStore:
    def __init__(self, params: MultimodalVectorStoreParams, client):
        self.params = params
        self.chunk_size = params.chunk_size
        self.chunk_overlap = params.chunk_overlap
        self.persist_directory = params.persist_directory
        self.collection_name = params.collection_name
        self.rebuild_vstore = params.rebuild_vstore

        self.use_bm25 = params.use_bm25
        self.use_rrf = params.use_rrf
        self.rrf_k = params.rrf_k

        self.use_reranker = params.use_reranker
        self.reranker_model = params.reranker_model
        self._reranker = None

        self.allow_online = params.allow_online
        self.http_timeout = params.http_timeout

        self.text_extensions = {
            "py", "js", "ts", "md", "html", "txt", "dat", "pdf", "csv", "json",
            "log", "info", "c", "cpp", "f", "f77", "f90", "f95", "ipynb",
        }
        self.image_extensions = {
            "png", "jpg", "jpeg", "webp", "bmp", "tif", "tiff"
        }
        self.audio_extensions = {
            "wav", "mp3", "m4a", "flac", "ogg", "aac"
        }
        self.video_extensions = {
            "mp4", "mov", "mkv", "webm", "avi"
        }
        self.table_extensions = {
            "csv", "tsv", "json", "xls", "xlsx", "md"
        }

        self.embedder = MultimodalEmbedder(params.embedding)

        self.client = client

        self.text_collection_name = f"{self.collection_name}_text"
        self.vision_collection_name = f"{self.collection_name}_vision"
        self.table_collection_name = f"{self.collection_name}_table"

        if self.rebuild_vstore:
            for cname in (self.text_collection_name, self.vision_collection_name, self.table_collection_name):
                try:
                    self.client.delete_collection(name=cname)
                except Exception:
                    pass

        self.text_collection = self.client.get_or_create_collection(
            name=self.text_collection_name,
            metadata={"hnsw_space": "cosine"},
        )
        self.vision_collection = self.client.get_or_create_collection(
            name=self.vision_collection_name,
            metadata={"hnsw_space": "cosine"},
        )
        self.table_collection = self.client.get_or_create_collection(
            name=self.table_collection_name,
            metadata={"hnsw_space": "cosine"},
        )

        self.bm25 = (
            PersistentBM25(
                self.persist_directory,
                index_name=f"{self.collection_name}_bm25",
            )
            if self.use_bm25
            else None
        )

        if self.use_reranker and st is not None:
            try:
                self._reranker = st.CrossEncoder(self.reranker_model)
            except Exception:
                self._reranker = None

    # ----------------------------
    # Internal helpers
    # ----------------------------
    def _get_collection_for_space(self, space: str):
        if space == "text":
            return self.text_collection
        if space == "vision":
            return self.vision_collection
        if space == "table":
            return self.table_collection
        raise ValueError(f"Unknown embedding space: {space}")

    def _classify_doc_id_space(self, doc_id: str, metadata: Optional[Dict[str, Any]] = None) -> str:
        if metadata:
            return metadata.get("embedding_space", "text")
        if "::image::" in doc_id or "::video_frame::" in doc_id:
            return "vision"
        if "::table::" in doc_id:
            return "table"
        return "text"

    async def _split_text_with_lines(self, text: str) -> List[Tuple[str, int, int]]:
        lines = text.splitlines()
        chunks = []
        current = []
        char_count = 0
        start_line = 0

        for i, line in enumerate(lines):
            if char_count + len(line) > self.chunk_size and current:
                chunks.append(("\n".join(current), start_line, i))
                overlap_lines = min(self.chunk_overlap // 50, len(current))
                current = current[-overlap_lines:] if overlap_lines > 0 else []
                start_line = i - len(current)
                char_count = sum(len(l) for l in current)

            current.append(line)
            char_count += len(line)

        if current:
            chunks.append(("\n".join(current), start_line, len(lines)))

        return chunks

    async def _read_text_file(self, file_path: Path) -> str:
        return await asyncio.to_thread(self._read_text_file_sync, file_path)

    def _read_text_file_sync(self, file_path: Path) -> str:
        try:
            ext = file_path.suffix.lower().strip(".")
            if ext == "pdf":
                content = ""
                with pdfplumber.open(file_path) as pdf:
                    for page in pdf.pages:
                        content += (page.extract_text() or "") + "\n"
                return content

            if ext == "json":
                with open(file_path, "r", encoding="utf-8") as f:
                    return json.dumps(json.load(f), indent=2)

            if ext == "csv":
                with open(file_path, "r", encoding="utf-8") as f:
                    reader = csv.reader(f)
                    return "\n".join([",".join(row) for row in reader])

            if ext == "ipynb":
                with open(file_path, "r", encoding="utf-8") as f:
                    nb = nbformat.read(f, as_version=4)
                return "\n".join(
                    [cell.get("source", "") for cell in nb.cells if cell.get("cell_type") == "code"]
                )

            with open(file_path, "r", encoding="utf-8") as f:
                return f.read()

        except Exception as e:
            return f"[Error reading {file_path}: {e}]"

    def _ffmpeg_extract_audio_wav(self, video_path: str, out_wav: str) -> None:
        subprocess.run(
            ["ffmpeg", "-y", "-i", video_path, "-vn", "-ac", "1", "-ar", "16000", out_wav],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def _ffmpeg_extract_keyframes(self, video_path: str, out_dir: str, fps: float = 0.2) -> List[str]:
        pattern = os.path.join(out_dir, "frame_%06d.jpg")
        subprocess.run(
            ["ffmpeg", "-y", "-i", video_path, "-vf", f"fps={fps}", pattern],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return sorted(glob.glob(os.path.join(out_dir, "frame_*.jpg")))

    def _add_items(self, items: List[IngestItem]) -> None:
        if not items:
            return

        buckets: Dict[str, List[IngestItem]] = {}
        for it in items:
            space = (it.metadata or {}).get("embedding_space", "text")
            buckets.setdefault(space, []).append(it)

        for space, group in buckets.items():
            coll = self._get_collection_for_space(space)
            coll.add(
                ids=[it.id for it in group],
                documents=[it.document for it in group],
                metadatas=[it.metadata for it in group],
                embeddings=[it.embedding for it in group],
            )

        if self.bm25 is not None:
            self.bm25.add_many([(it.id, it.document, it.metadata) for it in items])


    # ----------------------------
    # Public ingestion APIs
    # ----------------------------
    async def add_text_docs(self, texts: List[str], doc_source: str = "user") -> None:
        if not texts:
            return

        all_items: List[IngestItem] = []
        for idx, text in enumerate(texts):
            chunks = await self._split_text_with_lines(text)
            chunk_texts = [c[0] for c in chunks]
            vecs = await asyncio.to_thread(self.embedder.embed_text, chunk_texts)

            for chunk_idx, ((chunk, ls, le), vec) in enumerate(zip(chunks, vecs)):
                doc_id = f"{doc_source}::text::{idx}::{chunk_idx}"
                meta = {
                    "source": doc_source,
                    "uri": doc_source,
                    "modality": "text",
                    "embedding_space": "text",
                    "text_id": idx,
                    "chunk_id": chunk_idx,
                    "line_start": ls,
                    "line_end": le,
                    "mime": "text/plain",
                }
                all_items.append(
                    IngestItem(id=doc_id, document=chunk, embedding=vec, metadata=meta)
                )

        self._add_items(all_items)

    async def _ingest_text_file(self, p: Path) -> None:
        content = await self._read_text_file(p)
        chunks = await self._split_text_with_lines(content)
        chunk_texts = [c[0] for c in chunks]
        vecs = await asyncio.to_thread(self.embedder.embed_text, chunk_texts)

        items = []
        for i, ((chunk, ls, le), vec) in enumerate(zip(chunks, vecs)):
            doc_id = f"{str(p)}::text::{i}"
            meta = {
                "source": str(p),
                "uri": str(p),
                "modality": "text",
                "embedding_space": "text",
                "chunk_id": i,
                "line_start": ls,
                "line_end": le,
                "mime": guess_mime(p),
            }
            items.append(IngestItem(id=doc_id, document=chunk, embedding=vec, metadata=meta))

        self._add_items(items)

    async def _ingest_image_file(self, p: Path) -> None:
        caption = await asyncio.to_thread(self.embedder.caption_image, str(p))
        proxy = caption.strip() if caption.strip() else f"[image] {p.name}"

        img_vec = (await asyncio.to_thread(self.embedder.embed_images, [str(p)]))[0]
        doc_id = f"{str(p)}::image::0"
        meta = {
            "source": str(p),
            "uri": str(p),
            "modality": "image",
            "embedding_space": "vision",
            "chunk_id": 0,
            "mime": guess_mime(p),
            "caption": caption,
            "sha256": await asyncio.to_thread(sha256_file, p),
        }
        self._add_items([
            IngestItem(id=doc_id, document=proxy, embedding=img_vec, metadata=meta)
        ])

    async def _ingest_audio_file(self, p: Path) -> None:
        transcript = await asyncio.to_thread(self.embedder.transcribe_audio, str(p))
        proxy = transcript.strip() if transcript.strip() else f"[audio] {p.name}"

        vec = (await asyncio.to_thread(self.embedder.embed_text, [proxy]))[0]
        doc_id = f"{str(p)}::audio::0"
        meta = {
            "source": str(p),
            "uri": str(p),
            "modality": "audio",
            "embedding_space": "text",
            "chunk_id": 0,
            "mime": guess_mime(p),
            "transcript": transcript,
            "sha256": await asyncio.to_thread(sha256_file, p),
        }
        self._add_items([
            IngestItem(id=doc_id, document=proxy, embedding=vec, metadata=meta)
        ])

    async def _ingest_video_file(self, p: Path) -> None:
        with tempfile.TemporaryDirectory() as td:
            wav = os.path.join(td, "audio.wav")
            await asyncio.to_thread(self._ffmpeg_extract_audio_wav, str(p), wav)
            transcript = await asyncio.to_thread(self.embedder.transcribe_audio, wav)
            transcript = transcript or ""

            if transcript.strip():
                chunks = await self._split_text_with_lines(transcript)
                texts = [c[0] for c in chunks]
                vecs = await asyncio.to_thread(self.embedder.embed_text, texts)

                items = []
                for i, ((chunk, ls, le), vec) in enumerate(zip(chunks, vecs)):
                    doc_id = f"{str(p)}::video_transcript::{i}"
                    meta = {
                        "source": str(p),
                        "uri": str(p),
                        "modality": "video_transcript",
                        "embedding_space": "text",
                        "chunk_id": i,
                        "line_start": ls,
                        "line_end": le,
                        "mime": guess_mime(p),
                    }
                    items.append(
                        IngestItem(id=doc_id, document=chunk, embedding=vec, metadata=meta)
                    )
                self._add_items(items)

            frame_dir = os.path.join(td, "frames")
            os.makedirs(frame_dir, exist_ok=True)
            frames = await asyncio.to_thread(
                self._ffmpeg_extract_keyframes, str(p), frame_dir, 0.2
            )

            if frames:
                frame_vecs = await asyncio.to_thread(self.embedder.embed_images, frames)
                items = []
                for i, (fp, vec) in enumerate(zip(frames, frame_vecs)):
                    doc_id = f"{str(p)}::video_frame::{i}"
                    proxy = f"[video frame] {p.name} frame={i}"
                    meta = {
                        "source": str(p),
                        "uri": str(p),
                        "modality": "video_frame",
                        "embedding_space": "vision",
                        "chunk_id": i,
                        "frame_path": fp,
                        "mime": "image/jpeg",
                    }
                    items.append(
                        IngestItem(id=doc_id, document=proxy, embedding=vec, metadata=meta)
                    )
                self._add_items(items)

    async def _ingest_binary_file(self, p: Path) -> None:
        sha = await asyncio.to_thread(sha256_file, p)
        mime = guess_mime(p)
        proxy = f"[binary] name={p.name} mime={mime} sha256={sha} size={p.stat().st_size}"

        vec = (await asyncio.to_thread(self.embedder.embed_text, [proxy]))[0]
        doc_id = f"{str(p)}::binary::0"
        meta = {
            "source": str(p),
            "uri": str(p),
            "modality": "binary",
            "embedding_space": "text",
            "chunk_id": 0,
            "mime": mime,
            "sha256": sha,
            "size_bytes": p.stat().st_size,
        }
        self._add_items([
            IngestItem(id=doc_id, document=proxy, embedding=vec, metadata=meta)
        ])

    async def _ingest_table(self, table_text: str, metadata: Optional[Dict[str, Any]] = None) -> None:
        """Ingest a table represented as plain text (CSV/markdown/etc.).

        The table is embedded with the table encoder and stored under the
        ``table`` modality.  ``metadata`` may contain any additional fields the
        caller wishes to attach (e.g., source PDF, page number).
        """
        vec = await asyncio.to_thread(self.embedder.embed_table, table_text)
        doc_id = (
            f"{metadata.get('source', 'table')}::table::"
            f"{metadata.get('page', 0)}::{metadata.get('table_index', 0)}"
        )
        base_meta: Dict[str, Any] = {
            "source": metadata.get("source", "table"),
            "uri": metadata.get("uri", "unknown"),
            "modality": "table",
            "embedding_space": "table",
            "page": metadata.get("page", 0),
            "table_index": metadata.get("table_index", 0),
            "doc_id": doc_id,
        }

        if metadata:
            base_meta.update({k: v for k, v in metadata.items() if k not in base_meta})

        self._add_items([
            IngestItem(
                id=doc_id,
                document=table_text,
                embedding=vec,
                metadata=base_meta,
            )
        ])

    async def add_documents(
        self,
        documents: List[str],
        binary_payload: Optional[Dict[str, bytes]] = None,
        pbar: Optional[str] = "filling",
        pbar_title: str = "[@] Adding documents (multi-modal)",
        pbar_length: int = 20,
        pbar_spinner: str = "wait",
    ) -> None:
        if not documents and not binary_payload:
            return

        # Handle binary payload first if provided
        if binary_payload:
            for modality, data in binary_payload.items():
                suffix_map = {
                    "image": ".png",
                    "audio": ".wav",
                    "video": ".mp4",
                    "binary": ".bin",
                    "table": ".txt",
                    "pdf": ".pdf",
                }
                suffix = suffix_map.get(modality, ".bin")
                
                with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                    tmp.write(data)
                    p = Path(tmp.name)

                try:
                    if modality == "image":
                        await self._ingest_image_file(p)
                    elif modality == "audio":
                        await self._ingest_audio_file(p)
                    elif modality == "video":
                        await self._ingest_video_file(p)
                    elif modality == "table":
                        text = data.decode("utf-8", errors="ignore")
                        await self._ingest_table(text, metadata={"source": str(p), "uri": str(p)})
                    else:
                        await self._ingest_binary_file(p)
                finally:
                    try:
                        os.unlink(p)
                    except OSError:
                        pass

        if not documents:
            return

        paths: List[Path] = []
        raw_texts: List[str] = []

        for doc in documents:
            s = str(doc)
            is_potential_path = len(s) < 4096 and not s.strip().startswith(("{", "["))
            if is_potential_path:
                try:
                    p = Path(s)
                    if p.exists() and p.is_file():
                        paths.append(p)
                        continue
                except Exception:
                    pass
            raw_texts.append(s)

        if raw_texts:
            await self.add_text_docs(raw_texts, doc_source="raw_text")

        if not paths:
            return

        async def ingest_one(p: Path):
            ext = p.suffix.lower().lstrip(".")
            if ext in self.text_extensions:
                return await self._ingest_text_file(p)
            if ext in self.image_extensions:
                return await self._ingest_image_file(p)
            if ext in self.audio_extensions:
                return await self._ingest_audio_file(p)
            if ext in self.video_extensions:
                return await self._ingest_video_file(p)
            if ext in self.table_extensions:
                txt = await self._read_text_file(p)
                await self._ingest_table(txt, metadata={"source": str(p), "uri": str(p)})
                return
            return await self._ingest_binary_file(p)

        tasks = [ingest_one(p) for p in paths]

        if pbar:
            with alive_bar(
                len(tasks),
                bar=pbar,
                title=pbar_title,
                length=pbar_length,
                spinner=pbar_spinner,
            ) as bar:
                for t in asyncio.as_completed(tasks):
                    await t
                    bar()
        else:
            await asyncio.gather(*tasks)

    async def recursive_upload(
        self,
        directory: str,
        extensions: Optional[List[str]] = None,
        pbar: str = "filling",
        pbar_title: str = "[@] Uploading Files to vstore",
        pbar_length: int = 20,
        pbar_spinner: str = "wait",
    ) -> None:
        if extensions is None:
            extensions = list(
                self.text_extensions
                | self.image_extensions
                | self.audio_extensions
                | self.video_extensions
            )

        ext_set = set(e.lower().lstrip(".") for e in extensions)
        patterns = [f"**/*.{ext}" for ext in ext_set]

        file_paths = []
        for pattern in patterns:
            file_paths.extend(glob.glob(os.path.join(directory, pattern), recursive=True))

        valid_files = list({
            Path(p).resolve()
            for p in file_paths
            if Path(p).suffix.lower().lstrip(".") in ext_set
        })

        if not valid_files:
            print(f"No matching files found in {directory}")
            return

        batch_size = 64
        with alive_bar(
            len(valid_files),
            bar=pbar,
            title=pbar_title,
            length=pbar_length,
            spinner=pbar_spinner,
        ) as bar:
            for i in range(0, len(valid_files), batch_size):
                batch = valid_files[i:i + batch_size]
                await self.add_documents([str(p) for p in batch], pbar=None)
                for _ in batch:
                    bar()

    async def upload_dir(self, *args, **kwargs):
        return await self.recursive_upload(*args, **kwargs)

    async def add_url_doc(self, url: str) -> None:
        if not self.allow_online:
            print("[VSTORE] Online fetch disabled (allow_online=False). Skipping URL.")
            return

        if requests is None or bs4 is None:
            print("[VSTORE] requests/bs4 not installed; cannot fetch URL.")
            return

        try:
            resp = await asyncio.to_thread(requests.get, url, timeout=self.http_timeout)
            resp.raise_for_status()
            soup = bs4.BeautifulSoup(resp.content, "html.parser")
            text = soup.get_text(separator="\n", strip=True)
            await self.add_text_docs([text], doc_source=url)
        except Exception as e:
            print(f"[VSTORE] Failed to fetch URL {url}: {e}")

    async def add_url_docs(self, urls: List[str], pbar: str = "filling") -> None:
        if not urls:
            return

        with alive_bar(len(urls), bar=pbar, title="[@] Loading URL docs", length=20, spinner="wait") as bar:
            for u in urls:
                await self.add_url_doc(u)
                bar()

    # ----------------------------
    # Retrieval
    # ----------------------------
    async def remove_documents(self, file_paths: List[str]) -> None:
        if not file_paths:
            return

        for path in file_paths:
            ids_to_delete = []

            for coll in (self.text_collection, self.vision_collection):
                results = coll.get(where={"source": path})
                ids = results.get("ids", [])
                if ids:
                    coll.delete(ids=ids)
                    ids_to_delete.extend(ids)

            if self.bm25 is not None and ids_to_delete:
                self.bm25.delete(ids_to_delete)

    async def query_hybrid(
        self,
        query: str,
        k: int = 10,
        dense_k: int = 40,
        bm25_k: int = 40,
        include_images: bool = True,
        filter: Optional[Dict[str, Any]] = None,
        rerank_top_n: int = 50,
        channel_weights: Optional[Dict[str, float]] = None,
    ) -> List[Dict[str, Any]]:
        channel_weights = channel_weights or {
            "dense": 1.0,
            "bm25": 1.0,
            "vision": 0.7,
            "table": 1.0,
        }

        ranked_lists: List[List[str]] = []
        weights: List[float] = []

        q_emb = (await asyncio.to_thread(self.embedder.embed_text, [query]))[0]
        kwargs = {"query_embeddings": [q_emb], "n_results": dense_k}
        if filter:
            kwargs["where"] = filter

        res_dense = self.text_collection.query(**kwargs)
        dense_ids = res_dense["ids"][0] if res_dense and res_dense.get("ids") else []
        ranked_lists.append(dense_ids)
        weights.append(channel_weights.get("dense", 1.0))

        if self.bm25 is not None:
            bm25_ids = self.bm25.search(query, k=bm25_k)
            if bm25_ids:
                ranked_lists.append(bm25_ids)
                weights.append(channel_weights.get("bm25", 1.0))

        if include_images:
            qv = (await asyncio.to_thread(
                self.embedder.embed_text_for_vision_space, [query]
            ))[0]

            vision_where = None
            if filter:
                vision_where = filter

            res_vision = self.vision_collection.query(
                query_embeddings=[qv],
                n_results=dense_k,
                where=vision_where,
            )
            vision_ids = res_vision["ids"][0] if res_vision and res_vision.get("ids") else []
            if vision_ids:
                ranked_lists.append(vision_ids)
                weights.append(channel_weights.get("vision", 0.7))

        if hasattr(self, "table_collection"):
            q_table = await asyncio.to_thread(self.embedder.embed_table, query)
            table_res = self.table_collection.query(
                query_embeddings=[q_table],
                n_results=dense_k,
                where=filter,
            )
            table_ids = table_res.get("ids", [])[0] if table_res and table_res.get("ids") else []
            if table_ids:
                ranked_lists.append(table_ids)
                weights.append(channel_weights.get("table", 1.0))

        if self.use_rrf:
            fused_ids = rrf_fuse(
                ranked_lists,
                k=max(k, rerank_top_n),
                rrf_k=self.rrf_k,
                weights=weights,
            )
        else:
            fused_ids = dense_ids[:max(k, rerank_top_n)]

        candidates = []
        fused_rank = {doc_id: i for i, doc_id in enumerate(fused_ids)}

        text_ids = []
        vision_ids = []
        table_ids = []
        for doc_id in fused_ids:
            space = self._classify_doc_id_space(doc_id)
            if space == "vision":
                vision_ids.append(doc_id)
            elif space == "table":
                table_ids.append(doc_id)
            else:
                text_ids.append(doc_id)

        if text_ids:
            got_text = self.text_collection.get(ids=text_ids)
            for did, doc, meta in zip(
                got_text.get("ids", []),
                got_text.get("documents", []),
                got_text.get("metadatas", []),
            ):
                candidates.append({
                    "id": did,
                    "document": doc,
                    "source": meta.get("source"),
                    "uri": meta.get("uri"),
                    "modality": meta.get("modality"),
                    "chunk_id": meta.get("chunk_id"),
                    "metadata": meta,
                })

        if vision_ids:
            got_vision = self.vision_collection.get(ids=vision_ids)
            for did, doc, meta in zip(
                got_vision.get("ids", []),
                got_vision.get("documents", []),
                got_vision.get("metadatas", []),
            ):
                candidates.append({
                    "id": did,
                    "document": doc,
                    "source": meta.get("source"),
                    "uri": meta.get("uri"),
                    "modality": meta.get("modality"),
                    "chunk_id": meta.get("chunk_id"),
                    "metadata": meta,
                })

        if table_ids:
            got_table = self.table_collection.get(ids=table_ids)
            for did, doc, meta in zip(
                got_table.get("ids", []),
                got_table.get("documents", []),
                got_table.get("metadatas", []),
            ):
                candidates.append({
                    "id": did,
                    "document": doc,
                    "source": meta.get("source"),
                    "uri": meta.get("uri"),
                    "modality": meta.get("modality"),
                    "chunk_id": meta.get("chunk_id"),
                    "metadata": meta,
                })

        candidates.sort(key=lambda x: fused_rank.get(x["id"], 10**9))

        if self._reranker is not None and candidates:
            top = candidates[:rerank_top_n]
            pairs = [(query, c["document"]) for c in top]
            try:
                scores = await asyncio.to_thread(self._reranker.predict, pairs)
                for c, s in zip(top, scores):
                    c["_rerank_score"] = float(s)
                top.sort(key=lambda x: x.get("_rerank_score", -1e9), reverse=True)
                candidates = top + candidates[rerank_top_n:]
            except Exception:
                pass

        return candidates[:k]

    async def query(
        self,
        query: str,
        n_results: int = 5,
        filter: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        return await self.query_hybrid(query=query, k=n_results, filter=filter)

    def query_search(
        self,
        query: str,
        k: int = 4,
        filter: Optional[Dict[str, Any]] = None,
        with_score: bool = True,
        max_score: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        loop = asyncio.get_event_loop()

        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as ex:
                fut = ex.submit(asyncio.run, self.query_hybrid(query=query, k=k, filter=filter))
                results = fut.result()
        else:
            results = loop.run_until_complete(
                self.query_hybrid(query=query, k=k, filter=filter)
            )

        out = []
        for r in results:
            item = {
                "document": r["document"],
                "metadata": r["metadata"],
            }
            if with_score:
                item["score"] = r.get("_rerank_score")
            out.append(item)

        return out

    # ----------------------------
    # Stats / lifecycle
    # ----------------------------
    def update_doc_count(self) -> int:
        rt = self.text_collection.get()
        rv = self.vision_collection.get()
        return len(rt.get("ids", [])) + len(rv.get("ids", []))

    def get_stats(self) -> Dict[str, Any]:
        rt = self.text_collection.get()
        rv = self.vision_collection.get()
        rtbl = self.table_collection.get()

        ids = rt.get("ids", []) + rv.get("ids", []) + rtbl.get("ids", [])
        metas = rt.get("metadatas", []) + rv.get("metadatas", []) + rtbl.get("metadatas", [])

        sources = set((m or {}).get("source", "unknown") for m in metas)
        modalities: Dict[str, int] = {}
        for m in metas:
            mod = (m or {}).get("modality", "unknown")
            modalities[mod] = modalities.get(mod, 0) + 1

        return {
            "total_items": len(ids),
            "unique_sources": len(sources),
            "modalities": modalities,
            "collection_name_text": self.text_collection_name,
            "collection_name_vision": self.vision_collection_name,
            "collection_name_table": self.table_collection_name,
            "persist_directory": self.persist_directory,
            "bm25_enabled": self.bm25 is not None,
            "reranker_enabled": self._reranker is not None,
            "online_enabled": self.allow_online,
        }

    async def purge(self) -> None:
        for cname in (self.text_collection_name, self.vision_collection_name, self.table_collection_name):
            try:
                self.client.delete_collection(name=cname)
            except Exception:
                pass

        self.text_collection = self.client.get_or_create_collection(
            name=self.text_collection_name,
            metadata={"hnsw_space": "cosine"},
        )
        self.vision_collection = self.client.get_or_create_collection(
            name=self.vision_collection_name,
            metadata={"hnsw_space": "cosine"},
        )
        self.table_collection = self.client.get_or_create_collection(
            name=self.table_collection_name,
            metadata={"hnsw_space": "cosine"},
        )

        if self.bm25 is not None:
            try:
                self.bm25.delete(list(self.bm25.doc_text.keys()))
                self.bm25.close()
                if self.bm25.index_path.exists():
                    self.bm25.index_path.unlink()
                self.bm25 = PersistentBM25(
                    self.persist_directory,
                    index_name=f"{self.collection_name}_bm25",
                )
            except Exception:
                pass

    async def close(self) -> None:
        if self.bm25 is not None:
            self.bm25.close()
