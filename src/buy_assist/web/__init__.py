"""The household web app: pick who you are, add items, track what they sell for."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from buy_assist.store import Store
from buy_assist.web import items, sales, settings
from buy_assist.web.pages import PACKAGE_DIR, NoIdentity, Services, ask_identity

__all__ = ["Services", "create_app"]


def create_app(db_path: Path, services: Services) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store = Store.open(db_path)
    app.state.services = services
    app.mount("/static", StaticFiles(directory=PACKAGE_DIR / "static"), name="static")
    app.add_exception_handler(NoIdentity, ask_identity)
    for module in (items, sales, settings):
        app.include_router(module.router)
    return app
