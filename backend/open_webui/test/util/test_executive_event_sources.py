import pytest

from open_webui.utils import executive_event_sources as sources


def test_registration_link_prefers_real_ticket_link_over_social_links():
    page = sources._parse_page(
        """
        <a href="https://www.facebook.com/example">Facebook</a>
        <a href="https://tickets.example.com/register/42">Register now</a>
        """
    )

    assert (
        sources._registration_url(page, "https://events.example.com/event/42")
        == "https://tickets.example.com/register/42"
    )


@pytest.mark.asyncio
async def test_business_nsw_normalises_cms_url_and_maps_api_fields(monkeypatch):
    async def get_json(*args, **kwargs):
        return {
            "items": [
                {
                    "id": 42,
                    "eventName": "Sydney CEO Forum",
                    "shortDescription": "A forum for senior business leaders.",
                    "eventDate": "2099-08-20T09:00:00",
                    "location": {"id": 18, "location": "Sydney"},
                    "price": "Paid",
                    "url": "~/events/sydney-ceo-forum",
                }
            ],
            "totalPages": 1,
        }

    monkeypatch.setattr(sources, "_get_json", get_json)

    events = await sources.crawl_business_nsw_events()

    assert len(events) == 1
    assert events[0]["url"] == "https://www.businessnsw.com/events/sydney-ceo-forum"
    assert events[0]["sourceType"] == "business-nsw"
    assert events[0]["location"] == "Sydney"


@pytest.mark.asyncio
async def test_aicd_keeps_sydney_and_virtual_events_only(monkeypatch):
    async def get_json(*args, **kwargs):
        return {
            "results": [
                {
                    "title": "Sydney Directors Forum",
                    "eventStartTime": "2099-09-10T01:00:00Z",
                    "url": "/events/sydney.html",
                    "city": "Sydney",
                    "state": "NSW",
                    "line1": "1 Martin Place",
                    "deliveryMethod": "face-to-face",
                },
                {
                    "title": "Perth Directors Forum",
                    "eventStartTime": "2099-09-11T01:00:00Z",
                    "url": "/events/perth.html",
                    "city": "Perth",
                    "state": "WA",
                    "deliveryMethod": "face-to-face",
                },
            ]
        }

    monkeypatch.setattr(sources, "_get_json", get_json)

    events = await sources.crawl_aicd_events()

    assert [event["title"] for event in events] == ["Sydney Directors Forum"]


@pytest.mark.asyncio
async def test_source_failures_do_not_discard_other_source_results(monkeypatch):
    async def works():
        return [
            sources._event(
                source_type="working",
                title="Executive Forum",
                start="2099-10-01",
                url="https://example.com/executive-forum",
                organiser="Example",
            )
        ]

    async def fails():
        raise RuntimeError("blocked")

    monkeypatch.setattr(
        sources,
        "EVENT_SOURCES",
        (
            sources.EventSourceDefinition(
                "working", "Working", "https://example.com", works
            ),
            sources.EventSourceDefinition(
                "blocked", "Blocked", "https://example.net", fails
            ),
        ),
    )

    result = await sources.crawl_event_sources()

    assert len(result.events) == 1
    assert result.counts == {"working": 1, "blocked": 0}
    assert result.errors == {"blocked": "blocked"}


def test_all_builtin_source_ids_are_unique():
    ids = [source.id for source in sources.EVENT_SOURCES]

    assert len(ids) == len(set(ids))
    assert {
        "humanitix",
        "aicd",
        "aicc-nsw",
        "adapt",
        "governance-institute",
        "acs",
        "business-nsw",
        "aisa",
        "isaca-sydney",
    } == set(ids)
