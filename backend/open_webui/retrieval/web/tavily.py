from __future__ import annotations

import logging

from tavily import TavilyClient
from open_webui.env import TAVILY_API_BASE_URL
from open_webui.retrieval.web.main import SearchResult, get_filtered_results

log = logging.getLogger(__name__)


def search_tavily(
    api_key: str,
    query: str,
    count: int,
    filter_list: list[str] | None = None,
    search_depth: str = 'basic',
    # **kwargs,
) -> list[SearchResult]:
    """Search using Tavily's Search API and return the results as a list of SearchResult objects.

    Args:
        api_key (str): A Tavily Search API key
        query (str): The query to search for
        count (int): The maximum number of results to return
        search_depth (str): Tavily search depth

    Returns:
        A list of SearchResult objects.
    """
    tavily_client = TavilyClient(api_key=api_key, api_base_url=TAVILY_API_BASE_URL)
    json_response = tavily_client.search(query, max_results=count, search_depth=search_depth)

    results = json_response.get('results', [])
    if filter_list:
        results = get_filtered_results(results, filter_list)

    return [
        SearchResult(
            link=result['url'],
            title=result.get('title', ''),
            snippet=result.get('content'),
        )
        for result in results
    ]
