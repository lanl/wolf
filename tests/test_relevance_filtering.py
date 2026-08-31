#!/usr/bin/env python3
from __future__ import annotations

"""End-to-end integration test for workflow-level relevance filtering.

This test:
- builds an in-process universe
- creates a multimodal KB through the FastAPI endpoint
- ingests Quote_Laser_Cutting_System.pdf
- reads questions from tests/questions.md
- randomly selects 4 questions (with a fixed seed for reproducibility)
- calls extract_relevant_docs_from_kbs(...) for each question
- prints concise summaries of filtered relevant results
- synthesizes a final answer from the filtered relevant results for inspection
"""

import json
import pathlib
import random
import re
import shutil
import sys
from typing import List

# Add framework to path
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from fastapi.testclient import TestClient

from framework.universes.base_universe import create_app, build_default_universe
from framework.workflows.relevance_filtering import extract_relevant_docs_from_kbs


class HeuristicYesNoAgent:
    """Very small deterministic agent stub for yes/no relevance checks.

    It does not answer the user's question.
    It only decides whether a retrieved candidate looks relevant to the question.
    """

    def __init__(self):
        self.settings = {}

    def get_chat_response(self, prompt, llm_sampling_settings=None):
        prompt = str(prompt)

        question_match = re.search(
            r"<User Question>\s*(.*?)\s*</User Question>",
            prompt,
            flags=re.IGNORECASE | re.DOTALL,
        )
        doc_match = re.search(
            r"\*\* document_content \*\*\s*:\s*(.*?)(?:</Retrieved Result>|<User Question>)",
            prompt,
            flags=re.IGNORECASE | re.DOTALL,
        )
        metadata_match = re.search(
            r"\*\* metadata \*\*\s*:\s*(.*?)(?:\*\* document_content \*\*|</Retrieved Result>)",
            prompt,
            flags=re.IGNORECASE | re.DOTALL,
        )

        question = (question_match.group(1).strip() if question_match else "").lower()
        document = (doc_match.group(1).strip() if doc_match else "").lower()
        metadata = (metadata_match.group(1).strip() if metadata_match else "").lower()
        haystack = f"{document}\n{metadata}"

        q_tokens = set(re.findall(r"[a-z0-9]+", question))
        stop = {
            "what", "which", "who", "when", "where", "why", "how", "is", "are", "the", "of",
            "a", "an", "to", "for", "on", "in", "does", "do", "did", "was", "were", "and",
            "or", "with", "about", "this", "that", "it", "its", "be", "as", "at", "from",
            "by", "whole", "system", "machine", "list", "three", "available", "full", "name",
            "main", "maximum", "according", "option", "options"
        }
        q_tokens = {t for t in q_tokens if len(t) > 2 and t not in stop}

        if not q_tokens:
            return "no"

        overlap = sum(1 for t in q_tokens if t in haystack)

        strong_phrases = [
            "proposal",
            "table of contents",
            "auto collimation",
            "ensis",
            "beam quality",
            "flying-fiber",
            "z-axis",
            "repeatability",
            "sheet size",
            "material weight",
            "electrical requirements",
            "laser resonator",
            "dust collector",
            "discharge port",
            "automatic nozzle changer",
            "chiller",
            "wavelength",
            "beam divergence",
            "power stability",
        ]

        if any(phrase in question and phrase in haystack for phrase in strong_phrases):
            return "yes"

        return "yes" if overlap >= 2 else "no"


class SynthesisAgent:
    """Simple deterministic synthesis stub.

    This produces a compact answer from filtered evidence so the integration
    test can validate whether retrieved evidence is collectively meaningful.
    """

    def __init__(self):
        self.settings = {}

    def get_chat_response(self, prompt, llm_sampling_settings=None):
        prompt = str(prompt)
        question_match = re.search(
            r"<Question>\s*(.*?)\s*</Question>",
            prompt,
            flags=re.IGNORECASE | re.DOTALL,
        )
        evidence_match = re.search(
            r"<Relevant Evidence>\s*(.*?)\s*</Relevant Evidence>",
            prompt,
            flags=re.IGNORECASE | re.DOTALL,
        )

        question = (question_match.group(1).strip() if question_match else "")
        evidence = (evidence_match.group(1).strip() if evidence_match else "")
        evidence_l = evidence.lower()
        question_l = question.lower()

        if not evidence.strip():
            return "Insufficient context to respond."

        if "chiller" in question_l:
            lines = []
            for line in evidence.splitlines():
                clean = line.strip(" -\t")
                if "chiller" in clean.lower() or "kw" in clean.lower() or "ton" in clean.lower():
                    lines.append(clean)
            if lines:
                uniq = []
                for line in lines:
                    if line not in uniq:
                        uniq.append(line)
                return "Possible answer from retrieved evidence: " + " | ".join(uniq[:6])

        if "title" in question_l and "proposal" in evidence_l:
            return "Possible answer from retrieved evidence: the proposal title appears in the retrieved document text."

        if "addressed to" in question_l:
            return "Possible answer from retrieved evidence: the addressee appears in the retrieved proposal text."

        if "date" in question_l and ("202" in evidence_l or "prepared" in evidence_l or "date" in evidence_l):
            return "Possible answer from retrieved evidence: the preparation date appears in the retrieved proposal text."

        preview = " ".join(evidence.split())
        if len(preview) > 300:
            preview = preview[:300] + "..."
        return "Possible answer from retrieved evidence: " + preview


def _parse_questions_md(path: pathlib.Path) -> List[str]:
    text = path.read_text(encoding="utf-8")
    questions: List[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        if "Question" in line and "#" in line:
            continue
        if set(line.replace("|", "").replace("-", "").strip()) == set():
            continue

        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 4:
            continue
        q_text = parts[2]
        if q_text:
            questions.append(q_text)
    return questions


def _preview(text: str, n: int = 220) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[:n] + "..."


def _synthesize_answer_from_relevant_results(question: str, relevant_results: List[dict], agent: SynthesisAgent) -> str:
    evidence_blocks = []
    for idx, item in enumerate(relevant_results, start=1):
        meta = item.get("metadata") or {}
        evidence_blocks.append(
            f"""
<Evidence {idx}>
  id: {item.get('id')}
  modality: {item.get('modality')}
  source: {item.get('source')}
  page: {meta.get('page_number', meta.get('page', '?'))}
  content: {item.get('document', '')}
</Evidence {idx}>
"""
        )

    prompt = f"""
You are given a question and retrieved evidence judged relevant.
Answer the question using only that evidence.
If the evidence is insufficient, say: Insufficient context to respond.

<Question>
{question}
</Question>

<Relevant Evidence>
{''.join(evidence_blocks)}
</Relevant Evidence>
"""
    return agent.get_chat_response(prompt, llm_sampling_settings=agent.settings)


def test_relevance_filtering_integration():
    repo_root = pathlib.Path(__file__).parent.parent
    pdf_path = repo_root / "tests" / "Quote_Laser_Cutting_System.pdf"
    questions_path = repo_root / "tests" / "questions.md"
    test_db_dir = repo_root / "tests" / "test_relevance_filtering_kb"
    extracted_images_dir = test_db_dir / "persisted_pdf_images"
    kb_name = "test_relevance_filtering_kb"
    universe_name = "integration_universe"
    sample_size = 4
    rng_seed = 42

    print("=" * 80)
    print("Testing end-to-end relevance filtering integration")
    print("=" * 80)

    if not pdf_path.exists():
        print(f"❌ ERROR: PDF file not found at {pdf_path}")
        return False
    if not questions_path.exists():
        print(f"❌ ERROR: Questions file not found at {questions_path}")
        return False

    questions = _parse_questions_md(questions_path)
    if len(questions) < sample_size:
        print(f"❌ ERROR: Need at least {sample_size} questions, found {len(questions)}")
        return False

    print(f"✅ Found PDF: {pdf_path}")
    print(f"✅ Found question database: {questions_path}")
    print(f"✅ Parsed {len(questions)} questions")

    if test_db_dir.exists():
        shutil.rmtree(test_db_dir)
        print(f"🧹 Cleaned up existing test directory: {test_db_dir}")

    test_db_dir.mkdir(parents=True, exist_ok=True)
    print(f"📁 Created test directory: {test_db_dir}")
    print(f"🖼️  Extracted images directory: {extracted_images_dir}\n")

    try:
        print("🔧 Building in-process universe app...")
        universe = build_default_universe()
        if getattr(universe, "info", None) is not None:
            universe.info.name = universe_name
        app = create_app(universe)
        client = TestClient(app)
        print("✅ Universe app ready\n")

        print("🔧 Creating multimodal KB through /kbs endpoint...")
        kb_payload = {
            "type": "multimodal",
            "kb_params": {
                "name": kb_name,
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
                "vrbz": 1
            }
        }
        create_resp = client.post("/kbs", json=kb_payload)
        if create_resp.status_code != 200:
            print(f"❌ ERROR creating KB: HTTP {create_resp.status_code}")
            print(create_resp.text)
            return False
        print("✅ Multimodal KB created\n")

        print("🔧 Ingesting PDF through /add_pdf endpoint...")
        add_pdf_payload = {
            "pdf_path": str(pdf_path),
            "metadata": {
                "test": "relevance_filtering_integration",
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
        summary = pdf_result.get("summary", {})
        print("✅ PDF ingested")
        print(json.dumps(summary, indent=2))

        if not pdf_result.get("ok", False):
            print("❌ ERROR: PDF ingestion returned ok=false")
            return False
        if summary.get("n_pages", 0) <= 0:
            print("❌ ERROR: PDF ingestion processed zero pages")
            return False

        rng = random.Random(rng_seed)
        selected_questions = rng.sample(questions, sample_size)
        relevance_agent = HeuristicYesNoAgent()
        synthesis_agent = SynthesisAgent()

        print("\n" + "=" * 80)
        print(f"Running relevance filtering on {sample_size} sampled questions (seed={rng_seed})")
        print("=" * 80)

        success = True
        any_relevant = False

        for idx, question in enumerate(selected_questions, start=1):
            print("\n" + "-" * 80)
            print(f"Question {idx}: {question}")
            print("-" * 80)

            result = extract_relevant_docs_from_kbs(
                user_input=question,
                agent=relevance_agent,
                infra=type("_InfraShim", (), {"UNIVs": {universe_name: universe}})(),
                targets=[{"universe": universe_name, "kb_name": kb_name}],
                k_per_kb=10,
                context_window=1,
                max_candidates=20,
                dedupe_by="id",
                require_strict_yes_no=True,
                include_nonrelevant=True,
                show_steps=False,
            )

            print(f"Candidates retrieved: {result['n_candidates']}")
            print(f"Relevant results:    {result['n_relevant']}")
            print(f"Search errors:       {len(result.get('search_errors', []))}")
            print(f"Relevance errors:    {len(result.get('relevance_errors', []))}")

            if result.get("search_errors"):
                print("❌ Search errors detected")
                print(json.dumps(result["search_errors"], indent=2))
                success = False

            if result.get("relevance_errors"):
                print("❌ Relevance errors detected")
                print(json.dumps(result["relevance_errors"], indent=2))
                success = False

            relevant_results = result.get("relevant_results", [])
            if relevant_results:
                any_relevant = True
                for j, item in enumerate(relevant_results, start=1):
                    meta = item.get("metadata") or {}
                    page = meta.get("page_number", meta.get("page", "?"))
                    print(f"  [{j}] id={item.get('id')}")
                    print(f"      modality={item.get('modality')} page={page} source={item.get('source')}")
                    print(f"      decision={item.get('relevance_decision')} raw={item.get('agent_raw_answer')!r}")
                    print(f"      preview={_preview(item.get('document', ''))}")
            else:
                print("  No relevant results for this question.")

            final_answer = _synthesize_answer_from_relevant_results(
                question=question,
                relevant_results=relevant_results,
                agent=synthesis_agent,
            )
            print("\n  Synthesized answer:")
            print(f"  {final_answer}")

        print("\n" + "=" * 80)
        if success and any_relevant:
            print("✅ TEST PASSED")
        elif success and not any_relevant:
            print("⚠️ TEST COMPLETED BUT NO RELEVANT RESULTS WERE FOUND")
            success = False
        else:
            print("❌ TEST FAILED")
        print("=" * 80)

        return success and any_relevant

    except Exception as e:
        print(f"\n❌ EXCEPTION: {type(e).__name__}: {e}")
        import traceback
        print("\nFull traceback:")
        print(traceback.format_exc())
        return False


if __name__ == "__main__":
    ok = test_relevance_filtering_integration()
    sys.exit(0 if ok else 1)
