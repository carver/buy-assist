import json
from collections.abc import Callable, Iterator
from pathlib import Path

import httpx2
import pytest

from buy_assist.bgg import BggApi
from buy_assist.links import HttpLinkReader, LinkError, first_url
from buy_assist.models import Edition
from tests.fixtures import BGG_URL, FakeClock, recorded_bgg_api

DATA = Path(__file__).parent / "data"
PUBLIC_IP = "93.184.215.14"

type Handler = Callable[[httpx2.Request], httpx2.Response]


def reader(handler: Handler, clock: FakeClock | None = None, ip: str = PUBLIC_IP) -> HttpLinkReader:
    clock = clock or FakeClock()
    client = httpx2.Client(transport=httpx2.MockTransport(handler))
    return HttpLinkReader(
        client,
        BggApi(client, clock=clock.time, sleep=clock.sleep),
        resolve=lambda host: [ip],
        clock=clock.time,
    )


def bgg_api(request: httpx2.Request) -> httpx2.Response:
    assert request.url.host == "api.geekdo.com"
    assert request.url.params["objectid"] == "343322"
    return recorded_bgg_api(request)


def shop_page(request: httpx2.Request) -> httpx2.Response:
    html = (DATA / "product_page.html").read_text()
    return httpx2.Response(200, text=html, headers={"content-type": "text/html; charset=utf-8"})


@pytest.mark.parametrize(
    ("text", "url"),
    [
        (f"golden book calendar {BGG_URL} under $35", BGG_URL),
        ("see https://shop.example/a?b=1, or the other one", "https://shop.example/a?b=1"),
        ("(https://shop.example/pan)", "https://shop.example/pan"),
        ("wool cardigan size M", None),
        ("ftp://files.example/x and nothing else", None),
    ],
)
def test_first_url_is_pulled_from_text(text: str, url: str | None) -> None:
    assert first_url(text) == url


def test_bgg_game_gives_name_image_and_editions() -> None:
    facts = reader(bgg_api).read(BGG_URL)

    assert facts.source == "BoardGameGeek"
    assert facts.name == "EXIT: The Game \u2013 Advent Calendar: The Hunt for the Golden Book"
    assert facts.image_url is not None
    assert facts.image_url.startswith("https://cf.geekdo-images.com/")
    assert len(facts.editions) == 7
    assert (
        Edition(
            name="English edition",
            title="EXIT: The Game \u2013 Advent Calendar: The Hunt for the Golden Book",
            product_code="681951",
            year="2022",
            languages=("English",),
            publishers=("KOSMOS",),
        )
        in facts.editions
    )


def test_bgg_editions_without_product_code_have_none() -> None:
    facts = reader(bgg_api).read(BGG_URL)
    german = next(e for e in facts.editions if e.name == "German edition")
    assert german.product_code is None


def test_bgg_requests_are_spaced_five_seconds_apart() -> None:
    clock = FakeClock()
    links = reader(bgg_api, clock)

    links.read(BGG_URL)
    links.read(BGG_URL)

    assert clock.sleeps == [5.0, 5.0, 5.0]


def test_bgg_error_is_link_error() -> None:
    with pytest.raises(LinkError):
        reader(lambda request: httpx2.Response(503)).read(BGG_URL)


def test_bgg_page_that_is_not_a_game_is_link_error() -> None:
    with pytest.raises(LinkError):
        reader(bgg_api).read("https://boardgamegeek.com/forum/123/whatever")


def test_bgg_unexpected_json_is_link_error() -> None:
    def odd(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, content=json.dumps({"item": "nope"}).encode())

    with pytest.raises(LinkError):
        reader(odd).read(BGG_URL)


def test_web_page_gives_structured_product_data_and_text() -> None:
    facts = reader(shop_page).read("https://shop.example/skillet")

    assert facts.source == "shop.example"
    assert facts.name == "Lodge 10.25 Inch Cast Iron Skillet"
    assert facts.upc == "075536250112"
    assert facts.model_number == "L8SK3"
    assert facts.image_url == "https://shop.example/img/skillet-1.jpg"
    assert facts.editions == ()
    assert facts.page_text is not None
    assert "Lodge Cast Iron Skillet, 10.25 in" in facts.page_text
    assert "Pre-seasoned with 100% natural vegetable oil. Made in the USA." in facts.page_text
    assert "do not read me" not in facts.page_text
    assert "color: red" not in facts.page_text


def test_web_page_without_product_data_uses_og_image_and_text_only() -> None:
    def plain(request: httpx2.Request) -> httpx2.Response:
        html = (
            '<html><head><meta property="og:image" content="/img/a.jpg"></head>'
            "<body><p>A wool cardigan</p></body></html>"
        )
        return httpx2.Response(200, text=html, headers={"content-type": "text/html"})

    facts = reader(plain).read("https://shop.example/cardigan")

    assert facts.name is None
    assert facts.upc is None
    assert facts.image_url == "https://shop.example/img/a.jpg"
    assert facts.page_text == "A wool cardigan"


def test_page_text_is_capped() -> None:
    def huge(request: httpx2.Request) -> httpx2.Response:
        body = "<p>" + "word " * 50_000 + "</p>"
        return httpx2.Response(200, text=body, headers={"content-type": "text/html"})

    facts = reader(huge).read("https://shop.example/long")
    assert facts.page_text is not None
    assert len(facts.page_text) <= 6000


@pytest.mark.parametrize("status", [403, 404, 500])
def test_blocked_or_failed_page_is_link_error(status: int) -> None:
    with pytest.raises(LinkError):
        reader(lambda request: httpx2.Response(status)).read("https://shop.example/x")


def test_non_html_page_is_link_error() -> None:
    def pdf(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, content=b"%PDF", headers={"content-type": "application/pdf"})

    with pytest.raises(LinkError):
        reader(pdf).read("https://shop.example/manual.pdf")


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.0.0.5", "192.168.1.1", "169.254.169.254", "::1"])
def test_private_hosts_are_refused_without_a_request(ip: str) -> None:
    requests: list[httpx2.Request] = []

    def record(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(200, text="<p>secret</p>", headers={"content-type": "text/html"})

    with pytest.raises(LinkError):
        reader(record, ip=ip).read("http://router.local/admin")
    assert requests == []


def test_redirect_to_private_host_is_refused() -> None:
    def redirect(request: httpx2.Request) -> httpx2.Response:
        if request.headers["host"] == "shop.example":
            return httpx2.Response(302, headers={"location": "http://internal.example/"})
        return httpx2.Response(200, text="<p>secret</p>", headers={"content-type": "text/html"})

    client = httpx2.Client(transport=httpx2.MockTransport(redirect))
    links = HttpLinkReader(
        client,
        BggApi(client),
        resolve=lambda host: [PUBLIC_IP] if host == "shop.example" else ["10.1.2.3"],
    )
    with pytest.raises(LinkError):
        links.read("https://shop.example/go")


def test_redirect_to_public_page_is_followed() -> None:
    def redirect(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/old":
            return httpx2.Response(301, headers={"location": "/new"})
        return shop_page(request)

    assert reader(redirect).read("https://shop.example/old").upc == "075536250112"


def test_non_http_url_is_link_error() -> None:
    with pytest.raises(LinkError):
        reader(shop_page).read("file:///etc/passwd")


@pytest.mark.live
def test_live_bgg_reads_fixture_game() -> None:
    client = httpx2.Client(timeout=20)
    facts = HttpLinkReader(client, BggApi(client)).read(BGG_URL)
    assert "Golden Book" in (facts.name or "")
    assert any(e.product_code == "681951" for e in facts.editions)


def test_bgg_editions_sharing_a_name_get_their_year() -> None:
    names = [e.name for e in reader(bgg_api).read(BGG_URL).editions]

    assert len(set(names)) == len(names) == 7
    assert "Dutch edition (2023)" in names
    assert "Dutch edition (2025)" in names
    assert "English edition" in names


def test_page_is_fetched_from_the_address_that_was_checked() -> None:
    seen: list[httpx2.Request] = []

    def record(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return shop_page(request)

    reader(record).read("https://shop.example/skillet")

    [request] = seen
    assert request.url.host == PUBLIC_IP
    assert request.url.path == "/skillet"
    assert request.headers["host"] == "shop.example"
    assert request.extensions["sni_hostname"] == "shop.example"


def test_host_with_any_private_address_is_refused() -> None:
    client = httpx2.Client(transport=httpx2.MockTransport(shop_page))
    links = HttpLinkReader(client, BggApi(client), resolve=lambda host: [PUBLIC_IP, "127.0.0.1"])
    with pytest.raises(LinkError):
        links.read("https://shop.example/skillet")


def test_redirect_loop_is_link_error() -> None:
    def loop(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(302, headers={"location": "/again"})

    with pytest.raises(LinkError, match="redirects too many times"):
        reader(loop).read("https://shop.example/start")


def test_oversized_page_is_link_error() -> None:
    def huge(request: httpx2.Request) -> httpx2.Response:
        chunks = (b"<p>" + b"x" * 1_000_000 for _ in range(5))
        return httpx2.Response(200, content=chunks, headers={"content-type": "text/html"})

    with pytest.raises(LinkError, match="too big"):
        reader(huge).read("https://shop.example/huge")


def test_slow_page_hits_overall_deadline() -> None:
    clock = FakeClock()

    def trickle(request: httpx2.Request) -> httpx2.Response:
        def chunks() -> Iterator[bytes]:
            for _ in range(10):
                clock.now += 5
                yield b"<p>slow</p>"

        return httpx2.Response(200, content=chunks(), headers={"content-type": "text/html"})

    with pytest.raises(LinkError, match="too long"):
        reader(trickle, clock).read("https://shop.example/slow")


def test_non_html_page_is_refused_before_its_body_is_read() -> None:
    read_chunks: list[int] = []

    def pdf(request: httpx2.Request) -> httpx2.Response:
        def chunks() -> Iterator[bytes]:
            read_chunks.append(1)
            yield b"%PDF"

        return httpx2.Response(200, content=chunks(), headers={"content-type": "application/pdf"})

    with pytest.raises(LinkError):
        reader(pdf).read("https://shop.example/manual.pdf")
    assert read_chunks == []


@pytest.mark.parametrize("image", ["data:image/png;base64,AAAA", "javascript:alert(1)"])
def test_page_image_that_is_not_a_web_link_is_dropped(image: str) -> None:
    def page(request: httpx2.Request) -> httpx2.Response:
        html = f'<html><head><meta property="og:image" content="{image}"></head><p>x</p></html>'
        return httpx2.Response(200, text=html, headers={"content-type": "text/html"})

    assert reader(page).read("https://shop.example/x").image_url is None


@pytest.mark.live
def test_live_generic_page_is_read_through_pinned_connection() -> None:
    client = httpx2.Client(timeout=20)
    facts = HttpLinkReader(client, BggApi(client)).read("https://example.com/")
    assert facts.source == "example.com"
    assert facts.page_text is not None
    assert "Example Domain" in facts.page_text
