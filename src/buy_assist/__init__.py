import argparse
from pathlib import Path

import uvicorn

from buy_assist.parser import ClaudeItemParser
from buy_assist.web import create_app


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Buy Assistant web app.")
    parser.add_argument("--db", type=Path, default=Path("data/buy.db"), help="SQLite file")
    parser.add_argument("--host", default="0.0.0.0", help="address to listen on")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    args.db.parent.mkdir(parents=True, exist_ok=True)
    uvicorn.run(create_app(args.db, ClaudeItemParser()), host=args.host, port=args.port)
