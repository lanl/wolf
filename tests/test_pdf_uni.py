#!/usr/bin/env python3
"""Test the universe-level /kbs/{name}/add_pdf HTTP endpoint in-process.

This script:
- builds a BaseUniverse
- creates a multimodal KB through the FastAPI endpoint
- calls POST /kbs/{name}/add_pdf using TestClient
- prints the ingestion summary
- fetches KB stats and sources through the universe API
"""

import sys
import json
import pathlib
import shutil

# Add framework to path
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from fastapi.testclient import TestClient

from framework.universes.base_universe import create_app, build_default_universe
from framework.knowledgebase.data_models import MultimodalKnowledgeBaseParams
from framework.data_store.data_models import MultimodalEmbeddingParams


def test_universe_pdf_add():
    pdf_path = pathlib.Path("/users/scalandrini/wolf_github/tests/Quote_Laser_Cutting_System.pdf")
    test_db_dir = pathlib.Path("/users/scalandrini/wolf_github/tests/test_pdf_uni_kb")
    kb_name = "test_pdf_uni_kb"
    extracted_images_dir = test_db_dir / "persisted_pdf_images"

    print("=" * 80)
    print("Testing BaseUniverse /kbs/{name}/add_pdf endpoint")
    print("=" * 80)

    if not pdf_path.exists():
        print(f"❌ ERROR: PDF file not found at {pdf_path}")
        return False

    print(f"✅ Found PDF: {pdf_path}")
    print(f"   Size: {pdf_path.stat().st_size / 1024:.2f} KB\n")

    if test_db_dir.exists():
        shutil.rmtree(test_db_dir)
        print(f"🧹 Cleaned up existing test directory: {test_db_dir}")

    test_db_dir.mkdir(parents=True, exist_ok=True)
    print(f"📁 Created test directory: {test_db_dir}")
    print(f"🖼️  Extracted images will be persisted under: {extracted_images_dir}\n")

    try:
        print("🔧 Building in-process universe app...")
        universe = build_default_universe()
        app = create_app(universe)
        client = TestClient(app)
        print("✅ Universe app ready\n")

        print("🔧 Creating multimodal KB through /kbs endpoint...")
        kb_payload = {
            "type": "multimodal",
            "kb_params": {
                "name": kb_name,
                "description": "Universe-level test KB for PDF ingestion",
                "chunk_size": 512,
                "chunk_overlap": 50,
                "persist_dir": str(test_db_dir),
                "rebuild_vstore": True,
                "persist_extracted_pdf_images": True,
                "extracted_pdf_images_dir": str(extracted_images_dir),
                "embedding": {
                    "embedding_type": "clip",
                    "model": "clip-ViT-B-32"
                },
                "vrbz": 2
            }
        }
        create_resp = client.post("/kbs", json=kb_payload)
        if create_resp.status_code != 200:
            print(f"❌ ERROR creating KB: HTTP {create_resp.status_code}")
            print(create_resp.text)
            return False
        print("✅ Multimodal KB created\n")

        print("=" * 80)
        print("Starting PDF ingestion via universe HTTP endpoint...")
        print("=" * 80)

        add_pdf_payload = {
            "pdf_path": str(pdf_path),
            "metadata": {
                "test": "pdf_universe_ingestion",
                "source_type": "quote"
            },
            "extract_images": True,
            "extract_tables": True,
            "persist_extracted_images": True,
            "extracted_image_dir": str(extracted_images_dir)
        }
        pdf_resp = client.post(f"/kbs/{kb_name}/add_pdf", json=add_pdf_payload)
        if pdf_resp.status_code != 200:
            print(f"❌ ERROR ingesting PDF: HTTP {pdf_resp.status_code}")
            print(pdf_resp.text)
            return False

        pdf_result = pdf_resp.json()

        print("\n" + "=" * 80)
        print("Universe PDF Ingestion Response:")
        print("=" * 80)
        print(json.dumps(pdf_result, indent=2))

        summary = pdf_result.get("summary", {})

        print("\n" + "=" * 80)
        print("Verification:")
        print("=" * 80)

        success = True

        if not pdf_result.get("ok", False):
            print("❌ FAILED: Endpoint response has ok=false")
            success = False
        else:
            print("✅ Endpoint returned ok=true")

        if summary.get("status") not in ["success", "partial_success"]:
            print("❌ FAILED: Unexpected summary status")
            success = False
        else:
            print(f"✅ Summary status: {summary.get('status')}")

        n_pages = summary.get("n_pages", 0)
        n_text = summary.get("n_text_chunks", 0)
        n_images = summary.get("n_images", 0)
        n_tables = summary.get("n_tables", 0)

        print(f"📄 Pages processed: {n_pages}")
        print(f"📝 Text chunks: {n_text}")
        print(f"🖼️  Images extracted: {n_images}")
        print(f"📊 Tables extracted: {n_tables}")

        if n_pages == 0:
            print("❌ FAILED: No pages processed")
            success = False

        stats_resp = client.get(f"/kbs/{kb_name}/stats")
        if stats_resp.status_code != 200:
            print(f"❌ ERROR getting stats: HTTP {stats_resp.status_code}")
            print(stats_resp.text)
            return False
        stats = stats_resp.json()

        print("\n" + "=" * 80)
        print("Knowledge Base Statistics:")
        print("=" * 80)
        print(json.dumps(stats, indent=2))

        sources_resp = client.get(f"/kbs/{kb_name}/sources")
        if sources_resp.status_code != 200:
            print(f"❌ ERROR getting sources: HTTP {sources_resp.status_code}")
            print(sources_resp.text)
            return False
        sources = sources_resp.json()

        print("\n📚 Sources in inventory:")
        for source in sources:
            print(f"   - {source['source_path']} ({source['modality']}, {source['n_chunks']} chunks)")

        print("\n" + "=" * 80)
        if success:
            print("✅ TEST PASSED")
        else:
            print("❌ TEST FAILED")
        print("=" * 80)

        return success

    except Exception as e:
        print(f"\n❌ EXCEPTION: {type(e).__name__}: {e}")
        import traceback
        print("\nFull traceback:")
        print(traceback.format_exc())
        return False


if __name__ == "__main__":
    success = test_universe_pdf_add()
    sys.exit(0 if success else 1)
