"""Portal API 的 FastAPI 应用入口。"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from deep_agent.core.config import AppSettings, get_settings
from deep_agent.core.runtime_logging import configure_logging_from_env
from deep_agent.portal.events import PortalEventHub
from deep_agent.portal.routers import router
from deep_agent.portal.runner import PortalRunner
from deep_agent.portal.store import PortalStore


RunnerFactory = Callable[[AppSettings, PortalStore, PortalEventHub], Any]


@dataclass(slots=True)
class PortalAppState:
    settings: AppSettings
    store: PortalStore
    hub: PortalEventHub
    runner: Any


def default_store_path() -> Path:
    """返回 Portal JSON 持久化的默认运行时路径。"""

    return Path(__file__).resolve().parents[2] / "runtime" / "portal" / "sessions.json"


def create_app(
    *,
    settings: AppSettings | None = None,
    store_path: Path | None = None,
    runner_factory: RunnerFactory | None = None,
) -> FastAPI:
    """创建 Portal FastAPI 应用，并为测试保留可注入依赖。"""

    configure_logging_from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        resolved_settings = settings or get_settings()
        store = PortalStore(store_path or default_store_path())
        hub = PortalEventHub()
        runner = (
            runner_factory(resolved_settings, store, hub)
            if runner_factory is not None
            else PortalRunner(settings=resolved_settings, store=store, hub=hub)
        )
        app.state.portal = PortalAppState(settings=resolved_settings, store=store, hub=hub, runner=runner)
        try:
            yield
        finally:
            shutdown = getattr(runner, "shutdown", None)
            if callable(shutdown):
                await shutdown()

    app = FastAPI(title="Web AutoTest Portal API", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router, prefix="/api/portal", tags=["portal"])
    return app


app = create_app()
