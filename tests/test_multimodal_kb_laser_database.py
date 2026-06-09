# tests/test_multimodal_kb_laser_database.py
"""Laser‑cutting PDF multimodal KB test – verify each retrieved hit contains the expected answer.

For every question we retrieve the top‑5 results and consider the QA passed if **any** of the
hits contains the expected answer. The check is now tolerant:

* Direct substring match (case‑insensitive)
* All token match – each word from the expected answer appears in the hit
* Fuzzy similarity (SequenceMatcher) with a threshold of 0.6

The script follows the same overall structure as *laser_kb_temp.py* – it extracts images
and tables from the PDF, loads the QA markdown file
*Quote_Laser_Cutting_System_QA_v2.md*, builds a temporary multimodal knowledge base and
runs the validation.
"""

import sys, os, subprocess, asyncio, tempfile, re, difflib
from pathlib import Path

# ---------------------------------------------------------------------------
# Make repository root importable (as all other tests do)
# ---------------------------------------------------------------------------
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import chromadb
from chromadb.config import Settings
from framework.knowledgebase.base_multimodal_knowledgebase import MultimodalKnowledgeBase
from framework.knowledgebase.data_models import MultimodalKnowledgeBaseParams

# ---------------------------------------------------------------------------
# Helpers – image / table extraction (identical to laser_kb_temp.py)
# ---------------------------------------------------------------------------
def extract_images_from_pdf(pdf_path: Path, out_dir: Path) -> list[dict]:
    """Extract every image object from *pdf_path* into *out_dir*.
    Returns a list of dicts: {"page": int, "image_path": str}.
    """
    import fitz  # PyMuPDF
    out_dir.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(str(pdf_path))
    manifest = []
    for page_num in range(len(doc)):
        page = doc.load_page(page_num)
        for img_index, img in enumerate(page.get_images(full=True)):
            xref = img[0]
            pix = fitz.Pixmap(doc, xref)
            if pix.n - pix.alpha < 4:  # RGB or Gray
                pix = fitz.Pixmap(fitz.csRGB, pix)
            img_name = f"page_{page_num+1}_img_{img_index+1}.png"
            img_path = out_dir / img_name
            pix.save(str(img_path))
            pix = None
            manifest.append({"page": page_num + 1, "image_path": str(img_path)})
            print(f"Saved image {img_name} → {img_path}")
    return manifest

def _rows_to_csv(rows: list[list[str]]) -> str:
    return "\n".join([",".join(str(cell) for cell in r) for r in rows])

def extract_tables_from_pdf(pdf_path: Path) -> list[dict]:
    """Extract tables from *pdf_path*.
    Returns a list of dicts: {"page": int, "table_text": str} where the table
    is represented as CSV text. If the PDF/fitz version does not support
    ``find_tables`` an empty list is returned.
    """
    import fitz
    doc = fitz.open(str(pdf_path))
    tables = []
    for page_num in range(len(doc)):
        page = doc.load_page(page_num)
        if hasattr(page, "find_tables"):
            for tbl in page.find_tables():
                rows = tbl.extract()
                if not rows:
                    continue
                tables.append({"page": page_num + 1, "table_text": _rows_to_csv(rows)})
                print(f"Extracted table on page {page_num+1}, rows={len(rows)}")
    return tables

# ---------------------------------------------------------------------------
# Helper – parse the QA markdown file into a list of {question, answer}
# ---------------------------------------------------------------------------
def parse_qa_markdown(md_path: Path) -> list[dict]:
    """Read the markdown table and return a list of dicts with keys
    ``question`` and ``answer``.
    """
    qa = []
    with md_path.open(encoding="utf-8") as f:
        lines = [l.rstrip() for l in f]
    start = None
    for i, line in enumerate(lines):
        if line.startswith("| #"):
            start = i
            break
    if start is None:
        raise RuntimeError("QA table not found in markdown file.")
    i = start + 2  # skip header and separator
    while i < len(lines):
        line = lines[i]
        i += 1
        if not line.strip() or line.startswith("---"):
            continue
        parts = [p.strip() for p in line.split("|") if p.strip()]
        if len(parts) < 3:
            continue
        qa.append({"question": parts[1], "answer": parts[2]})
    return qa

# ---------------------------------------------------------------------------
# Matching utilities – tolerant containment check
# ---------------------------------------------------------------------------
def tokens_from_text(text: str) -> set[str]:
    """Return a set of lower‑cased alphanumeric tokens from *text*.
    Punctuation is stripped and numbers are kept.
    """
    return set(re.findall(r"[a-z0-9]+", text.lower()))

def answer_matches(expected: str, snippet: str) -> bool:
    """Return True if *snippet* is considered a match for *expected*.
    The logic is:
    1. Direct case‑insensitive substring.
    2. All tokens from *expected* appear in *snippet*.
    3. Fuzzy similarity (SequenceMatcher) above a threshold.
    """
    exp_low = expected.lower()
    snip_low = snippet.lower()
    # 1. Direct substring
    if exp_low in snip_low:
        return True
    # 2. Token containment
    exp_tokens = tokens_from_text(expected)
    snip_tokens = tokens_from_text(snippet)
    if exp_tokens.issubset(snip_tokens):
        return True
    # 3. Fuzzy similarity – useful for dates or slightly different wording
    ratio = difflib.SequenceMatcher(None, exp_low, snip_low).ratio()
    return ratio >= 0.6

# ---------------------------------------------------------------------------
# Main async test routine
# ---------------------------------------------------------------------------
async def main():
    with tempfile.TemporaryDirectory() as tmp_dir:
        persist_dir = Path(tmp_dir)
        print(f"Using temporary persist directory: {persist_dir}\n")

        # -----------------------------------------------------------------
        # Paths to artefacts
        # -----------------------------------------------------------------
        pdf_path = Path("/vast/home/scalandrini/wolf_uni/tests/data/Quote_Laser_Cutting_System.pdf")
        qa_path = Path("/vast/home/scalandrini/wolf-main/tests/data/Quote_Laser_Cutting_System_QA_v2.md")

        # -----------------------------------------------------------------
        # Extract images & tables
        # -----------------------------------------------------------------
        image_dir = Path("./laser_database_extracted_images")
        image_manifest = extract_images_from_pdf(pdf_path, image_dir)
        if not image_manifest:
            raise RuntimeError("No images extracted – the PDF may not contain embedded pictures.")

        table_manifest = extract_tables_from_pdf(pdf_path)
        if not table_manifest:
            print("No tables extracted – proceeding with text‑only ingestion.")

        # -----------------------------------------------------------------
        # Create ChromaDB client
        # -----------------------------------------------------------------
        #chroma_client = chromadb.Client(Settings(
        #    chroma_db_impl="duckdb+parquet",
        #    persist_directory=str(persist_dir)
        #))
        #chroma_client = chromadb.PersistentClient(path=str(persist_dir))
        chroma_client = chromadb.Client(Settings(persist_directory=str(persist_dir), anonymized_telemetry=False))

        # -----------------------------------------------------------------
        # KB configuration using MultimodalKnowledgeBaseParams
        # -----------------------------------------------------------------
        kb_params = MultimodalKnowledgeBaseParams(
            name="laser_test_kb",
            chunk_size=500,
            chunk_overlap=50,
            persist_dir=str(persist_dir),
            rebuild_vstore=True,
            use_bm25=True,
            use_rrf=True,
            rrf_k=60,
            use_reranker=False,
            allow_online=False,
            vrbz=0
        )

        kb = MultimodalKnowledgeBase(params=kb_params, db_client=chroma_client)
        await kb.store.purge()

        # -----------------------------------------------------------------
        # Ingest PDF text
        # -----------------------------------------------------------------
        print(f"Ingesting PDF text → {pdf_path}")
        kb.add_document(pdf_path, modality="text")

        # -----------------------------------------------------------------
        # Ingest images
        # -----------------------------------------------------------------
        print("Ingesting extracted images as image modality")
        for entry in image_manifest:
            img_path = Path(entry["image_path"]).resolve()
            kb.add_document(img_path, modality="image")

        # -----------------------------------------------------------------
        # Ingest tables
        # -----------------------------------------------------------------
        print("Ingesting extracted tables as table modality")
        for idx, tbl in enumerate(table_manifest):
            meta = {
                "source_pdf": str(pdf_path),
                "page": tbl["page"],
                "table_index": idx,
                "embedding_space": "table",
            }
            kb.add_document(tbl["table_text"], modality="table", metadata=meta)

        # -----------------------------------------------------------------
        # Load QA pairs
        # -----------------------------------------------------------------
        qa_items = parse_qa_markdown(qa_path)
        print(f"Loaded {len(qa_items)} QA entries from {qa_path}\n")

        failures = []
        for idx, item in enumerate(qa_items, start=1):
            question = item["question"]
            expected = item["answer"]
            print(f"\n--- QA {idx}: {question}\nExpected snippet (truncated): {expected[:120]}…")

            # Adjust channel weights for image / table oriented queries
            if re.search(r"picture|image|photo", question, re.IGNORECASE):
                filter_cfg = {"channel_weights": {"dense": 0.8, "bm25": 0.5, "vision": 1.5}}
            elif re.search(r"table|specifications|list|row|column", question, re.IGNORECASE):
                filter_cfg = {"channel_weights": {"dense": 0.5, "bm25": 0.5, "vision": 0.5, "table": 1.7}}
            else:
                filter_cfg = None

            results = await kb.query(question, n_results=5, filter=filter_cfg)
            hit_matches = []
            for hit in results:
                snippet = hit.get("document", "")
                match = answer_matches(expected, snippet)
                hit_matches.append(match)
                status = "✅" if match else "❌"
                meta = hit.get("metadata", {})
                modality = meta.get("modality", "unknown")
                page = meta.get("page") or meta.get("line_start") or "?"
                print(f"{status} [{modality.upper():5}] (page {page}): {snippet[:200]}…")

            if not any(hit_matches):
                failures.append({"index": idx, "question": question, "expected": expected, "results": results})
                print(f"❌ No hit contained the expected answer for QA {idx}")
            else:
                print(f"✅ At least one hit matched for QA {idx}")

        # -----------------------------------------------------------------
        # Report / raise if any failures
        # -----------------------------------------------------------------
        if failures:
            summary = "\n".join(
                f"QA {f['index']}: {f['question']}\n  Expected: {f['expected']}\n"
                for f in failures
            )
            raise AssertionError(f"{len(failures)} QA checks failed:\n{summary}")
        else:
            print("\nAll QA checks passed! 🎉")

        # -----------------------------------------------------------------
        # Close KB
        # -----------------------------------------------------------------
        await kb.close()
        print("\n✅ Knowledge base closed – temporary store at", persist_dir)

# ---------------------------------------------------------------------------
if __name__ == "__main__":
    asyncio.run(main())
