"""Read a reference link into LinkFacts: what the linked page says about the product.

BoardGameGeek links go through its JSON API (see bgg.py). Every other site gets the generic
reader, which fetches the page, takes any structured product data, and keeps the visible
text for Claude to read.
"""

import ipaddress
import json
import re
import socket
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urljoin, urlsplit

import httpx2
from bs4 import BeautifulSoup

from buy_assist.bgg import BggApi, BggError, game_id
from buy_assist.models import Edition, is_upc, optional_text, url_domain

_URL = re.compile(r"https?://[^\s<>\"')\]]+")
_TRAILING_PUNCTUATION = ".,;:!?"
_MAX_REDIRECTS = 5
_MAX_PAGE_BYTES = 3_000_000
_PAGE_DEADLINE_SECONDS = 20.0
_MAX_PAGE_TEXT = 6000
_USER_AGENT = "buy-assist/0.1 (household shopping list)"

_BGG_LABEL = "BoardGameGeek"


type Json = dict[str, Json] | list[Json] | str | int | float | bool | None


class LinkError(Exception):
    """The link couldn't be read. The message is safe to show to the person."""


@dataclass(frozen=True)
class LinkFacts:
    source: str
    name: str | None = None
    image_url: str | None = None
    upc: str | None = None
    model_number: str | None = None
    editions: tuple[Edition, ...] = ()
    page_text: str | None = None


class LinkReader(Protocol):
    def read(self, url: str) -> LinkFacts:
        """What the page at `url` says about its product. Raises LinkError."""
        ...


def first_url(text: str) -> str | None:
    match = _URL.search(text)
    return match.group(0).rstrip(_TRAILING_PUNCTUATION) if match else None


def _resolve(host: str) -> list[str]:
    return [str(info[4][0]) for info in socket.getaddrinfo(host, None)]


class HttpLinkReader:
    def __init__(
        self,
        client: httpx2.Client,
        bgg: BggApi,
        *,
        resolve: Callable[[str], list[str]] = _resolve,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = client
        self._bgg = bgg
        self._resolve = resolve
        self._clock = clock

    def read(self, url: str) -> LinkFacts:
        dedicated = _DEDICATED_READERS.get(url_domain(url) or "")
        return dedicated.read(self, url) if dedicated else self._read_page(url)

    def read_bgg(self, url: str) -> LinkFacts:
        bgg_id = game_id(url)
        if bgg_id is None:
            raise LinkError("That BoardGameGeek link isn't a game page.")
        try:
            game = self._bgg.game(bgg_id)
            editions = self._bgg.editions(bgg_id)
        except BggError as e:
            raise LinkError(str(e)) from e
        return LinkFacts(
            source=_BGG_LABEL,
            name=game.name,
            image_url=_web_url(game.image_url),
            editions=editions,
        )

    def _read_page(self, url: str) -> LinkFacts:
        page = self._fetch_public(url)
        return _page_facts(url_domain(page.url) or "web page", page.url, page.html)

    def _fetch_public(self, url: str) -> _Page:
        """GET an HTML page from a public host, following redirects only to public hosts."""
        deadline = self._clock() + _PAGE_DEADLINE_SECONDS
        for _ in range(_MAX_REDIRECTS + 1):
            request = self._pinned_request(url, deadline)
            try:
                response = self._client.send(request, stream=True, follow_redirects=False)
                try:
                    if response.is_redirect and "location" in response.headers:
                        url = urljoin(url, response.headers["location"])
                        continue
                    if response.status_code != 200:
                        raise LinkError(f"The site answered {response.status_code}.")
                    if "html" not in response.headers.get("content-type", ""):
                        raise LinkError("That link isn't a web page.")
                    body = self._read_body(response, deadline)
                finally:
                    response.close()
            except httpx2.HTTPError as e:
                raise LinkError(f"The site didn't answer ({type(e).__name__}).") from e
            return _Page(url, body.decode(response.encoding or "utf-8", errors="replace"))
        raise LinkError("That link redirects too many times.")

    def _pinned_request(self, url: str, deadline: float) -> httpx2.Request:
        """A request that connects to the exact address checked here, so a second DNS answer
        can't send it somewhere private."""
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise LinkError("Only http and https links can be read.")
        address = self._public_address(parts.hostname)
        return self._client.build_request(
            "GET",
            httpx2.URL(url).copy_with(host=address),
            headers={"host": parts.netloc.rpartition("@")[2], "user-agent": _USER_AGENT},
            extensions={"sni_hostname": parts.hostname},
            timeout=max(deadline - self._clock(), 0.1),
        )

    def _public_address(self, host: str) -> str:
        try:
            addresses = self._resolve(host)
        except OSError as e:
            raise LinkError("That site's name doesn't resolve.") from e
        if not addresses or any(not ipaddress.ip_address(a).is_global for a in addresses):
            raise LinkError("That link points at a private network address.")
        return addresses[0]

    def _read_body(self, response: httpx2.Response, deadline: float) -> bytes:
        body = bytearray()
        for chunk in response.iter_bytes():
            body += chunk
            if len(body) > _MAX_PAGE_BYTES:
                raise LinkError("That page is too big to read.")
            if self._clock() > deadline:
                raise LinkError("That page took too long to load.")
        return bytes(body)


@dataclass(frozen=True)
class _DedicatedReader:
    label: str
    read: Callable[[HttpLinkReader, str], LinkFacts]


_DEDICATED_READERS = {"boardgamegeek.com": _DedicatedReader(_BGG_LABEL, HttpLinkReader.read_bgg)}

DEDICATED_DOMAINS = {domain: reader.label for domain, reader in _DEDICATED_READERS.items()}
"""Domains with their own reader, and the reader's name."""


@dataclass(frozen=True)
class _Page:
    url: str
    html: str


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
    image_url = urljoin(url, image) if image else None
    return LinkFacts(
        source=source,
        name=optional_text(product.get("name")),
        image_url=_web_url(image_url),
        upc=upc if upc and is_upc(upc) else None,
        model_number=_first_text(product, "mpn", "sku"),
        page_text=text[:_MAX_PAGE_TEXT] or None,
    )


def _web_url(url: str | None) -> str | None:
    """`url` if it's an http(s) link, else None."""
    url = optional_text(url)
    return url if url and urlsplit(url).scheme in ("http", "https") else None


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
