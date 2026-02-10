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


def _system_prompt() -> str:
    now = datetime.now(timezone.utc).isoformat()
    return (
        "You are an expert researcher. Today is "
        + now
        + ". Follow these instructions when responding:\n"
        "- You may be asked to research subjects that are after your knowledge cutoff.\n"
        "- The user is a highly experienced analyst; be as detailed as possible and make sure your response is correct.\n"
        "- Be highly organized.\n"
        "- Suggest solutions that the user didn't think about.\n"
        "- Be proactive and anticipate needs.\n"
        "- Mistakes erode trust, so be accurate and thorough.\n"
        "- Provide detailed explanations; lots of detail is acceptable.\n"
        "- Value good arguments over authorities.\n"
        "- Consider new technologies and contrarian ideas, not just conventional wisdom.\n"
        "- You may use speculation or prediction, but flag it clearly."
    )


def _build_report_prompt(prompt: str, learnings: List[str]) -> str:
    learnings_section = "\n".join(f"<learning>\n{l}\n</learning>" for l in learnings)
    return (
        "Given the following prompt from the user, write a final report on the topic using the learnings "
        "from research. Make it as detailed as possible, aim for 3 or more pages, and include ALL the learnings "
        "from research. Use clear markdown structure with H2/H3 headings and bold key terms or conclusions. "
        "Do not mix heading text with bold in the same line.\n\n"
        f"<prompt>{prompt}</prompt>\n\n"
        "<learnings>\n"
        + learnings_section
        + "\n</learnings>"
    )


async def _call_llm_json(
    request: Request,
    user: Any,
    model_id: str,
    prompt: str,
) -> Tuple[str, Optional[Dict[str, Any]]]:
    payload = {
        "model": model_id,
        "messages": [
            {"role": "system", "content": _system_prompt()},
            {"role": "user", "content": prompt},
        ],
        "stream": False,
        "metadata": {"task": "deep_search"},
    }

    models = request.app.state.MODELS
    payload = await process_pipeline_inlet_filter(request, payload, user, models)
    res = await generate_chat_completion(request, form_data=payload, user=user)
    if hasattr(res, "body"):
        try:
            res = json.loads(res.body.decode("utf-8"))
        except Exception:
            res = {}
    content = res.get("choices", [{}])[0].get("message", {}).get("content", "")
    return content, _extract_json(content)


async def _generate_serp_queries(
    request: Request,
    user: Any,
    model_id: str,
    query: str,
    breadth: int,
    learnings: Optional[List[str]] = None,
) -> List[Dict[str, str]]:
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

    _, parsed = await _call_llm_json(request, user, model_id, prompt)
    if parsed and isinstance(parsed.get("queries"), list):
        queries = [
            q
            for q in parsed.get("queries", [])
            if isinstance(q, dict) and q.get("query")
        ]
        if queries:
            return queries[:breadth]
    return [{"query": query, "researchGoal": "Explore the topic in depth."}]


async def _extract_learnings(
    request: Request,
    user: Any,
    model_id: str,
    query: str,
    contents: List[str],
    num_learnings: int,
    num_followups: int,
) -> Tuple[List[str], List[str]]:
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

    _, parsed = await _call_llm_json(request, user, model_id, prompt)
    learnings = parsed.get("learnings", []) if parsed else []
    followups = parsed.get("followUpQuestions", []) if parsed else []

    learnings = [l for l in learnings if isinstance(l, str) and l.strip()]
    followups = [q for q in followups if isinstance(q, str) and q.strip()]

    return learnings[:num_learnings], followups[:num_followups]


def _inject_citations(report: str, sources: List[Dict[str, str]]) -> str:
    report = report.strip()
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


async def _write_final_report(
    request: Request,
    user: Any,
    model_id: str,
    prompt: str,
    learnings: List[str],
    sources: List[Dict[str, str]],
) -> str:
    report_prompt = _build_report_prompt(prompt, learnings) + (
        '\n\nReturn JSON in the form:\n{"reportMarkdown":"..."}'
    )

    raw, parsed = await _call_llm_json(request, user, model_id, report_prompt)
    report = parsed.get("reportMarkdown") if parsed else raw
    if not isinstance(report, str) or not report.strip():
        report = raw

    return _inject_citations(report, sources)


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
        request, user, model_id, query, breadth, learnings=learnings
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
                        learnings=combined_learnings,
                        visited_urls=combined_urls,
                        sources=all_sources,
                        on_progress=on_progress,
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
    on_progress: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
) -> Dict[str, Any]:
    full_query = query
    if messages:
        conversation = "\n".join(
            f"{(m.get('role') or 'user').upper()}: {m.get('content')}"
            for m in messages
            if m.get("content")
        )
        full_query = (
            "Use the full conversation context below when generating search queries and the final report.\n\n"
            f"{conversation}\n\nCurrent request: {query}"
        )

    result = await run_deep_search(
        request=request,
        user=user,
        model_id=model_id,
        query=full_query,
        depth=depth,
        breadth=breadth,
        result_count=result_count,
        concurrency=concurrency,
        on_progress=on_progress,
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

    report = await _write_final_report(
        request,
        user,
        model_id,
        full_query,
        result.get("learnings", []),
        result.get("sources", []),
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
        "report": report,
        "learnings": result.get("learnings", []),
        "sources": result.get("sources", []),
    }
