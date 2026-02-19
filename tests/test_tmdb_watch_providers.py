from app.services.tmdb import (
    _dedupe_streaming_providers,
    _extract_direct_streaming_urls_from_watch_html,
)


def test_dedupe_streaming_providers_ignores_rent_and_buy():
    payload = {
        "flatrate": [
            {
                "provider_id": 8,
                "provider_name": "Netflix",
                "logo_path": "/netflix.png",
                "display_priority": 2,
            },
            {
                "provider_id": 1796,
                "provider_name": "Netflix Standard with Ads",
                "logo_path": "/netflix-ads.png",
                "display_priority": 3,
            },
        ],
        "ads": [
            {
                "provider_id": 73,
                "provider_name": "Tubi TV",
                "logo_path": "/tubi.png",
                "display_priority": 7,
            }
        ],
        "free": [
            {
                "provider_id": 8,
                "provider_name": "Netflix",
                "logo_path": "/netflix.png",
                "display_priority": 2,
            }
        ],
        "rent": [
            {
                "provider_id": 2,
                "provider_name": "Apple TV Store",
                "logo_path": "/apple.png",
                "display_priority": 1,
            }
        ],
        "buy": [
            {
                "provider_id": 3,
                "provider_name": "Google Play Movies",
                "logo_path": "/google.png",
                "display_priority": 1,
            }
        ],
    }

    providers = _dedupe_streaming_providers(payload)
    names = [row["provider_name"] for row in providers]

    assert names == ["Netflix", "Tubi TV"]


def test_dedupe_streaming_providers_orders_by_display_priority_then_name():
    payload = {
        "flatrate": [
            {"provider_id": 100, "provider_name": "Zulu", "display_priority": 12},
            {"provider_id": 101, "provider_name": "Alpha", "display_priority": 2},
            {"provider_id": 102, "provider_name": "Beta", "display_priority": 2},
        ]
    }

    providers = _dedupe_streaming_providers(payload)
    names = [row["provider_name"] for row in providers]

    assert names == ["Alpha", "Beta", "Zulu"]


def test_extract_direct_streaming_urls_from_watch_html_only_watch_links():
    html = """
    <ul class="providers">
      <li>
        <a href="https://click.justwatch.com/a?foo=bar&r=https%3A%2F%2Fwww.netflix.com%2Ftitle%2F80057281"
           title="Watch Stranger Things on Netflix">Netflix</a>
      </li>
      <li>
        <a href="https://click.justwatch.com/a?foo=bar&r=https%3A%2F%2Ftv.youtube.com%2Fbrowse%2FUCLqUTxe"
           title="Watch Stranger Things on YouTube TV">YouTube TV</a>
      </li>
      <li>
        <a href="https://click.justwatch.com/a?foo=bar&r=https%3A%2F%2Ftv.apple.com%2Fshow%2Fabc"
           title="Buy Stranger Things on Apple TV Store">Apple TV</a>
      </li>
    </ul>
    """

    out = _extract_direct_streaming_urls_from_watch_html(html)

    assert out == {
        "netflix": "https://www.netflix.com/title/80057281",
        "youtube tv": "https://tv.youtube.com/browse/UCLqUTxe",
    }
