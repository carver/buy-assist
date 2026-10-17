import argparse
from pathlib import Path

import httpx2
import uvicorn

from buy_assist.bgg import BggApi
from buy_assist.links import HttpLinkReader
from buy_assist.parser import ClaudeItemParser
from buy_assist.web import create_app


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Buy Assistant web app.")
    parser.add_argument("--db", type=Path, default=Path("data/buy.db"), help="SQLite file")
    parser.add_argument("--host", default="0.0.0.0", help="address to listen on")
    parser.add_argument("--port", type=int, default=6767)
    args = parser.parse_args()
    args.db.parent.mkdir(parents=True, exist_ok=True)
    client = httpx2.Client(timeout=20)
    links = HttpLinkReader(client, BggApi(client))
    app = create_app(args.db, ClaudeItemParser(), links)
    uvicorn.run(app, host=args.host, port=args.port)
