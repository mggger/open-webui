import json
from datetime import date

from open_webui.utils.humanitix_events import directory_request, listing_to_event


def test_directory_request_uses_humanitix_recommendations_api():
    next_data = {
        "props": {
            "pageProps": {
                "parsedCategories": {"category": "businessAndProfessional"},
                "geobox": {"name": "Sydney", "latLng": {"lat": -33.87, "lng": 151.2}},
                "stateKey": "state-1",
            }
        }
    }
    page = f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(next_data)}</script>'

    body, api_url = directory_request(page)

    assert api_url == "https://humanitix.com/api/recommendations"
    assert body["categories"] == ["businessAndProfessional"]
    assert body["geobox"]["name"] == "Sydney"


def test_listing_to_event_extracts_detail_and_never_emits_directory_url():
    listing = {
        "hostname": "https://events.humanitix.com/",
        "slug": "sydney-cio-executive-roundtable",
        "name": "Sydney CIO Executive Roundtable",
        "date": {
            "startDate": "Wed Nov 18 2026 07:00:00 GMT+0000 (Coordinated Universal Time)",
            "endDate": "Wed Nov 18 2026 10:00:00 GMT+0000 (Coordinated Universal Time)",
        },
        "eventLocation": {
            "type": "address",
            "venueName": "Sydney CBD",
            "address": "Sydney NSW 2000, Australia",
        },
        "organiser": {"name": "Technology Leaders Australia"},
        "pricing": {"minimumPrice": 95, "maximumPrice": 150, "plusBuyerFee": True},
    }
    event_json = {
        "@context": "https://schema.org",
        "@type": "Event",
        "name": listing["name"],
        "startDate": "2026-11-18T18:00:00+1100",
        "endDate": "2026-11-18T21:00:00+1100",
        "description": "A cybersecurity and digital leadership forum for CIOs, CISOs and CTOs.",
        "offers": {
            "@type": "Offer",
            "url": "https://events.humanitix.com/sydney-cio-executive-roundtable/tickets",
            "price": 95,
            "priceCurrency": "AUD",
        },
    }
    detail = f'<script type="application/ld+json">{json.dumps(event_json)}</script>'

    event = listing_to_event(listing, detail, today=date(2026, 7, 15))

    assert event is not None
    assert event["start"] == "2026-11-18"
    assert event["location"].startswith("Sydney CBD")
    assert event["cost"] == "A$95–A$150 + booking fee"
    assert event["organiser"] == "Technology Leaders Australia"
    assert event["registrationUrl"].endswith("/tickets")
    assert (
        event["url"] == "https://events.humanitix.com/sydney-cio-executive-roundtable"
    )
    assert "humanitix.com/au/events" not in event["url"]
    assert event["sourceType"] == "humanitix"
    assert event["discoveryStatus"] == "verified"


def test_listing_to_event_does_not_apply_keyword_filtering():
    listing = {
        "hostname": "https://events.humanitix.com/",
        "slug": "community-picnic",
        "name": "Event Director Networking Forum",
        "date": {
            "startDate": "Sat Aug 15 2026 01:00:00 GMT+0000 (Coordinated Universal Time)"
        },
    }
    detail = (
        '<script type="application/ld+json">'
        + json.dumps(
            {
                "@type": "Event",
                "name": "Event Director Networking Forum",
                "startDate": "2026-08-15T11:00:00+1000",
                "description": "Designed for event coordinators to share practical tips.",
            }
        )
        + "</script>"
    )

    event = listing_to_event(listing, detail, today=date(2026, 7, 15))

    assert event is not None
    assert event["title"] == "Event Director Networking Forum"
