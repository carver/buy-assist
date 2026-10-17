"""The test fixture item from the README: an EXIT advent calendar on BoardGameGeek."""

from pathlib import Path

import httpx2

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
    return httpx2.Response(404)


def recorded_bgg_facts() -> LinkFacts:
    client = httpx2.Client(transport=httpx2.MockTransport(recorded_bgg_api))
    return HttpLinkReader(client, sleep=lambda seconds: None).read(BGG_URL)
