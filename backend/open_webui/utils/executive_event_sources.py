import asyncio
import hashlib
import html
import json
import logging
import os
import re

from dataclasses import dataclass
from datetime import date, datetime, timezone
from html.parser import HTMLParser
from typing import Awaitable, Callable
from urllib.parse import urljoin, urlparse

import httpx

from open_webui.utils.humanitix_events import crawl_humanitix_events


log = logging.getLogger(__name__)

REQUEST_TIMEOUT = 30
MAX_DETAIL_CONCURRENCY = max(
    1, min(16, int(os.getenv("BIG_EVENTS_CRAWLER_CONCURRENCY", "8")))
)
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/json",
}
MONTH_PATTERN = (
    r"Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|"
    r"Dec(?:ember)?"
)

AICD_URL = "https://www.aicd.com.au/events/all-events.html"
AICD_API_URL = (
    "https://www.aicd.com.au/content/aicd/events/"
    "all-events.tilesearch.events.json?limit=100"
)
AICC_URL = "https://portal.aiccnsw.org.au/all-events/"
ADAPT_URL = "https://adapt.com.au/edge-events/"
GOVERNANCE_URL = "https://www.governanceinstitute.com.au/events/"
GOVERNANCE_ALGOLIA_URL = (
    "https://32VI1AR3CM-dsn.algolia.net/1/indexes/events/query"
)
ACS_URL = "https://www.acs.org.au/cpd-education/event-listing.html"
ACS_API_URL = (
    "https://www.acs.org.au/content/acs/cpd-education/event-listing/"
    "jcr:content/root/container/list_copy.event-list.json"
)
BUSINESS_NSW_URL = "https://www.businessnsw.com/events/upcoming-events"
BUSINESS_NSW_API_URL = "https://www.businessnsw.com/api/events"
AISA_URL = "https://aisa.org.au/"
ISACA_SYDNEY_URL = "https://engage.isaca.org/sydneychapter/events"


@dataclass(frozen=True)
class EventSourceDefinition:
    id: str
    name: str
    homepage_url: str
    crawler: Callable[[], Awaitable[list[dict]]]


@dataclass
class CrawlResult:
    events: list[dict]
    errors: dict[str, str]
    counts: dict[str, int]


class PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.title = ""
        self.headings: list[str] = []
        self.links: list[tuple[str, str]] = []
        self.meta: dict[str, str] = {}
        self.json_ld: list[object] = []
        self._capture: str | None = None
        self._href = ""
        self._buffer: list[str] = []
        self._visible: list[str] = []

    def handle_starttag(self, tag, attrs) -> None:
        values = dict(attrs)
        if tag == "meta":
            key = values.get("property") or values.get("name")
            if key and values.get("content"):
                self.meta[key.lower()] = values["content"]
        elif tag == "title":
            self._capture, self._buffer = "title", []
        elif tag in ("h1", "h2"):
            self._capture, self._buffer = "heading", []
        elif tag == "a":
            self._capture, self._buffer = "link", []
            self._href = values.get("href", "")
        elif tag == "script" and values.get("type") == "application/ld+json":
            self._capture, self._buffer = "jsonld", []

    def handle_data(self, data) -> None:
        if self._capture:
            self._buffer.append(data)
        if data.strip():
            self._visible.append(data)

    def handle_endtag(self, tag) -> None:
        expected = {
            "title": "title",
            "h1": "heading",
            "h2": "heading",
            "a": "link",
            "script": "jsonld",
        }.get(tag)
        if expected != self._capture:
            return
        value = _plain_text(" ".join(self._buffer))
        if expected == "title":
            self.title = value
        elif expected == "heading" and value:
            self.headings.append(value)
        elif expected == "link" and self._href:
            self.links.append((html.unescape(self._href), value))
        elif expected == "jsonld":
            try:
                self.json_ld.append(json.loads("".join(self._buffer)))
            except json.JSONDecodeError:
                pass
        self._capture, self._buffer, self._href = None, [], ""

    @property
    def text(self) -> str:
        return _plain_text(" ".join(self._visible))


def _parse_page(value: str) -> PageParser:
    parser = PageParser()
    parser.feed(value)
    return parser


def _plain_text(value: object) -> str:
    text = re.sub(r"<[^>]+>", " ", str(value or ""))
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def _iso_date(value: object) -> str | None:
    if value is None or value == "":
        return None
    raw = _plain_text(value)
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        pass
    for expression, formats in (
        (r"\b(\d{1,2}/\d{1,2}/20\d{2})\b", ("%d/%m/%Y",)),
        (
            rf"\b(\d{{1,2}}\s+(?:{MONTH_PATTERN})\s+20\d{{2}})\b",
            ("%d %B %Y", "%d %b %Y"),
        ),
        (
            rf"\b((?:{MONTH_PATTERN})\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s+20\d{{2}})\b",
            ("%B %d %Y", "%b %d %Y"),
        ),
    ):
        match = re.search(expression, raw, re.IGNORECASE)
        if not match:
            continue
        cleaned = re.sub(r"(\d)(?:st|nd|rd|th)", r"\1", match.group(1)).replace(",", "")
        for fmt in formats:
            try:
                return datetime.strptime(cleaned, fmt).date().isoformat()
            except ValueError:
                continue
    match = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", raw)
    return match.group(1) if match else None


def _stable_id(source_type: str, url: str, start: str) -> str:
    fingerprint = hashlib.sha256(f"{url}|{start}".encode()).hexdigest()[:32]
    return f"{source_type}-{fingerprint}"


def _location(*parts: object) -> str:
    values = [_plain_text(part) for part in parts if _plain_text(part)]
    return ", ".join(dict.fromkeys(values))


def _registration_url(page: PageParser, base_url: str) -> str:
    preferred = []
    for href, label in page.links:
        absolute = urljoin(base_url, href)
        if urlparse(absolute).scheme not in ("http", "https"):
            continue
        score = 0
        text = f"{label} {href}".lower()
        if any(word in text for word in ("register", "ticket", "book", "apply")):
            score += 2
        if any(word in href.lower() for word in ("register", "ticket")):
            score += 3
        if urlparse(absolute).hostname == urlparse(base_url).hostname:
            score += 1
        if any(word in text for word in ("login", "myregistrations", "javascript")):
            score -= 2
        if score > 0:
            preferred.append((score, absolute))
    return max(preferred, key=lambda item: item[0], default=(0, base_url))[1]


def _page_title(page: PageParser) -> str:
    title = next((item for item in page.headings if len(item) > 4), "")
    return title or page.meta.get("og:title") or page.title.split("|")[0].strip()


def _page_description(page: PageParser) -> str:
    return _plain_text(
        page.meta.get("description") or page.meta.get("og:description") or ""
    )


def _cost_from_text(text: str) -> str:
    if re.search(r"\bfree\b", text, re.IGNORECASE):
        return "Free"
    prices = re.findall(
        r"(?:AUD|AU\$|A\$|\$)\s?\d+(?:\.\d{1,2})?", text, re.IGNORECASE
    )
    if prices:
        return " / ".join(dict.fromkeys(prices[:4]))
    if re.search(r"invitation only|by invitation", text, re.IGNORECASE):
        return "By invitation only"
    return "See registration page"


def _event(
    *,
    source_type: str,
    title: object,
    start: object,
    url: str,
    organiser: str,
    description: object = "",
    end: object = None,
    location: object = "Sydney NSW",
    cost: object = "See registration page",
    registration_url: str | None = None,
    category: str = "business",
) -> dict | None:
    start_date = _iso_date(start)
    title_text = _plain_text(title)
    if not title_text or not start_date or start_date < date.today().isoformat():
        return None
    absolute_url = str(url)
    return {
        "id": _stable_id(source_type, absolute_url, start_date),
        "title": title_text,
        "start": start_date,
        "end": _iso_date(end),
        "description": _plain_text(description),
        "location": _plain_text(location) or "Sydney NSW",
        "organiser": organiser,
        "targetAudience": "Senior executive decision-makers",
        "cost": _plain_text(cost) or "See registration page",
        "participation": (
            "Open the registration page to register, buy a ticket, or apply to attend."
        ),
        "registrationUrl": registration_url or absolute_url,
        "url": absolute_url,
        "lastVerified": date.today().isoformat(),
        "category": category,
        "sourceId": source_type,
        "sourceType": source_type,
        "discoveryStatus": "verified",
    }


async def _get_text(url: str, *, headers: dict | None = None) -> str:
    def load() -> str:
        response = httpx.get(
            url,
            headers={**HEADERS, **(headers or {})},
            timeout=REQUEST_TIMEOUT,
            follow_redirects=True,
        )
        response.raise_for_status()
        return response.text

    return await asyncio.to_thread(load)


async def _get_json(
    url: str, *, params: dict | None = None, headers: dict | None = None
) -> dict:
    def load() -> dict:
        response = httpx.get(
            url,
            params=params,
            headers={**HEADERS, **(headers or {})},
            timeout=REQUEST_TIMEOUT,
            follow_redirects=True,
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError(f"Expected an object from {url}")
        return data

    return await asyncio.to_thread(load)


async def _post_json(url: str, body: dict, *, headers: dict | None = None) -> dict:
    def load() -> dict:
        response = httpx.post(
            url,
            json=body,
            headers={**HEADERS, **(headers or {})},
            timeout=REQUEST_TIMEOUT,
            follow_redirects=True,
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError(f"Expected an object from {url}")
        return data

    return await asyncio.to_thread(load)


async def _load_pages(urls: list[str]) -> list[tuple[str, PageParser]]:
    semaphore = asyncio.Semaphore(MAX_DETAIL_CONCURRENCY)

    async def load(url: str) -> tuple[str, PageParser] | None:
        async with semaphore:
            try:
                return url, _parse_page(await _get_text(url))
            except Exception as error:
                log.warning("Unable to crawl event detail %s: %s", url, error)
                return None

    pages = await asyncio.gather(*(load(url) for url in dict.fromkeys(urls)))
    return [page for page in pages if page]


async def crawl_aicd_events() -> list[dict]:
    data = await _get_json(AICD_API_URL)
    events = []
    for item in data.get("results", []):
        city = _plain_text(item.get("city"))
        delivery = _plain_text(item.get("deliveryMethod"))
        if city.lower() != "sydney" and "virtual" not in delivery.lower():
            continue
        url = urljoin(AICD_URL, str(item.get("url") or ""))
        event = _event(
            source_type="aicd",
            title=item.get("title"),
            start=item.get("eventStartTime"),
            end=item.get("eventEndTime"),
            url=url,
            organiser="Australian Institute of Company Directors",
            description=item.get("description"),
            location=(
                "Online"
                if "virtual" in delivery.lower()
                else _location(item.get("line1"), city, item.get("state"))
            ),
            registration_url=url,
        )
        if event:
            events.append(event)
    return events


async def crawl_governance_institute_events() -> list[dict]:
    midnight = datetime.combine(date.today(), datetime.min.time(), tzinfo=timezone.utc)
    data = await _post_json(
        GOVERNANCE_ALGOLIA_URL,
        {
            "query": "",
            "hitsPerPage": 100,
            "filters": f"display_on_web:true AND start_timestamp >= {int(midnight.timestamp())}",
        },
        headers={
            "x-algolia-application-id": "32VI1AR3CM",
            "x-algolia-api-key": "6e8fd0f1c2e58145da356f8eea9c77d4",
            "Content-Type": "application/json",
        },
    )
    events = []
    for item in data.get("hits", []):
        state = _plain_text(item.get("venue_state"))
        suburb = _plain_text(item.get("venue_suburb"))
        location_text = _plain_text(item.get("location"))
        format_text = _plain_text(item.get("format"))
        if state != "NSW" and "online" not in format_text.lower():
            continue
        if state == "NSW" and suburb and "sydney" not in f"{suburb} {location_text}".lower():
            continue
        event = _event(
            source_type="governance-institute",
            title=item.get("event_name") or item.get("title"),
            start=item.get("start_time"),
            end=item.get("end_time"),
            url=str(item.get("url") or GOVERNANCE_URL),
            organiser="Governance Institute of Australia",
            description=item.get("web_description_detail"),
            location="Online" if "online" in format_text.lower() else _location(suburb, state),
            cost=item.get("web_display_cost") or "See registration page",
        )
        if event:
            events.append(event)
    return events


async def crawl_acs_events() -> list[dict]:
    data = await _get_json(ACS_API_URL)
    events = []
    for item in data.get("events", []):
        state = _plain_text(item.get("eventState"))
        city = _plain_text(item.get("eventCity"))
        is_virtual = bool(item.get("eventIsVirtual"))
        if state != "NSW":
            continue
        if city and "sydney" not in city.lower() and not is_virtual:
            continue
        event_id = str(item.get("eventId") or "")
        if not event_id:
            continue
        url = f"https://www.acs.org.au/cpd-education/event-detail.html?eventId={event_id}"
        event = _event(
            source_type="acs",
            title=item.get("eventTitle"),
            start=item.get("eventStartDateUTC") or item.get("eventStartDate"),
            end=item.get("eventEndDateUTC") or item.get("eventEndDate"),
            url=url,
            organiser="Australian Computer Society",
            description=item.get("description"),
            location=(
                "Online"
                if is_virtual
                else _location(
                    item.get("eventVenue"), item.get("eventStreet"), city, state
                )
            ),
            registration_url=url,
            category="technology",
        )
        if event:
            events.append(event)
    return events


async def crawl_business_nsw_events() -> list[dict]:
    events = []
    page = 1
    while page <= 10:
        data = await _get_json(
            BUSINESS_NSW_API_URL,
            params={
                "type": "upcoming",
                "page": page,
                "pagesize": 100,
                "onlineOnly": "false",
            },
            headers={"Referer": BUSINESS_NSW_URL, "Accept": "application/json"},
        )
        for item in data.get("items", []):
            item_location = item.get("location") or item.get("Location") or {}
            if isinstance(item_location, dict):
                item_location = item_location.get("location") or item_location.get("Location")
            is_online = bool(item.get("isOnline") or item.get("IsOnline"))
            if (
                item_location
                and "sydney" not in _plain_text(item_location).lower()
                and not is_online
            ):
                continue
            raw_url = str(item.get("url") or item.get("Url") or "").replace("~/", "/", 1)
            url = urljoin(BUSINESS_NSW_URL, raw_url)
            event = _event(
                source_type="business-nsw",
                title=item.get("eventName") or item.get("EventName"),
                start=item.get("eventDate") or item.get("EventDate"),
                url=url,
                organiser="Business NSW",
                description=item.get("shortDescription") or item.get("description"),
                location="Online" if is_online else (item_location or "Sydney NSW"),
                cost=item.get("price") or item.get("Price") or "See registration page",
                registration_url=url,
            )
            if event:
                events.append(event)
        if page >= int(data.get("totalPages") or 1):
            break
        page += 1
    return events


async def _crawl_link_directory(
    *,
    source_type: str,
    index_url: str,
    link_predicate: Callable[[str], bool],
    organiser: str,
    category: str = "business",
    require_sydney: bool = True,
    fixed_location: str | None = None,
    document_title: bool = False,
    fixed_cost: str | None = None,
) -> list[dict]:
    index = _parse_page(await _get_text(index_url))
    urls = [urljoin(index_url, href) for href, _ in index.links if link_predicate(href)]
    pages = await _load_pages(urls)
    events = []
    for url, page in pages:
        text = page.text
        if require_sydney and not re.search(
            r"\bSydney\b|\bonline\b|\bvirtual\b", text, re.IGNORECASE
        ):
            continue
        start = _iso_date(text) or _iso_date(page.title)
        title = (
            page.meta.get("og:title") or page.title.split("|")[0].strip()
            if document_title
            else _page_title(page)
        )
        title_context = f"{title} {page.title}"
        location = fixed_location or (
            "Online"
            if re.search(r"\bonline\b|\bwebinar\b|\bvirtual\b", title_context, re.IGNORECASE)
            else "Sydney NSW"
        )
        event = _event(
            source_type=source_type,
            title=title,
            start=start,
            url=url,
            organiser=organiser,
            description=_page_description(page),
            location=location,
            cost=fixed_cost or _cost_from_text(text),
            registration_url=_registration_url(page, url),
            category=category,
        )
        if event:
            events.append(event)
    return events


async def crawl_aicc_events() -> list[dict]:
    return await _crawl_link_directory(
        source_type="aicc-nsw",
        index_url=AICC_URL,
        link_predicate=lambda href: "/all-events/events-details/" in href,
        organiser="Australia-Israel Chamber of Commerce NSW",
    )


async def crawl_adapt_events() -> list[dict]:
    return await _crawl_link_directory(
        source_type="adapt",
        index_url=ADAPT_URL,
        link_predicate=lambda href: "/events/" in href
        and "edge" in href.lower()
        and "melbourne" not in href.lower()
        and "government" not in href.lower(),
        organiser="ADAPT",
        category="technology",
        fixed_location="Sydney NSW",
        document_title=True,
        fixed_cost="Apply to attend; see registration page",
    )


async def crawl_aisa_events() -> list[dict]:
    return await _crawl_link_directory(
        source_type="aisa",
        index_url=AISA_URL,
        link_predicate=lambda href: "Event_Display.aspx" in href and "EventKey=" in href,
        organiser="Australian Information Security Association",
        category="technology",
    )


async def crawl_isaca_sydney_events() -> list[dict]:
    excluded = ("calendar", "communityday", "myregistrations", "new-page")
    return await _crawl_link_directory(
        source_type="isaca-sydney",
        index_url=ISACA_SYDNEY_URL,
        link_predicate=lambda href: "/sydneychapter/events/" in href.lower()
        and not any(value in href.lower() for value in excluded),
        organiser="ISACA Sydney Chapter",
        category="technology",
        require_sydney=False,
        fixed_location="Sydney NSW",
    )


EVENT_SOURCES = (
    EventSourceDefinition(
        "humanitix",
        "Humanitix · Sydney Business & Professional",
        "https://humanitix.com/au/events/au--nsw--sydney/businessandprofessional",
        crawl_humanitix_events,
    ),
    EventSourceDefinition("aicd", "AICD", AICD_URL, crawl_aicd_events),
    EventSourceDefinition("aicc-nsw", "AICC NSW", AICC_URL, crawl_aicc_events),
    EventSourceDefinition("adapt", "ADAPT Edge", ADAPT_URL, crawl_adapt_events),
    EventSourceDefinition(
        "governance-institute",
        "Governance Institute of Australia",
        GOVERNANCE_URL,
        crawl_governance_institute_events,
    ),
    EventSourceDefinition("acs", "Australian Computer Society", ACS_URL, crawl_acs_events),
    EventSourceDefinition(
        "business-nsw", "Business NSW", BUSINESS_NSW_URL, crawl_business_nsw_events
    ),
    EventSourceDefinition("aisa", "AISA", AISA_URL, crawl_aisa_events),
    EventSourceDefinition(
        "isaca-sydney", "ISACA Sydney Chapter", ISACA_SYDNEY_URL, crawl_isaca_sydney_events
    ),
)

MANAGED_SOURCE_TYPES = tuple(source.id for source in EVENT_SOURCES)


async def crawl_event_sources() -> CrawlResult:
    results = await asyncio.gather(
        *(source.crawler() for source in EVENT_SOURCES), return_exceptions=True
    )
    events: list[dict] = []
    errors: dict[str, str] = {}
    counts: dict[str, int] = {}
    seen: set[tuple[str, str]] = set()
    for source, result in zip(EVENT_SOURCES, results):
        if isinstance(result, BaseException):
            errors[source.id] = str(result)[:500]
            counts[source.id] = 0
            log.exception("Big-event source %s failed", source.id, exc_info=result)
            continue
        counts[source.id] = len(result)
        for event in result:
            key = (event["url"].rstrip("/"), event["start"])
            if key not in seen:
                seen.add(key)
                events.append(event)
    return CrawlResult(events=events, errors=errors, counts=counts)
