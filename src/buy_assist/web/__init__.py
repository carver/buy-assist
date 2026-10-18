"""The household web app: pick who you are, add items, track what they sell for."""

import logging
import threading
from collections.abc import AsyncGenerator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from buy_assist.store import Store
from buy_assist.watch import Watch
from buy_assist.web import feedback, items, sales, settings
from buy_assist.web.pages import PACKAGE_DIR, NoIdentity, Services, ask_identity

__all__ = ["Services", "create_app"]

_log = logging.getLogger(__name__)


def create_app(
    db_path: Path, services: Services, *, watch_every: timedelta | None = None
) -> FastAPI:
    """The app. With `watch_every`, it also runs the watch (fetches, alerts, digest) on that
    cycle for as long as it's up, starting straight away."""
    store = Store.open(db_path)
    lifespan = _watching(store, services, watch_every) if watch_every else None
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.store = store
    app.state.services = services
    app.mount("/static", StaticFiles(directory=PACKAGE_DIR / "static"), name="static")
    app.add_exception_handler(NoIdentity, ask_identity)
    for module in (items, sales, settings, feedback):
        app.include_router(module.router)
    return app


def _watching(
    store: Store, services: Services, every: timedelta
) -> Callable[[FastAPI], AbstractAsyncContextManager[None]]:
    watch = Watch(
        store,
        listing_sources=services.listing_sources,
        judge=services.judge,
        sold_prices=services.sold_prices,
        shipping=services.shipping,
        mailer=services.mailer,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        stop = threading.Event()
        thread = threading.Thread(
            target=_run_watch, args=(watch, every, stop), name="watch", daemon=True
        )
        thread.start()
        try:
            yield
        finally:
            stop.set()
            thread.join(timeout=10)

    return lifespan


def _run_watch(watch: Watch, every: timedelta, stop: threading.Event) -> None:
    while not stop.is_set():
        try:
            watch.tick(datetime.now(UTC))
        except Exception:
            _log.exception("Watch tick failed")
        stop.wait(every.total_seconds())
