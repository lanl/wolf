from __future__ import annotations

from typing import Any, Dict, List
import asyncio
import re
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


def _candidate_priority(candidate: Dict[str, Any], intent: Dict[str, bool]) -> tuple:
    modality = str(candidate.get("modality") or "")
    retrieval_rank = int(candidate.get("retrieval_rank") or 10**9)

    if intent.get("is_visual"):
        modality_rank = 0 if modality == "image" else 1 if modality == "text" else 2
    elif intent.get("is_table"):
        modality_rank = 0 if modality == "table" else 1 if modality == "text" else 2
    else:
        modality_rank = 0

    return (modality_rank, retrieval_rank)


def _prioritize_candidates_for_intent(candidates: List[Dict[str, Any]], user_input: str) -> List[Dict[str, Any]]:
    intent = _detect_query_intent(user_input)
    if not candidates:
        return candidates
    if not intent.get("is_visual") and not intent.get("is_table"):
        return candidates
    return sorted(candidates, key=lambda c: _candidate_priority(c, intent))


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


def _normalize_results(
    raw_results: List[Dict[str, Any]],
    universe_name: str,
    kb_name: str,
) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    for rank, raw in enumerate(raw_results, start=1):
        normalized.append(
            _normalize_candidate(
                raw_result=raw,
                universe_name=universe_name,
                kb_name=kb_name,
                retrieval_rank=rank,
            )
        )
    return normalized


def _build_relevance_prompt(user_input: str, candidate: Dict[str, Any]) -> str:
    target = candidate.get("matched_target", {})
    intent = _detect_query_intent(user_input)

    extra_instruction = ""
    if intent.get("is_visual"):
        extra_instruction = (
            "The user is specifically asking about an image/figure/picture. "
            "Prefer results that are themselves images or that directly identify or describe an image over general nearby text when available."
        )
    elif intent.get("is_table"):
        extra_instruction = (
            "The user is specifically asking about a table/tabular content. "
            "Prefer results that are tables or that directly identify or describe a table over general nearby text when available."
        )

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

<Instruction>
{extra_instruction or 'Judge relevance normally.'}
</Instruction>

Provide only yes or no.
"""


def _build_sufficiency_prompt(user_input: str, relevant_candidates: List[Dict[str, Any]]) -> str:
    formatted_candidates: List[str] = []
    for idx, candidate in enumerate(relevant_candidates, start=1):
        formatted_candidates.append(
            f"""<Relevant Result {idx}>
  ** document_id **      : {candidate.get('id')}
  ** modality **         : {candidate.get('modality')}
  ** source **           : {candidate.get('source')}
  ** uri **              : {candidate.get('uri')}
  ** metadata **         : {candidate.get('metadata')}
  ** document_content ** : {candidate.get('document')}
</Relevant Result {idx}>"""
        )

    return f"""Determine whether the user question can be answered accurately and specifically from the retrieved results below alone.

Important rules:
- Relevance is not enough; judge answer sufficiency.
- Answer yes only if the retrieved results contain enough evidence to answer the user's actual question directly.
- If the question asks for an exact target such as an equation number, figure number, axis label, date, count, name, section, identifier, or similarly precise fact, answer no unless that exact target is present or directly recoverable from the retrieved results.
- If the results are only topically related, partially related, or missing the key detail needed for the answer, answer no.
- Do not guess. Do not assume missing information.

<User Question>
{user_input}
</User Question>

<Retrieved Relevant Results>
{chr(10).join(formatted_candidates)}
</Retrieved Relevant Results>

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
    raw_answer = agent.get_chat_response(prompt)
    decision = _parse_yes_no(raw_answer)
    if require_strict_yes_no and decision == "unknown":
        raise ValueError(
            f"Agent relevance answer is not strict yes/no for candidate {candidate.get('id')}: {raw_answer}"
        )
    return {
        "decision": decision,
        "raw_answer": raw_answer,
    }


def _ask_agent_sufficiency(
    agent: Any,
    user_input: str,
    relevant_candidates: List[Dict[str, Any]],
    require_strict_yes_no: bool = True,
) -> Dict[str, Any]:
    prompt = _build_sufficiency_prompt(user_input, relevant_candidates)
    raw_answer = agent.get_chat_response(prompt)
    decision = _parse_yes_no(raw_answer)
    if require_strict_yes_no and decision == "unknown":
        raise ValueError(
            f"Agent sufficiency answer is not strict yes/no: {raw_answer}"
        )
    return {
        "decision": decision,
        "raw_answer": raw_answer,
    }


def _filter_candidates_for_relevance(
    user_input: str,
    agent: Any,
    candidates: List[Dict[str, Any]],
    require_strict_yes_no: bool = True,
    include_nonrelevant: bool = False,
    show_steps: bool = False,
) -> Dict[str, Any]:
    relevant_results: List[Dict[str, Any]] = []
    nonrelevant_results: List[Dict[str, Any]] = []
    relevance_errors: List[Dict[str, Any]] = []

    ordered_candidates = _prioritize_candidates_for_intent(candidates, user_input)

    for idx, candidate in enumerate(ordered_candidates, start=1):
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
        "n_candidates": len(ordered_candidates),
        "n_relevant": len(relevant_results),
        "relevant_results": relevant_results,
        "relevance_errors": relevance_errors,
    }
    if include_nonrelevant:
        result["nonrelevant_results"] = nonrelevant_results
        result["n_nonrelevant"] = len(nonrelevant_results)
    return result


def evaluate_direct_search_relevance(
    user_input: str,
    agent: Any,
    raw_results: List[Dict[str, Any]],
    universe_name: str,
    kb_name: str,
    require_strict_yes_no: bool = True,
    include_nonrelevant: bool = False,
    show_steps: bool = False,
) -> Dict[str, Any]:
    normalized_results = _normalize_results(
        raw_results,
        universe_name=universe_name,
        kb_name=kb_name,
    )
    filtered = _filter_candidates_for_relevance(
        user_input=user_input,
        agent=agent,
        candidates=normalized_results,
        require_strict_yes_no=require_strict_yes_no,
        include_nonrelevant=include_nonrelevant,
        show_steps=show_steps,
    )
    result: Dict[str, Any] = {
        "normalized_results": normalized_results,
        "n_raw_results": len(raw_results),
        "n_relevant": filtered.get("n_relevant", 0),
        "relevant_results": filtered.get("relevant_results", []),
        "relevance_errors": filtered.get("relevance_errors", []),
    }
    if include_nonrelevant:
        result["nonrelevant_results"] = filtered.get("nonrelevant_results", [])
        result["n_nonrelevant"] = filtered.get("n_nonrelevant", 0)
    return result


def evaluate_direct_search_sufficiency(
    user_input: str,
    agent: Any,
    relevant_results: List[Dict[str, Any]],
    require_strict_yes_no: bool = True,
    max_results_to_check: int = 3,
    show_steps: bool = False,
) -> Dict[str, Any]:
    if max_results_to_check <= 0:
        raise ValueError("max_results_to_check must be > 0")

    candidates_to_check = list(relevant_results[:max_results_to_check])
    if not candidates_to_check:
        return {
            "attempted": False,
            "sufficient": False,
            "decision": "no",
            "raw_answer": "No relevant results available for sufficiency evaluation.",
            "evaluated_candidate_ids": [],
            "error": None,
        }

    try:
        decision_info = _ask_agent_sufficiency(
            agent=agent,
            user_input=user_input,
            relevant_candidates=candidates_to_check,
            require_strict_yes_no=require_strict_yes_no,
        )
        if show_steps:
            print(f"   ---> [SUFFICIENCY CHECK]: {decision_info['raw_answer']}")
        return {
            "attempted": True,
            "sufficient": decision_info["decision"] == "yes",
            "decision": decision_info["decision"],
            "raw_answer": decision_info["raw_answer"],
            "evaluated_candidate_ids": [c.get("id") for c in candidates_to_check],
            "error": None,
        }
    except Exception as e:
        if show_steps:
            print(f"   -> sufficiency error: {e}")
        return {
            "attempted": True,
            "sufficient": False,
            "decision": "unknown",
            "raw_answer": None,
            "evaluated_candidate_ids": [c.get("id") for c in candidates_to_check],
            "error": str(e),
        }


def search_direct(
    infra: Any,
    universe_name: str,
    kb_name: str,
    query: str,
    k: int = 5,
    context_window: int = 1,
) -> Dict[str, Any]:
    raw_results = _search_kb_target(
        infra=infra,
        universe_name=universe_name,
        kb_name=kb_name,
        query=query,
        k_per_kb=k,
        context_window=context_window,
    )
    normalized_results = _normalize_results(raw_results, universe_name=universe_name, kb_name=kb_name)
    return {
        "mode": "direct_search",
        "query": query,
        "results": raw_results,
        "count": len(raw_results),
        "metadata": {
            "normalized_results": normalized_results,
            "universe": universe_name,
            "kb_name": kb_name,
            "context_window": context_window,
        },
    }


def search_with_rolling_window(
    user_input: str,
    agent: Any,
    infra: Any,
    universe_name: str,
    kb_name: str,
    batch_size: int = 10,
    max_batches: int = 5,
    context_window: int = 1,
    dedupe_by: str = "id",
    require_strict_yes_no: bool = True,
    include_nonrelevant: bool = False,
    max_relevant_results: int | None = None,
    show_steps: bool = False,
) -> Dict[str, Any]:
    if batch_size <= 0:
        raise ValueError("batch_size must be > 0")
    if max_batches <= 0:
        raise ValueError("max_batches must be > 0")

    fetch_k = batch_size * max_batches
    raw_results = _search_kb_target(
        infra=infra,
        universe_name=universe_name,
        kb_name=kb_name,
        query=user_input,
        k_per_kb=fetch_k,
        context_window=context_window,
    )
    normalized = _normalize_results(raw_results, universe_name=universe_name, kb_name=kb_name)
    deduped = _dedupe_candidates(normalized, dedupe_by=dedupe_by)
    deduped = _prioritize_candidates_for_intent(deduped, user_input)

    relevant_results: List[Dict[str, Any]] = []
    nonrelevant_results: List[Dict[str, Any]] = []
    relevance_errors: List[Dict[str, Any]] = []
    batches_processed = 0

    for batch_index in range(max_batches):
        start = batch_index * batch_size
        end = start + batch_size
        batch = deduped[start:end]
        if not batch:
            break
        batches_processed += 1
        batch_eval = _filter_candidates_for_relevance(
            user_input=user_input,
            agent=agent,
            candidates=batch,
            require_strict_yes_no=require_strict_yes_no,
            include_nonrelevant=include_nonrelevant,
            show_steps=show_steps,
        )
        relevant_results.extend(batch_eval.get("relevant_results", []))
        relevance_errors.extend(batch_eval.get("relevance_errors", []))
        if include_nonrelevant:
            nonrelevant_results.extend(batch_eval.get("nonrelevant_results", []))
        if max_relevant_results is not None and len(relevant_results) >= max_relevant_results:
            relevant_results = relevant_results[:max_relevant_results]
            break

    result: Dict[str, Any] = {
        "mode": "rolling_window",
        "query": user_input,
        "results": relevant_results,
        "count": len(relevant_results),
        "metadata": {
            "universe": universe_name,
            "kb_name": kb_name,
            "batch_size": batch_size,
            "max_batches": max_batches,
            "batches_processed": batches_processed,
            "n_candidates_seen": len(deduped),
            "n_relevant": len(relevant_results),
            "search_errors": [],
            "relevance_errors": relevance_errors,
            "dedupe_by": dedupe_by,
            "context_window": context_window,
        },
    }
    if include_nonrelevant:
        result["metadata"]["nonrelevant_results"] = nonrelevant_results
        result["metadata"]["n_nonrelevant"] = len(nonrelevant_results)
    return result


def search_direct_with_auto_fallback(
    user_input: str,
    agent: Any,
    infra: Any,
    universe_name: str,
    kb_name: str,
    k: int = 5,
    context_window: int = 1,
    batch_size: int = 10,
    max_batches: int = 5,
    dedupe_by: str = "id",
    require_strict_yes_no: bool = True,
    include_nonrelevant: bool = False,
    max_relevant_results: int | None = None,
    show_steps: bool = False,
    require_direct_answer_sufficiency: bool = True,
    max_direct_results_for_sufficiency_check: int = 3,
    min_relevant_results_for_direct_accept: int | None = None,
) -> Dict[str, Any]:
    direct_result = search_direct(
        infra=infra,
        universe_name=universe_name,
        kb_name=kb_name,
        query=user_input,
        k=k,
        context_window=context_window,
    )

    raw_results = direct_result.get("results", [])
    precheck = evaluate_direct_search_relevance(
        user_input=user_input,
        agent=agent,
        raw_results=raw_results,
        universe_name=universe_name,
        kb_name=kb_name,
        require_strict_yes_no=require_strict_yes_no,
        include_nonrelevant=include_nonrelevant,
        show_steps=show_steps,
    )

    sufficiency_check: Dict[str, Any] = {
        "attempted": False,
        "sufficient": False,
        "decision": None,
        "raw_answer": None,
        "evaluated_candidate_ids": [],
        "error": None,
    }

    fallback_reason: str | None = None
    n_relevant = precheck.get("n_relevant", 0)

    if n_relevant == 0:
        fallback_reason = "direct_search_returned_no_relevant_results"
    elif min_relevant_results_for_direct_accept is not None and n_relevant < min_relevant_results_for_direct_accept:
        fallback_reason = "direct_search_too_few_relevant_results"
    elif require_direct_answer_sufficiency:
        sufficiency_check = evaluate_direct_search_sufficiency(
            user_input=user_input,
            agent=agent,
            relevant_results=precheck.get("relevant_results", []),
            require_strict_yes_no=require_strict_yes_no,
            max_results_to_check=max_direct_results_for_sufficiency_check,
            show_steps=show_steps,
        )
        if not sufficiency_check.get("sufficient", False):
            if sufficiency_check.get("error"):
                fallback_reason = "direct_search_sufficiency_check_error"
            else:
                fallback_reason = "direct_search_relevant_results_insufficient_to_answer"

    direct_metadata = dict(direct_result.get("metadata") or {})
    direct_metadata.update({
        "requested_mode": "direct_search",
        "executed_mode": "direct_search",
        "auto_fallback_attempted": False,
        "fallback_from": None,
        "fallback_reason": None,
        "direct_precheck": {
            "n_raw_results": precheck.get("n_raw_results", 0),
            "n_relevant": n_relevant,
            "relevance_errors": precheck.get("relevance_errors", []),
            "relevant_candidate_ids": [r.get("id") for r in precheck.get("relevant_results", [])],
        },
        "direct_sufficiency_check": sufficiency_check,
    })
    if include_nonrelevant:
        direct_metadata["direct_precheck"]["n_nonrelevant"] = precheck.get("n_nonrelevant", 0)

    if fallback_reason is None:
        direct_result["metadata"] = direct_metadata
        return direct_result

    rolling_result = search_with_rolling_window(
        user_input=user_input,
        agent=agent,
        infra=infra,
        universe_name=universe_name,
        kb_name=kb_name,
        batch_size=batch_size,
        max_batches=max_batches,
        context_window=context_window,
        dedupe_by=dedupe_by,
        require_strict_yes_no=require_strict_yes_no,
        include_nonrelevant=include_nonrelevant,
        max_relevant_results=max_relevant_results,
        show_steps=show_steps,
    )
    rolling_metadata = dict(rolling_result.get("metadata") or {})
    rolling_metadata.update({
        "requested_mode": "direct_search",
        "executed_mode": "rolling_window",
        "auto_fallback_attempted": True,
        "fallback_from": "direct_search",
        "fallback_reason": fallback_reason,
        "direct_precheck": {
            "n_raw_results": precheck.get("n_raw_results", 0),
            "n_relevant": n_relevant,
            "relevance_errors": precheck.get("relevance_errors", []),
            "relevant_candidate_ids": [r.get("id") for r in precheck.get("relevant_results", [])],
        },
        "direct_sufficiency_check": sufficiency_check,
    })
    if include_nonrelevant:
        rolling_metadata["direct_precheck"]["n_nonrelevant"] = precheck.get("n_nonrelevant", 0)
    rolling_result["metadata"] = rolling_metadata
    return rolling_result


def _build_internal_questions_prompt(query: str, max_internal_questions: int = 3) -> str:
    intent = _detect_query_intent(query)
    extra_instruction = ""
    if intent.get("is_visual"):
        extra_instruction = (
            "The user appears to be asking specifically about an image, figure, picture, photo, or diagram. "
            "Generate internal questions that help locate extracted images and their nearby identifying context, not just generic topical text."
        )
    elif intent.get("is_table"):
        extra_instruction = (
            "The user appears to be asking specifically about a table. "
            "Generate internal questions that help locate extracted tables and their nearby identifying context, not just generic topical text."
        )

    return f"""Generate up to {max_internal_questions} short internal research questions that would help answer the user query below.
Return only the questions, one per line.
Do not number them.
Do not add explanations.

{extra_instruction}

<User Query>
{query}
</User Query>
"""


def _parse_internal_questions(agent_output: Any, max_internal_questions: int = 3) -> List[str]:
    questions: List[str] = []
    for raw_line in str(agent_output or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        while line[:2] in {"- ", "* "}:
            line = line[2:].strip()
        if line and line[0].isdigit() and "." in line:
            prefix, suffix = line.split(".", 1)
            if prefix.strip().isdigit():
                line = suffix.strip()
        if line:
            questions.append(line)
        if len(questions) >= max_internal_questions:
            break
    return questions


def _ask_agent_internal_questions(agent: Any, query: str, max_internal_questions: int = 3) -> Dict[str, Any]:
    prompt = _build_internal_questions_prompt(query, max_internal_questions=max_internal_questions)
    raw_answer = agent.get_chat_response(prompt)
    questions = _parse_internal_questions(raw_answer, max_internal_questions=max_internal_questions)
    if not questions:
        raise ValueError(f"Agent did not return parseable internal questions: {raw_answer}")
    return {
        "questions": questions,
        "raw_answer": raw_answer,
    }


def search_with_agentic_internal_questions(
    user_input: str,
    agent: Any,
    infra: Any,
    universe_name: str,
    kb_name: str,
    k_per_internal_question: int = 5,
    context_window: int = 1,
    max_internal_questions: int = 3,
    dedupe_by: str = "id",
    require_strict_yes_no: bool = True,
    include_nonrelevant: bool = False,
    max_relevant_results: int | None = None,
    show_steps: bool = False,
) -> Dict[str, Any]:
    if k_per_internal_question <= 0:
        raise ValueError("k_per_internal_question must be > 0")
    if max_internal_questions <= 0:
        raise ValueError("max_internal_questions must be > 0")

    internal_question_info = _ask_agent_internal_questions(
        agent=agent,
        query=user_input,
        max_internal_questions=max_internal_questions,
    )
    internal_questions = internal_question_info["questions"]

    all_candidates: List[Dict[str, Any]] = []
    per_question_results: List[Dict[str, Any]] = []
    search_errors: List[Dict[str, Any]] = []

    for question in internal_questions:
        try:
            raw_results = _search_kb_target(
                infra=infra,
                universe_name=universe_name,
                kb_name=kb_name,
                query=question,
                k_per_kb=k_per_internal_question,
                context_window=context_window,
            )
            normalized_results = _normalize_results(raw_results, universe_name=universe_name, kb_name=kb_name)
            all_candidates.extend(normalized_results)
            per_question_results.append({
                "question": question,
                "raw_result_count": len(raw_results),
                "normalized_result_count": len(normalized_results),
            })
        except Exception as e:
            search_errors.append({
                "question": question,
                "error": str(e),
            })
            if show_steps:
                print(f"   -> internal-question search error for '{question}': {e}")

    deduped_candidates = _dedupe_candidates(all_candidates, dedupe_by=dedupe_by)
    deduped_candidates = _prioritize_candidates_for_intent(deduped_candidates, user_input)
    filtered = _filter_candidates_for_relevance(
        user_input=user_input,
        agent=agent,
        candidates=deduped_candidates,
        require_strict_yes_no=require_strict_yes_no,
        include_nonrelevant=include_nonrelevant,
        show_steps=show_steps,
    )

    relevant_results = filtered.get("relevant_results", [])
    if max_relevant_results is not None:
        relevant_results = relevant_results[:max_relevant_results]

    result: Dict[str, Any] = {
        "mode": "agentic_internal_questions",
        "query": user_input,
        "results": relevant_results,
        "count": len(relevant_results),
        "internal_questions": internal_questions,
        "metadata": {
            "universe": universe_name,
            "kb_name": kb_name,
            "k_per_internal_question": k_per_internal_question,
            "max_internal_questions": max_internal_questions,
            "n_candidates_seen": len(deduped_candidates),
            "n_relevant": len(relevant_results),
            "search_errors": search_errors,
            "relevance_errors": filtered.get("relevance_errors", []),
            "per_question_results": per_question_results,
            "dedupe_by": dedupe_by,
            "context_window": context_window,
            "agent_internal_questions_raw_answer": internal_question_info.get("raw_answer"),
        },
    }
    if include_nonrelevant:
        result["metadata"]["nonrelevant_results"] = filtered.get("nonrelevant_results", [])
        result["metadata"]["n_nonrelevant"] = filtered.get("n_nonrelevant", 0)
    return result


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
            all_candidates.extend(
                _normalize_results(raw_results, universe_name=universe_name, kb_name=kb_name)
            )
        except Exception as e:
            search_errors.append({
                "target": target,
                "error": str(e),
            })
            if show_steps:
                print(f"   -> search error: {e}")

    deduped_candidates = _dedupe_candidates(all_candidates, dedupe_by=dedupe_by)
    deduped_candidates = _prioritize_candidates_for_intent(deduped_candidates, user_input)
    truncated_candidates = deduped_candidates[:max_candidates]

    filtered = _filter_candidates_for_relevance(
        user_input=user_input,
        agent=agent,
        candidates=truncated_candidates,
        require_strict_yes_no=require_strict_yes_no,
        include_nonrelevant=include_nonrelevant,
        show_steps=show_steps,
    )

    result: Dict[str, Any] = {
        "query": user_input,
        "targets": targets,
        "n_targets": len(targets),
        "n_candidates": len(truncated_candidates),
        "n_relevant": len(filtered.get("relevant_results", [])),
        "relevant_results": filtered.get("relevant_results", []),
        "search_errors": search_errors,
        "relevance_errors": filtered.get("relevance_errors", []),
    }

    if include_nonrelevant:
        result["nonrelevant_results"] = filtered.get("nonrelevant_results", [])
        result["n_nonrelevant"] = filtered.get("n_nonrelevant", 0)

    return result
