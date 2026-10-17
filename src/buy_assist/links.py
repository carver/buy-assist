"""Read a reference link into LinkFacts: what the linked page says about the product.

BoardGameGeek pages sit behind a Cloudflare challenge, so BGG links go through the JSON API
the BGG site itself uses. Every other site gets the generic reader, which fetches the page,
takes any structured product data, and keeps the visible text for Claude to read.
"""

import ipaddress
import json
import re
import socket
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpx2
from bs4 import BeautifulSoup
from pydantic import BaseModel, ValidationError

from buy_assist.models import is_upc, optional_text, url_domain

_URL = re.compile(r"https?://[^\s<>\"')\]]+")
_TRAILING_PUNCTUATION = ".,;:!?"
_BGG_GAME_PATH = re.compile(r"/(?:boardgame|boardgameexpansion)/(\d+)(?:/|$)")
_BGG_API = "https://api.geekdo.com/api"
_BGG_MIN_INTERVAL_SECONDS = 5.0
_MAX_REDIRECTS = 5
_MAX_PAGE_BYTES = 3_000_000
_MAX_PAGE_TEXT = 6000
_USER_AGENT = "buy-assist/0.1 (household shopping list)"

DEDICATED_DOMAINS = {"boardgamegeek.com": "BoardGameGeek"}


type Json = dict[str, Json] | list[Json] | str | int | float | bool | None


class LinkError(Exception):
    """The link couldn't be read. The message is safe to show to the person."""


@dataclass(frozen=True)
class Edition:
    name: str
    title: str
    product_code: str | None
    year: str | None
    languages: tuple[str, ...]
    publishers: tuple[str, ...]


@dataclass(frozen=True)
class LinkFacts:
    source: str
    name: str | None = None
    image_url: str | None = None
    upc: str | None = None
    model_number: str | None = None
    editions: tuple[Edition, ...] = ()
    page_text: str | None = None


def first_url(text: str) -> str | None:
    match = _URL.search(text)
    return match.group(0).rstrip(_TRAILING_PUNCTUATION) if match else None


def _resolve(host: str) -> list[str]:
    return [str(info[4][0]) for info in socket.getaddrinfo(host, None)]


class _Throttle:
    """Keeps requests to one host at least `interval` seconds apart, across threads."""

    def __init__(
        self, interval: float, clock: Callable[[], float], sleep: Callable[[float], None]
    ) -> None:
        self._interval = interval
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._last: float | None = None

    def wait(self) -> None:
        with self._lock:
            if self._last is not None:
                remaining = self._last + self._interval - self._clock()
                if remaining > 0:
                    self._sleep(remaining)
            self._last = self._clock()


class LinkReader:
    def __init__(
        self,
        client: httpx2.Client,
        *,
        resolve: Callable[[str], list[str]] = _resolve,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._client = client
        self._resolve = resolve
        self._bgg_throttle = _Throttle(_BGG_MIN_INTERVAL_SECONDS, clock, sleep)

    def read(self, url: str) -> LinkFacts:
        if urlsplit(url).scheme not in ("http", "https"):
            raise LinkError("Only http and https links can be read.")
        if url_domain(url) == "boardgamegeek.com":
            return self._read_bgg(url)
        return self._read_page(url)

    def _read_bgg(self, url: str) -> LinkFacts:
        match = _BGG_GAME_PATH.search(urlsplit(url).path)
        if not match:
            raise LinkError("That BoardGameGeek link isn't a game page.")
        game_id = match.group(1)
        game = self._bgg_get(
            _BggGameAnswer,
            "/geekitems",
            {"objectid": game_id, "objecttype": "thing", "nosession": "1"},
        ).item
        versions = self._bgg_get(
            _BggVersionsAnswer,
            "/geekitem/linkeditems",
            {
                "ajax": "1",
                "linkdata_index": "boardgameversion",
                "nosession": "1",
                "objectid": game_id,
                "objecttype": "thing",
                "pageid": "1",
                "showcount": "25",
                "sort": "yearpublished",
                "subtype": "boardgameversion",
            },
        )
        return LinkFacts(
            source=DEDICATED_DOMAINS["boardgamegeek.com"],
            name=optional_text(game.name),
            image_url=optional_text(game.imageurl),
            editions=tuple(e for v in versions.items if (e := v.edition())),
        )

    def _bgg_get[T: BaseModel](self, answer: type[T], path: str, params: dict[str, str]) -> T:
        self._bgg_throttle.wait()
        try:
            response = self._client.get(
                _BGG_API + path, params=params, headers={"user-agent": _USER_AGENT}
            )
        except httpx2.HTTPError as e:
            raise LinkError(f"BoardGameGeek didn't answer ({type(e).__name__}).") from e
        if response.status_code != 200:
            raise LinkError(f"BoardGameGeek answered {response.status_code}.")
        try:
            return answer.model_validate_json(response.content)
        except ValidationError as e:
            raise LinkError("BoardGameGeek's answer wasn't what this app expects.") from e

    def _read_page(self, url: str) -> LinkFacts:
        response = self._fetch_public(url)
        content_type = response.headers.get("content-type", "")
        if "html" not in content_type:
            raise LinkError("That link isn't a web page.")
        return _page_facts(
            url_domain(str(response.url)) or "web page", str(response.url), response.text
        )

    def _fetch_public(self, url: str) -> httpx2.Response:
        """GET a page, following redirects only to hosts on the public internet."""
        for _ in range(_MAX_REDIRECTS + 1):
            self._refuse_private_host(url)
            try:
                with self._client.stream(
                    "GET", url, headers={"user-agent": _USER_AGENT}, follow_redirects=False
                ) as response:
                    if response.is_redirect and "location" in response.headers:
                        url = urljoin(url, response.headers["location"])
                        continue
                    if response.status_code != 200:
                        raise LinkError(f"The site answered {response.status_code}.")
                    body = bytearray()
                    for chunk in response.iter_bytes():
                        body += chunk
                        if len(body) > _MAX_PAGE_BYTES:
                            raise LinkError("That page is too big to read.")
            except httpx2.HTTPError as e:
                raise LinkError(f"The site didn't answer ({type(e).__name__}).") from e
            return httpx2.Response(
                response.status_code,
                headers=response.headers,
                content=bytes(body),
                request=response.request,
            )
        raise LinkError("That link redirects too many times.")

    def _refuse_private_host(self, url: str) -> None:
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise LinkError("Only http and https links can be read.")
        try:
            addresses = self._resolve(parts.hostname)
        except OSError as e:
            raise LinkError("That site's name doesn't resolve.") from e
        if not addresses or any(not ipaddress.ip_address(a).is_global for a in addresses):
            raise LinkError("That link points at a private network address.")


class _BggLink(BaseModel):
    name: str = ""


class _BggGame(BaseModel):
    name: str | None = None
    imageurl: str | None = None


class _BggGameAnswer(BaseModel):
    item: _BggGame


class _BggVersion(BaseModel):
    versionname: str | None = None
    linkedname: str | None = None
    productcode: str | None = None
    yearpublished: str | None = None
    links: dict[str, list[_BggLink]] = {}

    def edition(self) -> Edition | None:
        name = optional_text(self.versionname)
        title = optional_text(self.linkedname)
        if not (name and title):
            return None
        return Edition(
            name=name,
            title=title,
            product_code=optional_text(self.productcode),
            year=optional_text(self.yearpublished),
            languages=self._names("languages"),
            publishers=self._names("boardgamepublisher"),
        )

    def _names(self, kind: str) -> tuple[str, ...]:
        return tuple(link.name for link in self.links.get(kind, []) if link.name)


class _BggVersionsAnswer(BaseModel):
    items: list[_BggVersion]


def _page_facts(source: str, url: str, html: str) -> LinkFacts:
    soup = BeautifulSoup(html, "html.parser")
    product = _json_ld_product(soup)
    og_title = _meta(soup, "og:title")
    og_image = _meta(soup, "og:image")
    for tag in soup(["script", "style", "noscript", "template", "svg"]):
        tag.decompose()
    text = " ".join(soup.get_text(" ").split())
    if og_title:
        text = f"{og_title}\n{text}"
    upc = _first_text(product, "gtin12", "gtin13", "gtin", "gtin14", "gtin8")
    image = _image(product.get("image")) or og_image
    return LinkFacts(
        source=source,
        name=optional_text(product.get("name")),
        image_url=urljoin(url, image) if image else None,
        upc=upc if upc and is_upc(upc) else None,
        model_number=_first_text(product, "mpn", "sku"),
        page_text=text[:_MAX_PAGE_TEXT] or None,
    )


def _json_ld_product(soup: BeautifulSoup) -> dict[str, Json]:
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data: Json = json.loads(script.get_text())
        except ValueError:
            continue
        for node in _json_ld_nodes(data):
            node_type = node.get("@type")
            types = node_type if isinstance(node_type, list) else [node_type]
            if "Product" in types:
                return node
    return {}


def _json_ld_nodes(data: Json) -> Iterator[dict[str, Json]]:
    if isinstance(data, list):
        for entry in data:
            yield from _json_ld_nodes(entry)
    elif isinstance(data, dict):
        yield data
        yield from _json_ld_nodes(data.get("@graph"))


def _meta(soup: BeautifulSoup, prop: str) -> str | None:
    tag = soup.find("meta", property=prop)
    return optional_text(tag.get("content")) if tag else None


def _first_text(product: dict[str, Json], *keys: str) -> str | None:
    for key in keys:
        if text := optional_text(product.get(key)):
            return text
    return None


def _image(value: Json) -> str | None:
    if isinstance(value, list):
        return _image(value[0]) if value else None
    if isinstance(value, dict):
        return optional_text(value.get("url"))
    return optional_text(value)
