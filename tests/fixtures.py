"""The test fixture item from the README: an EXIT advent calendar on BoardGameGeek."""

from pathlib import Path

import httpx2

from buy_assist.bgg import BggApi
from buy_assist.links import HttpLinkReader, LinkFacts

BGG_URL = (
    "https://boardgamegeek.com/boardgame/343322/exit-the-game-advent-calendar-the-hunt-for-the-gol"
)
EXIT_NAME = "EXIT: The Game Advent Calendar: The Hunt for the Golden Book"

DATA = Path(__file__).parent / "data"


def recorded_bgg_api(request: httpx2.Request) -> httpx2.Response:
    """BGG's JSON API as recorded for game 343322."""
    if request.url.path == "/api/geekitems":
        return httpx2.Response(200, content=(DATA / "bgg_thing_343322.json").read_bytes())
    if request.url.path == "/api/geekitem/linkeditems":
        return httpx2.Response(200, content=(DATA / "bgg_versions_343322.json").read_bytes())
    if request.url.path == "/api/market/products/pricehistory":
        return httpx2.Response(200, content=(DATA / "bgg_pricehistory_343322.json").read_bytes())
    return httpx2.Response(404)


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def recorded_bgg_facts() -> LinkFacts:
    client = httpx2.Client(transport=httpx2.MockTransport(recorded_bgg_api))
    bgg = BggApi(client, sleep=lambda seconds: None)
    return HttpLinkReader(client, bgg).read(BGG_URL)
