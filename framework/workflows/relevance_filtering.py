from __future__ import annotations

from typing import Any, Dict, List
import asyncio
import requests

DEFAULT_TIMEOUT = 30
ALLOWED_DEDUPE_KEYS = {"id", "source", "document"}


def _run_async_in_sync(coro):
    try:
        asyncio.get_running_loop()
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(asyncio.run, coro)
            return future.result()
    except RuntimeError:
        return asyncio.run(coro)


def _search_kb_target(
    infra: Any,
    universe_name: str,
    kb_name: str,
    query: str,
    k_per_kb: int = 5,
    context_window: int = 1,
) -> List[Dict[str, Any]]:
    univ = infra.UNIVs[universe_name]

    if hasattr(univ, "get_base_url"):
        univ_base_url = univ.get_base_url()
        response = requests.post(
            f"{univ_base_url}/kbs/{kb_name}/search",
            json={
                "query": query,
                "k": k_per_kb,
                "context_window": context_window,
            },
            timeout=DEFAULT_TIMEOUT,
        )
        response.raise_for_status()
        result = response.json()
    elif hasattr(univ, "akb_search"):
        result = _run_async_in_sync(
            univ.akb_search(kb_name, query, k=k_per_kb, context_window=context_window)
        )
    elif hasattr(univ, "kb_search"):
        result = univ.kb_search(kb_name, query, k=k_per_kb, context_window=context_window)
    else:
        raise ValueError(
            f"Universe object for '{universe_name}' supports neither get_base_url nor kb_search/akb_search"
        )

    if not isinstance(result, list):
        raise ValueError(
            f"Invalid search response for universe={universe_name} kb={kb_name}: expected list, got {type(result).__name__}"
        )
    return result


def _normalize_candidate(
    raw_result: Dict[str, Any],
    universe_name: str,
    kb_name: str,
    retrieval_rank: int,
) -> Dict[str, Any]:
    metadata = raw_result.get("metadata") or {}
    document = raw_result.get("document", "")
    source = raw_result.get("source", metadata.get("source"))
    uri = raw_result.get("uri", metadata.get("uri"))
    modality = raw_result.get("modality", metadata.get("modality"))
    chunk_id = raw_result.get("chunk_id", metadata.get("chunk_id"))
    candidate_id = raw_result.get("id")
    if candidate_id is None:
        candidate_id = f"{universe_name}::{kb_name}::{retrieval_rank}"

    return {
        "id": candidate_id,
        "document": document,
        "source": source,
        "uri": uri,
        "modality": modality,
        "chunk_id": chunk_id,
        "metadata": metadata,
        "matched_target": {
            "universe": universe_name,
            "kb_name": kb_name,
        },
        "retrieval_rank": retrieval_rank,
    }


def _dedupe_candidates(candidates: List[Dict[str, Any]], dedupe_by: str = "id") -> List[Dict[str, Any]]:
    if dedupe_by not in ALLOWED_DEDUPE_KEYS:
        raise ValueError(f"Invalid dedupe_by='{dedupe_by}'. Allowed values: {sorted(ALLOWED_DEDUPE_KEYS)}")

    seen = set()
    deduped: List[Dict[str, Any]] = []
    for candidate in candidates:
        key = candidate.get(dedupe_by)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)
    return deduped


def _build_relevance_prompt(user_input: str, candidate: Dict[str, Any]) -> str:
    target = candidate.get("matched_target", {})
    return f"""Are the following retrieved context and user question relevant to each other?

<Retrieved Result>
  ** universe **           : {target.get('universe')}
  ** kb_name **            : {target.get('kb_name')}
  ** document_id **        : {candidate.get('id')}
  ** modality **           : {candidate.get('modality')}
  ** source **             : {candidate.get('source')}
  ** uri **                : {candidate.get('uri')}
  ** metadata **           : {candidate.get('metadata')}
  ** document_content **   : {candidate.get('document')}
</Retrieved Result>

<User Question>
  {user_input}
</User Question>

Provide only yes or no.
"""


def _parse_yes_no(agent_output: Any) -> str:
    ans = str(agent_output or "").strip().lower()
    if ans.startswith("yes"):
        return "yes"
    if ans.startswith("no"):
        return "no"
    return "unknown"


def _ask_agent_relevance(
    agent: Any,
    user_input: str,
    candidate: Dict[str, Any],
    require_strict_yes_no: bool = True,
) -> Dict[str, Any]:
    prompt = _build_relevance_prompt(user_input, candidate)
    raw_answer = agent.get_chat_response(prompt, llm_sampling_settings=agent.settings)
    decision = _parse_yes_no(raw_answer)
    if require_strict_yes_no and decision == "unknown":
        raise ValueError(
            f"Agent relevance answer is not strict yes/no for candidate {candidate.get('id')}: {raw_answer}"
        )
    return {
        "decision": decision,
        "raw_answer": raw_answer,
    }


def extract_relevant_docs_from_kbs(
    user_input: str,
    agent: Any,
    infra: Any,
    targets: List[Dict[str, str]],
    k_per_kb: int = 5,
    context_window: int = 1,
    max_candidates: int = 20,
    dedupe_by: str = "id",
    require_strict_yes_no: bool = True,
    include_nonrelevant: bool = False,
    show_steps: bool = False,
) -> Dict[str, Any]:
    if not str(user_input).strip():
        raise ValueError("user_input must be a non-empty string")
    if not targets:
        raise ValueError("targets must not be empty")
    if k_per_kb <= 0:
        raise ValueError("k_per_kb must be > 0")
    if max_candidates <= 0:
        raise ValueError("max_candidates must be > 0")
    if dedupe_by not in ALLOWED_DEDUPE_KEYS:
        raise ValueError(f"Invalid dedupe_by='{dedupe_by}'. Allowed values: {sorted(ALLOWED_DEDUPE_KEYS)}")

    all_candidates: List[Dict[str, Any]] = []
    search_errors: List[Dict[str, Any]] = []

    for idx, target in enumerate(targets, start=1):
        universe_name = target.get("universe")
        kb_name = target.get("kb_name")
        if not universe_name or not kb_name:
            raise ValueError(f"Each target must contain 'universe' and 'kb_name'. Invalid target: {target}")

        if show_steps:
            print(f"[+][extract_relevant_docs_from_kbs]: searching target {idx:04d} universe={universe_name} kb={kb_name}")

        try:
            raw_results = _search_kb_target(
                infra=infra,
                universe_name=universe_name,
                kb_name=kb_name,
                query=user_input,
                k_per_kb=k_per_kb,
                context_window=context_window,
            )
            if show_steps:
                print(f"   -> retrieved {len(raw_results)} candidates")
            for rank, raw in enumerate(raw_results, start=1):
                all_candidates.append(
                    _normalize_candidate(
                        raw_result=raw,
                        universe_name=universe_name,
                        kb_name=kb_name,
                        retrieval_rank=rank,
                    )
                )
        except Exception as e:
            search_errors.append({
                "target": target,
                "error": str(e),
            })
            if show_steps:
                print(f"   -> search error: {e}")

    deduped_candidates = _dedupe_candidates(all_candidates, dedupe_by=dedupe_by)
    truncated_candidates = deduped_candidates[:max_candidates]

    relevant_results: List[Dict[str, Any]] = []
    nonrelevant_results: List[Dict[str, Any]] = []
    relevance_errors: List[Dict[str, Any]] = []

    for idx, candidate in enumerate(truncated_candidates, start=1):
        try:
            decision_info = _ask_agent_relevance(
                agent=agent,
                user_input=user_input,
                candidate=candidate,
                require_strict_yes_no=require_strict_yes_no,
            )
            enriched = dict(candidate)
            enriched["relevance_decision"] = decision_info["decision"]
            enriched["agent_raw_answer"] = decision_info["raw_answer"]

            if show_steps:
                print(f"   ---> [RELEVANCE CHECK][{idx:04d}][{candidate.get('id')}]: {decision_info['raw_answer']}")

            if decision_info["decision"] == "yes":
                relevant_results.append(enriched)
            elif include_nonrelevant:
                nonrelevant_results.append(enriched)
        except Exception as e:
            relevance_errors.append({
                "candidate_id": candidate.get("id"),
                "error": str(e),
            })
            if show_steps:
                print(f"   -> relevance error for {candidate.get('id')}: {e}")

    result: Dict[str, Any] = {
        "query": user_input,
        "targets": targets,
        "n_targets": len(targets),
        "n_candidates": len(truncated_candidates),
        "n_relevant": len(relevant_results),
        "relevant_results": relevant_results,
        "search_errors": search_errors,
        "relevance_errors": relevance_errors,
    }

    if include_nonrelevant:
        result["nonrelevant_results"] = nonrelevant_results
        result["n_nonrelevant"] = len(nonrelevant_results)

    return result
