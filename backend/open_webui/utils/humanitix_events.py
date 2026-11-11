import asyncio
import hashlib
import html
import json
import logging
import os
import re

from datetime import date, datetime
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse, urlunparse

import httpx


log = logging.getLogger(__name__)

HUMANITIX_DIRECTORY_URL = os.getenv(
    "HUMANITIX_BIG_EVENTS_URL",
    "https://humanitix.com/au/events/au--nsw--sydney/businessandprofessional",
)
HUMANITIX_MAX_PAGES = max(1, min(20, int(os.getenv("HUMANITIX_MAX_PAGES", "5"))))
HUMANITIX_MAX_DETAILS = max(1, min(500, int(os.getenv("HUMANITIX_MAX_DETAILS", "160"))))
REQUEST_TIMEOUT = 30
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0 Safari/537.36"
    ),
    "Accept": "text/html,application/json",
}


class _ScriptParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.capture: str | None = None
        self.buffer: list[str] = []
        self.next_data: str | None = None
        self.json_ld: list[str] = []

    def handle_starttag(self, tag, attrs) -> None:
        if tag != "script":
            return
        values = dict(attrs)
        if values.get("id") == "__NEXT_DATA__":
            self.capture = "next"
            self.buffer = []
        elif values.get("type") == "application/ld+json":
            self.capture = "jsonld"
            self.buffer = []

    def handle_data(self, data) -> None:
        if self.capture:
            self.buffer.append(data)

    def handle_endtag(self, tag) -> None:
        if tag != "script" or not self.capture:
            return
        value = "".join(self.buffer)
        if self.capture == "next":
            self.next_data = value
        else:
            self.json_ld.append(value)
        self.capture = None
        self.buffer = []


def _parse_html(value: str) -> _ScriptParser:
    parser = _ScriptParser()
    parser.feed(value)
    return parser


def directory_request(value: str) -> tuple[dict, str]:
    """Return the Humanitix recommendations request and its same-origin API URL."""
    parser = _parse_html(value)
    if not parser.next_data:
        raise ValueError("Humanitix directory did not contain __NEXT_DATA__")
    page_props = json.loads(parser.next_data)["props"]["pageProps"]
    category = page_props.get("parsedCategories", {}).get(
        "category", "businessAndProfessional"
    )
    body = {
        "query": "",
        "locationQuery": "",
        "locationType": "",
        "types": [],
        "categories": [category],
        "subcategories": [],
        "interests": [],
        "prices": "all",
        "dates": "",
        "startDate": "",
        "endDate": "",
        "accessibility": [],
        "page": 0,
        "safeSearch": True,
        "geobox": page_props["geobox"],
        "category": category,
        "stateKey": page_props.get("stateKey"),
    }
    parsed = urlparse(HUMANITIX_DIRECTORY_URL)
    return body, urlunparse(
        (parsed.scheme, parsed.netloc, "/api/recommendations", "", "", "")
    )


def _event_nodes(value: str) -> list[dict]:
    nodes = []
    for raw in _parse_html(value).json_ld:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        candidates = data if isinstance(data, list) else [data]
        for candidate in candidates:
            if isinstance(candidate, dict) and candidate.get("@type") == "Event":
                nodes.append(candidate)
    return nodes


def _plain_text(value: object) -> str:
    text = re.sub(r"<[^>]+>", " ", str(value or ""))
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def _iso_date(value: object) -> str | None:
    if not value:
        return None
    raw = str(value)
    try:
        if "Coordinated Universal Time" in raw:
            parsed = datetime.strptime(raw.split(" (")[0], "%a %b %d %Y %H:%M:%S GMT%z")
        else:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return parsed.date().isoformat()
    except ValueError:
        match = re.match(r"(20\d{2}-\d{2}-\d{2})", raw)
        return match.group(1) if match else None


def _location(listing: dict, detail: dict) -> str:
    location = listing.get("eventLocation") or {}
    if location.get("type") == "online":
        return "Online"
    parts = [location.get("venueName"), location.get("address")]
    if any(parts):
        return ", ".join(str(part) for part in parts if part)
    detail_location = detail.get("location") or {}
    if detail_location.get("@type") == "VirtualLocation":
        return "Online"
    address = detail_location.get("address")
    if isinstance(address, dict):
        address = ", ".join(
            str(address.get(key))
            for key in ("streetAddress", "addressLocality", "addressRegion")
            if address.get(key)
        )
    return _plain_text(detail_location.get("name") or address or "Sydney NSW")


def _cost(listing: dict, detail: dict) -> str:
    pricing = listing.get("pricing") or {}
    minimum = pricing.get("minimumPrice")
    maximum = pricing.get("maximumPrice")
    if minimum == 0 and maximum == 0:
        return "Free"
    if minimum is not None and maximum is not None:
        price = (
            f"A${minimum:g}" if minimum == maximum else f"A${minimum:g}–A${maximum:g}"
        )
        return f"{price}{' + booking fee' if pricing.get('plusBuyerFee') else ''}"
    offers = detail.get("offers") or []
    if isinstance(offers, dict):
        offers = [offers]
    prices = [offer.get("price") for offer in offers if offer.get("price") is not None]
    currency = next(
        (offer.get("priceCurrency") for offer in offers if offer.get("priceCurrency")),
        "AUD",
    )
    return f"{currency} {min(prices):g}" if prices else "See registration page"


def listing_to_event(
    listing: dict, detail_html: str, today: date | None = None
) -> dict | None:
    """Combine a directory result with its event page; directory URLs are never emitted."""
    today = today or date.today()
    slug = str(listing.get("slug") or "").strip("/")
    hostname = str(listing.get("hostname") or "https://events.humanitix.com/")
    url = urljoin(hostname, slug)
    if not slug or urlparse(url).hostname != "events.humanitix.com":
        return None

    nodes = _event_nodes(detail_html)
    if not nodes:
        return None
    listing_start = _iso_date((listing.get("date") or {}).get("startDate"))
    detail = next(
        (node for node in nodes if _iso_date(node.get("startDate")) == listing_start),
        nodes[0],
    )
    start = listing_start or _iso_date(detail.get("startDate"))
    if not start or start < today.isoformat():
        return None

    title = _plain_text(listing.get("name") or detail.get("name"))
    description = _plain_text(detail.get("description"))
    organiser_data = listing.get("organiser") or detail.get("organizer") or {}
    organiser = _plain_text(
        organiser_data.get("name")
        if isinstance(organiser_data, dict)
        else organiser_data
    )
    if not title:
        return None

    offers = detail.get("offers") or []
    if isinstance(offers, dict):
        offers = [offers]
    registration_url = next(
        (offer.get("url") for offer in offers if offer.get("url")), f"{url}/tickets"
    )
    fingerprint = hashlib.sha256(f"{url}|{start}".encode()).hexdigest()[:32]
    return {
        "id": f"humanitix-{fingerprint}",
        "title": title,
        "start": start,
        "end": _iso_date(
            (listing.get("date") or {}).get("endDate") or detail.get("endDate")
        ),
        "description": description,
        "location": _location(listing, detail),
        "organiser": organiser or "Humanitix event organiser",
        "targetAudience": "Senior executive decision-makers",
        "cost": _cost(listing, detail),
        "participation": "Open the registration page to select a ticket or apply to attend.",
        "registrationUrl": registration_url,
        "url": url,
        "lastVerified": today.isoformat(),
        "category": "business",
        "sourceId": "humanitix-sydney-business",
        "sourceType": "humanitix",
        "discoveryStatus": "verified",
    }


def _get_text(url: str) -> str:
    response = httpx.get(
        url, headers=HEADERS, timeout=REQUEST_TIMEOUT, follow_redirects=True
    )
    response.raise_for_status()
    return response.text


def _post_page(url: str, body: dict) -> list[dict]:
    response = httpx.post(
        url,
        headers=HEADERS,
        json=body,
        timeout=REQUEST_TIMEOUT,
        follow_redirects=True,
    )
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, list):
        raise ValueError("Humanitix recommendations response was not a list")
    return data


async def crawl_humanitix_events(request=None, user=None) -> list[dict]:
    directory_html = await asyncio.to_thread(_get_text, HUMANITIX_DIRECTORY_URL)
    body, api_url = directory_request(directory_html)
    listings: dict[str, dict] = {}
    for page in range(HUMANITIX_MAX_PAGES):
        page_body = {**body, "page": page}
        results = await asyncio.to_thread(_post_page, api_url, page_body)
        for listing in results:
            if listing.get("slug"):
                listings[str(listing["slug"])] = listing
        if len(results) < 32 or len(listings) >= HUMANITIX_MAX_DETAILS:
            break

    semaphore = asyncio.Semaphore(8)

    async def load(listing: dict) -> dict | None:
        url = urljoin(
            str(listing.get("hostname") or "https://events.humanitix.com/"),
            str(listing.get("slug") or ""),
        )
        async with semaphore:
            try:
                detail_html = await asyncio.to_thread(_get_text, url)
                return listing_to_event(listing, detail_html)
            except Exception as error:
                log.warning("Unable to crawl Humanitix event %s: %s", url, error)
                return None

    results = await asyncio.gather(
        *(load(listing) for listing in list(listings.values())[:HUMANITIX_MAX_DETAILS])
    )
    events = [event for event in results if event]
    if request is None:
        return events

    from open_webui.utils.big_event_llm import classify_events

    return await classify_events(request, user, events)
