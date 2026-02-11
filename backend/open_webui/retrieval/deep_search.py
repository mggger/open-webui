import asyncio
import json
import logging
import math
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from fastapi import Request

from open_webui.env import SRC_LOG_LEVELS
from open_webui.routers.pipelines import process_pipeline_inlet_filter
from open_webui.retrieval.web.serpapi import search_serpapi
from open_webui.utils.chat import generate_chat_completion

log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["RAG"])

MAX_CONTENT_CHARS = 25_000
MAX_ITERATIVE_ROUNDS = 3


def _preview(text: str, max_chars: int = 160) -> str:
    text = (text or "").replace("\n", " ").strip()
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars]}..."


def _json_preview(value: Any, max_chars: int = 400) -> str:
    try:
        rendered = json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        rendered = str(value)
    return _preview(rendered, max_chars=max_chars)


def _trim_text(text: str, max_chars: int = MAX_CONTENT_CHARS) -> str:
    if not text:
        return ""
    if len(text) <= max_chars:
        return text
    return text[:max_chars]


def _extract_json(content: str) -> Optional[Dict[str, Any]]:
    if not content:
        return None
    start = content.find("{")
    end = content.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(content[start : end + 1])
    except json.JSONDecodeError:
        return None


def _normalize_content(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: List[str] = []
        for item in value:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str) and text.strip():
                    parts.append(text)
            elif isinstance(item, str) and item.strip():
                parts.append(item)
        return "\n".join(parts).strip()
    return str(value)


def _resolve_system_prompt(messages: Optional[List[Dict[str, Any]]]) -> Optional[str]:
    if not messages:
        return None

    # Preserve explicit system intent from chat context for deep search steps.
    system_messages = [
        _normalize_content(m.get("content"))
        for m in messages
        if isinstance(m, dict) and (m.get("role") or "").lower() == "system"
    ]
    system_messages = [m.strip() for m in system_messages if m and m.strip()]
    if not system_messages:
        return None

    return "\n\n".join(system_messages)


def _build_answer_prompt(
    prompt: str,
    learnings: List[str],
    context_items: List[Dict[str, str]],
    requirements: Dict[str, Any],
) -> str:
    learnings_section = "\n".join(f"<learning>\n{l}\n</learning>" for l in learnings)
    context_section = "\n".join(
        f'<item index="{idx + 1}">\n<title>{(s.get("title") or "").strip()}</title>\n'
        f'<description>{(s.get("description") or "").strip()}</description>\n'
        f'<url>{(s.get("url") or "").strip()}</url>\n</item>'
        for idx, s in enumerate(context_items)
        if (s.get("url") or "").strip()
    )
    if not context_section:
        context_section = "<item>\n<url>Not available</url>\n</item>"
    requirement_section = (
        f"<wants_table>{requirements.get('wants_table')}</wants_table>\n"
        f"<table_only>{requirements.get('table_only')}</table_only>\n"
        f"<time_window_hint>{requirements.get('time_window_hint')}</time_window_hint>\n"
        f"<min_valid_urls>{requirements.get('min_valid_urls')}</min_valid_urls>"
    )
    return (
        "Given the following prompt from the user, provide a direct final answer that matches user intent. "
        "Use the research learnings and structured context data provided below.\n"
        "Prefer clear, concise structure. If the user asks for a table, return a table. "
        "If the user asks for a short summary, keep it short.\n"
        "When you provide source URLs, ONLY use URLs from context_data.\n"
        "You MUST NOT output 'Not available' for Source URL when context_data has valid URLs.\n"
        "If table rows are produced, each row must include one exact Source URL from context_data.\n\n"
        f"<prompt>{prompt}</prompt>\n\n"
        "<requirements>\n"
        + requirement_section
        + "\n</requirements>\n\n"
        "<learnings>\n"
        + learnings_section
        + "\n</learnings>"
        + "\n\n<context_data>\n"
        + context_section
        + "\n</context_data>"
    )


def _inject_today_context(query: str) -> str:
    now_utc = datetime.now(timezone.utc)
    date_context = (
        f"Current date (UTC): {now_utc.strftime('%Y-%m-%d')}.\n"
        f"Current timestamp (UTC): {now_utc.isoformat()}."
    )
    return f"{date_context}\n\nUser request:\n{query}"


def _dedupe_strings(values: List[str]) -> List[str]:
    return list(dict.fromkeys([v for v in values if isinstance(v, str) and v.strip()]))


def _derive_search_requirements(query: str) -> Dict[str, Any]:
    query_l = (query or "").lower()
    wants_table = any(token in query_l for token in ["table", "tabular", "markdown table"])
    table_only = any(token in query_l for token in ["just the table", "only table", "table only"])
    time_window_hint = "last 72 hours" if "72 hours" in query_l else ""
    min_valid_urls = 4 if wants_table else 2
    return {
        "wants_table": wants_table,
        "table_only": table_only,
        "time_window_hint": time_window_hint,
        "min_valid_urls": min_valid_urls,
    }


def _build_context_items(sources: List[Dict[str, str]]) -> List[Dict[str, str]]:
    items: List[Dict[str, str]] = []
    seen = set()
    for source in sources:
        url = (source.get("url") or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        items.append(
            {
                "title": (source.get("title") or "").strip(),
                "description": (source.get("snippet") or "").strip(),
                "url": url,
            }
        )
    return items


async def _assess_source_quality(
    request: Request,
    user: Any,
    model_id: str,
    original_query: str,
    active_query: str,
    context_items: List[Dict[str, str]],
    requirements: Dict[str, Any],
    iteration: int,
    max_iterations: int,
    system_prompt: Optional[str] = None,
) -> Dict[str, Any]:
    min_valid_urls = int(requirements.get("min_valid_urls", 2))
    valid_context_items = [
        s
        for s in context_items
        if (s.get("url") or "").strip()
        and "example.com" not in (s.get("url") or "").lower()
        and "localhost" not in (s.get("url") or "").lower()
    ]

    sources_section = (
        "\n".join(
            f"- {s.get('title') or '(untitled)'} | {s.get('url') or ''} | {s.get('description') or ''}"
            for s in context_items[:40]
        )
        or "- (none)"
    )
    prompt = (
        "You are evaluating source quality for iterative web research.\n"
        f"Original user request:\n<original_query>{original_query}</original_query>\n\n"
        f"Current active search query:\n<active_query>{active_query}</active_query>\n\n"
        f"Current iteration: {iteration}/{max_iterations}\n\n"
        f"Required minimum valid URLs: {min_valid_urls}\n"
        f"User expects table: {requirements.get('wants_table')}\n"
        f"Time window hint: {requirements.get('time_window_hint')}\n\n"
        "Candidate sources:\n"
        f"{sources_section}\n\n"
        "Decide whether the current source URLs are sufficiently accurate and relevant to answer the original request.\n"
        "Source quality criteria:\n"
        "- URL should look like a real source page (not placeholders like example.com).\n"
        "- Title/snippet should be topically relevant to the original request.\n"
        "- The source set should provide enough coverage to support answer generation.\n"
        "If source quality is insufficient, propose one refined next query to improve source accuracy.\n"
        "When strict filters under-return results, broaden carefully (synonyms, related terms, nearby time window) while preserving intent.\n"
        "Return JSON only in this format:\n"
        '{"sufficient":true|false,"reason":"...","refinedQuery":"..."}'
    )

    _, parsed = await _call_llm_json(
        request, user, model_id, prompt, system_prompt=system_prompt
    )
    if parsed and isinstance(parsed, dict):
        sufficient = bool(parsed.get("sufficient"))
        refined_query = _normalize_content(parsed.get("refinedQuery")).strip()
        reason = _normalize_content(parsed.get("reason")).strip()
        return {
            "sufficient": sufficient,
            "refined_query": refined_query,
            "reason": reason,
        }

    # Conservative fallback if model does not return JSON
    if len(valid_context_items) < min_valid_urls and iteration < max_iterations:
        return {
            "sufficient": False,
            "refined_query": (
                f"{original_query}\nExpand with synonyms, related entities/terms, and adjacent time window."
            ),
            "reason": "Insufficient reliable source URLs in current round.",
        }
    return {"sufficient": True, "refined_query": "", "reason": "Fallback: source set is acceptable."}


async def _assess_answer_requirements(
    request: Request,
    user: Any,
    model_id: str,
    original_query: str,
    candidate_answer: str,
    context_items: List[Dict[str, str]],
    requirements: Dict[str, Any],
    iteration: int,
    max_iterations: int,
    system_prompt: Optional[str] = None,
) -> Dict[str, Any]:
    context_section = (
        "\n".join(
            f"- {s.get('title') or '(untitled)'} | {s.get('url') or ''} | {s.get('description') or ''}"
            for s in context_items[:40]
        )
        or "- (none)"
    )
    prompt = (
        "Evaluate whether the candidate answer satisfies the user's request.\n"
        f"Round: {iteration}/{max_iterations}\n"
        f"Original request:\n<request>{original_query}</request>\n\n"
        "Derived requirements:\n"
        f"- wants_table: {requirements.get('wants_table')}\n"
        f"- table_only: {requirements.get('table_only')}\n"
        f"- min_valid_urls: {requirements.get('min_valid_urls')}\n"
        f"- time_window_hint: {requirements.get('time_window_hint')}\n\n"
        f"Context items:\n{context_section}\n\n"
        f"Candidate answer:\n<candidate_answer>{candidate_answer}</candidate_answer>\n\n"
        "Check: format compliance, relevance to request, and Source URL quality.\n"
        "Mark unsatisfied if Source URL is missing, 'Not available', or 'Not disclosed' while valid context URLs exist.\n"
        "If unsatisfied, provide concise feedback and a refined query for next round.\n"
        "Return JSON only:\n"
        '{"satisfied":true|false,"feedback":"...","refinedQuery":"..."}'
    )
    _, parsed = await _call_llm_json(
        request, user, model_id, prompt, system_prompt=system_prompt
    )
    if parsed and isinstance(parsed, dict):
        return {
            "satisfied": bool(parsed.get("satisfied")),
            "feedback": _normalize_content(parsed.get("feedback")).strip(),
            "refined_query": _normalize_content(parsed.get("refinedQuery")).strip(),
        }

    # Conservative fallback
    answer_l = (candidate_answer or "").lower()
    invalid_url_tokens = ["not available", "not disclosed", "| n/a |", "source url: n/a"]
    has_invalid_url_marker = any(token in answer_l for token in invalid_url_tokens)
    return {
        "satisfied": not has_invalid_url_marker,
        "feedback": "Source URL quality is insufficient; include concrete URLs from evidence context.",
        "refined_query": (
            f"{original_query}\nPrioritize incident reports that include direct article URLs and verifiable source pages."
        ),
    }


async def _call_llm_json(
    request: Request,
    user: Any,
    model_id: str,
    prompt: str,
    system_prompt: Optional[str] = None,
    overrides: Optional[Dict[str, Any]] = None,
) -> Tuple[str, Optional[Dict[str, Any]]]:
    log.debug(
        "deep_search.llm.request model=%s prompt_len=%s prompt_preview=%r",
        model_id,
        len(prompt or ""),
        _preview(prompt),
    )
    llm_messages: List[Dict[str, str]] = []
    if system_prompt and system_prompt.strip():
        llm_messages.append({"role": "system", "content": system_prompt})
    llm_messages.append({"role": "user", "content": prompt})

    payload = {
        "model": model_id,
        "messages": llm_messages,
        "stream": False,
        "temperature": 0,
        "metadata": {"task": "deep_search"},
    }
    if overrides:
        payload.update(overrides)

    models = request.app.state.MODELS
    payload = await process_pipeline_inlet_filter(request, payload, user, models)
    res = await generate_chat_completion(request, form_data=payload, user=user)
    status_code = getattr(res, "status_code", 200)
    if hasattr(res, "body"):
        try:
            res = json.loads(res.body.decode("utf-8"))
        except Exception:
            res = {}
    if isinstance(res, dict):
        log.debug(
            "deep_search.llm.response status=%s keys=%s",
            status_code,
            sorted(list(res.keys())),
        )
    else:
        log.debug(
            "deep_search.llm.response status=%s type=%s",
            status_code,
            type(res).__name__,
        )
    if status_code and int(status_code) >= 400:
        detail = None
        if isinstance(res, dict):
            detail = res.get("detail") or res.get("error")
        if not detail:
            detail = f"HTTP {status_code}"
        log.warning("deep_search.llm.error status=%s detail=%r", status_code, detail)
        raise RuntimeError(f"Deep search model call failed: {detail}")
    if isinstance(res, dict) and "choices" not in res:
        detail = res.get("detail") or res.get("error")
        if detail:
            log.warning(
                "deep_search.llm.invalid_response status=%s detail=%r keys=%s",
                status_code,
                detail,
                sorted(list(res.keys())),
            )
            raise RuntimeError(f"Deep search model response invalid: {detail}")
    choices = res.get("choices", []) if isinstance(res, dict) else []
    first_choice = choices[0] if isinstance(choices, list) and choices else {}
    message = first_choice.get("message", {}) if isinstance(first_choice, dict) else {}
    if not isinstance(message, dict):
        message = {}
    message_content = message.get("content") or ""
    content = _normalize_content(message_content)
    if not content.strip():
        log.warning(
            "deep_search.llm.empty_content status=%s choices=%s finish_reason=%r message_type=%s message_keys=%s first_choice_preview=%r response_preview=%r",
            status_code,
            len(choices) if isinstance(choices, list) else 0,
            first_choice.get("finish_reason") if isinstance(first_choice, dict) else None,
            type(message_content).__name__,
            sorted(list(message.keys())) if isinstance(message, dict) else [],
            _json_preview(first_choice),
            _json_preview(res),
        )
    parsed = _extract_json(content)
    log.debug(
        "deep_search.llm.parsed content_len=%s parsed=%s",
        len(content or ""),
        parsed is not None,
    )
    return content, parsed


async def _generate_serp_queries(
    request: Request,
    user: Any,
    model_id: str,
    query: str,
    breadth: int,
    system_prompt: Optional[str] = None,
    learnings: Optional[List[str]] = None,
) -> List[Dict[str, str]]:
    log.debug(
        "deep_search.generate_queries breadth=%s previous_learnings=%s query_preview=%r",
        breadth,
        len(learnings or []),
        _preview(query),
    )
    learnings_section = ""
    if learnings:
        learnings_section = (
            "\nHere are some learnings from previous research, use them to generate more specific queries:\n"
            + "\n".join(learnings)
        )

    prompt = (
        "Given the following prompt from the user, generate a list of SERP queries to research the topic. "
        f"Return a maximum of {breadth} queries, but feel free to return less if the original prompt is clear. "
        "Make sure each query is unique and not similar to each other. "
        "Return your response as JSON in the form:\n"
        '{"queries":[{"query":"...","researchGoal":"..."}]}\n\n'
        f"<prompt>{query}</prompt>{learnings_section}"
    )

    _, parsed = await _call_llm_json(
        request, user, model_id, prompt, system_prompt=system_prompt
    )
    if parsed and isinstance(parsed.get("queries"), list):
        queries = [
            q
            for q in parsed.get("queries", [])
            if isinstance(q, dict) and q.get("query")
        ]
        if queries:
            log.debug("deep_search.generate_queries.generated count=%s", len(queries[:breadth]))
            return queries[:breadth]
    log.debug("deep_search.generate_queries.fallback_single_query")
    return [{"query": query, "researchGoal": "Explore the topic in depth."}]


async def _extract_learnings(
    request: Request,
    user: Any,
    model_id: str,
    query: str,
    contents: List[str],
    num_learnings: int,
    num_followups: int,
    system_prompt: Optional[str] = None,
) -> Tuple[List[str], List[str]]:
    log.debug(
        "deep_search.extract_learnings query_preview=%r contents=%s num_learnings=%s num_followups=%s",
        _preview(query),
        len(contents or []),
        num_learnings,
        num_followups,
    )
    trimmed_contents = [_trim_text(content) for content in contents if content]
    prompt = (
        "Given the following contents from a SERP search for the query "
        f"<query>{query}</query>, generate a list of learnings from the contents. "
        f"Return a maximum of {num_learnings} learnings, but feel free to return less if the contents are clear. "
        "Make sure each learning is unique and not similar to each other. The learnings should be concise and "
        "to the point, as detailed and information dense as possible. Include entities, metrics, numbers, and dates. "
        "Also generate follow-up questions to research the topic further. "
        "Return JSON in the form:\n"
        '{"learnings":["..."],"followUpQuestions":["..."]}\n\n'
        "<contents>\n"
        + "\n".join(f"<content>\n{content}\n</content>" for content in trimmed_contents)
        + "\n</contents>"
    )

    _, parsed = await _call_llm_json(
        request, user, model_id, prompt, system_prompt=system_prompt
    )
    learnings = parsed.get("learnings", []) if parsed else []
    followups = parsed.get("followUpQuestions", []) if parsed else []

    learnings = [l for l in learnings if isinstance(l, str) and l.strip()]
    followups = [q for q in followups if isinstance(q, str) and q.strip()]
    log.debug(
        "deep_search.extract_learnings.result learnings=%s followups=%s",
        len(learnings),
        len(followups),
    )

    return learnings[:num_learnings], followups[:num_followups]


def _inject_citations(report: Optional[str], sources: List[Dict[str, str]]) -> str:
    report = (report or "").strip()
    if not report or not sources:
        return report

    blocks = [b.strip() for b in report.split("\n\n") if b.strip()]
    if not blocks:
        return report

    def is_eligible(block: str) -> bool:
        if block.startswith("#") or block.startswith("|"):
            return False
        hr_tokens = {"---", "***", "___"}
        if block.strip() in hr_tokens:
            return False
        if block.startswith("```"):
            return False
        return True

    eligible_indices = [i for i, block in enumerate(blocks) if is_eligible(block)]
    if not eligible_indices:
        eligible_indices = list(range(len(blocks)))

    for idx, _source in enumerate(sources):
        block_idx = eligible_indices[idx % len(eligible_indices)]
        blocks[block_idx] = f"{blocks[block_idx]} [{idx + 1}]"

    return "\n\n".join(blocks)


def _build_table_answer_from_context(context_items: List[Dict[str, str]]) -> str:
    if not context_items:
        return "No publicly reported cyber incidents were identified globally in the past three days."

    header = (
        "| Date of Incident | Breach Type | Affected Organization / Entity | Description | Source URL |\n"
        "|---|---|---|---|---|"
    )
    rows = []
    for item in context_items[:12]:
        description = (item.get("description") or "Details remain limited").replace("\n", " ").strip()
        if len(description) > 220:
            description = f"{description[:217]}..."
        title = (item.get("title") or "Undisclosed organization").replace("\n", " ").strip()
        url = (item.get("url") or "Not available").strip()
        rows.append(
            f"| Recent disclosure | Not specified | {title} | {description} | {url} |"
        )
    return "\n".join([header, *rows])


async def _generate_final_answer(
    request: Request,
    user: Any,
    model_id: str,
    prompt: str,
    learnings: List[str],
    sources: List[Dict[str, str]],
    context_items: List[Dict[str, str]],
    requirements: Dict[str, Any],
    system_prompt: Optional[str] = None,
) -> str:
    log.debug(
        "deep_search.answer.start model=%s learnings=%s sources=%s prompt_preview=%r",
        model_id,
        len(learnings or []),
        len(sources or []),
        _preview(prompt),
    )
    answer_prompt = _build_answer_prompt(prompt, learnings, context_items, requirements) + (
        '\n\nReturn JSON in the form:\n{"answer":"..."}'
    )

    raw, parsed = await _call_llm_json(
        request,
        user,
        model_id,
        answer_prompt,
        system_prompt=system_prompt,
        overrides={
            "max_completion_tokens": 8192,
        },
    )
    answer = parsed.get("answer") if parsed else raw
    answer = _normalize_content(answer)
    if not isinstance(answer, str) or not answer.strip():
        answer = _normalize_content(raw)
    log.debug(
        "deep_search.answer.result raw_len=%s answer_len=%s parsed=%s",
        len(raw or ""),
        len(answer or ""),
        parsed is not None,
    )

    failure_text = "No publicly reported cyber incidents were identified globally in the past three days."
    if answer.strip() == failure_text and context_items:
        answer = _build_table_answer_from_context(context_items)
    return _inject_citations(answer, sources)


def _build_result_snippets(results) -> List[str]:
    snippets = []
    for result in results:
        title = result.title or ""
        snippet = result.snippet or ""
        link = result.link or ""
        if not (title or snippet or link):
            continue
        snippets.append(
            "\n".join(
                [
                    f"Title: {title}" if title else "",
                    f"Snippet: {snippet}" if snippet else "",
                    f"URL: {link}" if link else "",
                ]
            ).strip()
        )
    return snippets


def _dedupe_sources(sources: List[Dict[str, str]]) -> List[Dict[str, str]]:
    seen = set()
    deduped = []
    for source in sources:
        url = source.get("url")
        if not url or url in seen:
            continue
        seen.add(url)
        deduped.append(source)
    return deduped


async def run_deep_search(
    request: Request,
    user: Any,
    model_id: str,
    query: str,
    depth: int,
    breadth: int,
    result_count: int,
    concurrency: int,
    system_prompt: Optional[str] = None,
    learnings: Optional[List[str]] = None,
    visited_urls: Optional[List[str]] = None,
    sources: Optional[List[Dict[str, str]]] = None,
    on_progress: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
) -> Dict[str, List[str]]:
    if learnings is None:
        learnings = []
    if visited_urls is None:
        visited_urls = []
    if sources is None:
        sources = []

    depth = max(1, depth)
    breadth = max(1, breadth)
    log.info(
        "deep_search.run.start model=%s depth=%s breadth=%s result_count=%s concurrency=%s seed_learnings=%s",
        model_id,
        depth,
        breadth,
        result_count,
        concurrency,
        len(learnings),
    )

    if on_progress:
        await on_progress(
            {
                "type": "status",
                "data": {
                    "action": "deep_search",
                    "description": "Generating search query",
                    "done": False,
                },
            }
        )

    serp_queries = await _generate_serp_queries(
        request,
        user,
        model_id,
        query,
        breadth,
        system_prompt=system_prompt,
        learnings=learnings,
    )
    log.info(
        "deep_search.run.queries_generated count=%s queries=%s",
        len(serp_queries),
        [q.get("query", "") for q in serp_queries],
    )

    if on_progress:
        await on_progress(
            {
                "type": "status",
                "data": {
                    "action": "deep_search_queries_generated",
                    "queries": [q.get("query") for q in serp_queries if q.get("query")],
                    "done": False,
                },
            }
        )

    sem = asyncio.Semaphore(max(1, concurrency))

    async def handle_query(serp_query: Dict[str, str]) -> Dict[str, List[str]]:
        async with sem:
            try:
                log.info(
                    "deep_search.query.start depth=%s query=%r",
                    depth,
                    serp_query.get("query", query),
                )
                if on_progress:
                    await on_progress(
                        {
                            "type": "status",
                            "data": {
                                "action": "deep_search",
                                "description": 'Searching "{{searchQuery}}"',
                                "query": serp_query.get("query", query),
                                "done": False,
                            },
                        }
                    )

                search_results = search_serpapi(
                    request.app.state.config.SERPAPI_API_KEY,
                    request.app.state.config.SERPAPI_ENGINE,
                    serp_query.get("query", query),
                    result_count,
                    filter_list=request.app.state.config.WEB_SEARCH_DOMAIN_FILTER_LIST,
                )
                log.info(
                    "deep_search.query.search_results count=%s query=%r",
                    len(search_results),
                    serp_query.get("query", query),
                )
                urls = [result.link for result in search_results if result.link]
                new_sources = [
                    {
                        "title": result.title or result.link or "",
                        "url": result.link or "",
                        "snippet": result.snippet or "",
                    }
                    for result in search_results
                    if result.link
                ]
                all_sources = _dedupe_sources(sources + new_sources)
                if on_progress and urls:
                    await on_progress(
                        {
                            "type": "status",
                            "data": {
                                "action": "deep_search",
                                "description": "Searched {{count}} sites",
                                "urls": urls,
                                "done": True,
                            },
                        }
                    )
                contents = _build_result_snippets(search_results)

                num_followups = max(1, math.ceil(breadth / 2))
                new_learnings, followups = await _extract_learnings(
                    request,
                    user,
                    model_id,
                    serp_query.get("query", query),
                    contents,
                    num_learnings=3,
                    num_followups=num_followups,
                    system_prompt=system_prompt,
                )
                log.info(
                    "deep_search.query.learnings learnings=%s followups=%s urls=%s",
                    len(new_learnings),
                    len(followups),
                    len(urls),
                )

                combined_learnings = learnings + new_learnings
                combined_urls = visited_urls + urls

                if depth > 1 and followups:
                    next_query = (
                        "Previous research goal: "
                        + serp_query.get("researchGoal", "")
                        + "\nFollow-up research directions:\n"
                        + "\n".join(followups)
                    ).strip()
                    return await run_deep_search(
                        request,
                        user,
                        model_id,
                        next_query,
                        depth - 1,
                        max(1, math.ceil(breadth / 2)),
                        result_count,
                        concurrency,
                        system_prompt=system_prompt,
                        learnings=combined_learnings,
                        visited_urls=combined_urls,
                        sources=all_sources,
                        on_progress=on_progress,
                    )

                log.info(
                    "deep_search.query.done total_learnings=%s total_sources=%s",
                    len(combined_learnings),
                    len(all_sources),
                )
                return {
                    "learnings": combined_learnings,
                    "visited_urls": combined_urls,
                    "sources": all_sources,
                }
            except Exception as exc:
                log.exception("Deep search query failed: %s", exc)
                if on_progress:
                    await on_progress(
                        {
                            "type": "status",
                            "data": {
                                "action": "deep_search",
                                "description": "An error occurred while searching the web",
                                "done": True,
                                "error": True,
                            },
                        }
                    )
                return {
                    "learnings": learnings,
                    "visited_urls": visited_urls,
                    "sources": sources,
                }

    results = await asyncio.gather(*(handle_query(q) for q in serp_queries))

    all_learnings = []
    all_urls = []
    all_sources = []
    for result in results:
        all_learnings.extend(result.get("learnings", []))
        all_urls.extend(result.get("visited_urls", []))
        all_sources.extend(result.get("sources", []))

    # Deduplicate while preserving order
    dedup_learnings = list(dict.fromkeys(all_learnings))
    dedup_urls = list(dict.fromkeys(all_urls))
    dedup_sources = _dedupe_sources(all_sources)
    log.info(
        "deep_search.run.done learnings=%s urls=%s sources=%s",
        len(dedup_learnings),
        len(dedup_urls),
        len(dedup_sources),
    )

    return {
        "learnings": dedup_learnings,
        "visited_urls": dedup_urls,
        "sources": dedup_sources,
    }


async def generate_deep_search_report(
    request: Request,
    user: Any,
    model_id: str,
    query: str,
    depth: int,
    breadth: int,
    result_count: int,
    concurrency: int,
    messages: Optional[List[Dict[str, Any]]] = None,
    system_prompt: Optional[str] = None,
    max_iterations: Optional[int] = None,
    on_progress: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
) -> Dict[str, Any]:
    log.info(
        "deep_search.report_flow.start model=%s depth=%s breadth=%s result_count=%s concurrency=%s messages=%s query_preview=%r",
        model_id,
        depth,
        breadth,
        result_count,
        concurrency,
        len(messages or []),
        _preview(query),
    )
    full_query = _inject_today_context(query)
    resolved_system_prompt = (system_prompt or "").strip() or _resolve_system_prompt(messages)
    if messages:
        conversation = "\n".join(
            f"{(m.get('role') or 'user').upper()}: {m.get('content')}"
            for m in messages
            if m.get("content")
        )
        full_query = (
            f"{_inject_today_context(query)}\n\n"
            "Conversation context (for query refinement):\n"
            f"{conversation}"
        )

    configured_max_iterations = max_iterations or MAX_ITERATIVE_ROUNDS
    max_iterations = max(1, min(configured_max_iterations, depth))
    per_round_depth = max(1, math.ceil(depth / max_iterations))
    requirements = _derive_search_requirements(query)
    current_query = full_query
    all_learnings: List[str] = []
    all_sources: List[Dict[str, str]] = []
    final_answer = ""
    context_sufficient = False

    for iteration in range(1, max_iterations + 1):
        if on_progress:
            await on_progress(
                {
                    "type": "status",
                    "data": {
                        "action": "deep_search",
                        "description": f"Iterative research round {iteration}/{max_iterations}",
                        "done": False,
                    },
                }
            )

        round_result = await run_deep_search(
            request=request,
            user=user,
            model_id=model_id,
            query=current_query,
            depth=per_round_depth,
            breadth=breadth,
            result_count=result_count,
            concurrency=concurrency,
            system_prompt=resolved_system_prompt,
            on_progress=on_progress,
        )
        all_learnings = _dedupe_strings(all_learnings + round_result.get("learnings", []))
        all_sources = _dedupe_sources(all_sources + round_result.get("sources", []))
        context_items = _build_context_items(all_sources)
        log.info(
            "deep_search.report_flow.round_complete round=%s/%s learnings=%s sources=%s",
            iteration,
            max_iterations,
            len(all_learnings),
            len(all_sources),
        )

        source_assessment = await _assess_source_quality(
            request=request,
            user=user,
            model_id=model_id,
            original_query=query,
            active_query=current_query,
            context_items=context_items,
            requirements=requirements,
            iteration=iteration,
            max_iterations=max_iterations,
            system_prompt=resolved_system_prompt,
        )
        log.info(
            "deep_search.report_flow.context_assessment round=%s sufficient=%s refined_query_preview=%r reason=%r",
            iteration,
            source_assessment.get("sufficient"),
            _preview(source_assessment.get("refined_query", "")),
            _preview(source_assessment.get("reason", "")),
        )
        feedback = (source_assessment.get("reason") or "").strip()
        if on_progress and feedback:
            await on_progress(
                {
                    "type": "status",
                    "data": {
                        "action": "deep_search",
                        "description": f"Round {iteration} feedback: {feedback}",
                        "done": False,
                    },
                }
            )
        if source_assessment.get("sufficient"):
            final_answer = await _generate_final_answer(
                request=request,
                user=user,
                model_id=model_id,
                prompt=query,
                learnings=all_learnings,
                sources=all_sources,
                context_items=context_items,
                requirements=requirements,
                system_prompt=resolved_system_prompt,
            )
            context_sufficient = True
            break

        if iteration >= max_iterations:
            break

        refined_query = (source_assessment.get("refined_query") or "").strip()
        if not refined_query and feedback:
            refined_query = f"{query}\n\nFeedback from previous round:\n{feedback}"
        if not refined_query or refined_query == current_query.strip():
            break

        current_query = refined_query
        if on_progress:
            await on_progress(
                {
                    "type": "status",
                    "data": {
                        "action": "deep_search",
                        "description": "Refining query to improve source accuracy",
                        "done": False,
                    },
                }
            )

    result = {"learnings": all_learnings, "sources": all_sources}
    context_items = _build_context_items(result.get("sources", []))
    log.info(
        "deep_search.report_flow.search_complete learnings=%s sources=%s iterations=%s",
        len(result.get("learnings", [])),
        len(result.get("sources", [])),
        max_iterations,
    )

    if on_progress:
        for source in result.get("sources", []):
            await on_progress(
                {
                    "type": "source",
                    "data": {
                        "source": {"name": source.get("title"), "url": source.get("url")},
                        "document": [source.get("snippet", "")],
                        "metadata": [
                            {
                                "source": source.get("url"),
                                "name": source.get("title"),
                                "url": source.get("url"),
                            }
                        ],
                        "distances": [],
                    },
                }
            )

    if not final_answer:
        final_context_items = _build_context_items(result.get("sources", []))
        final_answer = await _generate_final_answer(
            request=request,
            user=user,
            model_id=model_id,
            prompt=query,
            learnings=result.get("learnings", []),
            sources=result.get("sources", []),
            context_items=final_context_items,
            requirements=requirements,
            system_prompt=resolved_system_prompt,
        )
        if on_progress and not context_sufficient:
            await on_progress(
                {
                    "type": "status",
                    "data": {
                        "action": "deep_search",
                        "description": "Reached max rounds; generated best-effort answer from collected context",
                        "done": False,
                    },
                }
            )

    answer = (final_answer or "").strip()
    if not answer:
        answer = "Unable to produce a reliable answer from the collected web context."
    log.info(
        "deep_search.report_flow.done answer_len=%s learnings=%s sources=%s",
        len(answer or ""),
        len(result.get("learnings", [])),
        len(result.get("sources", [])),
    )

    if on_progress:
        await on_progress(
            {
                "type": "status",
                "data": {
                    "action": "deep_search",
                    "description": "Deep research completed",
                    "done": True,
                },
            }
        )

    return {
        "answer": answer,
        "learnings": result.get("learnings", []),
        "sources": result.get("sources", []),
    }
