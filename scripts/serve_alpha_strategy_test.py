"""Isolated UI acceptance server; synthetic package, never the user evidence DB."""

import argparse
import asyncio
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from marketpulse.config import Settings
from marketpulse.infrastructure.storage.local import LocalContentAddressedBlobStorage
from marketpulse.quant.backtest.catalog import PackageCatalog
from marketpulse.quant.completion import router
from marketpulse.quant.contracts import canonical
from marketpulse.quant.live.research_api import router as research_router
from marketpulse.quant.live.store import ResearchRunRow, ResearchStore

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.unit.quant.test_backtest_package import fixture  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def create(root):
    root = root.resolve()
    if root.exists() or not root.is_relative_to(ROOT / ".phase3-quant-o"):
        raise ValueError("new private acceptance directory required")
    root.mkdir(parents=True)
    package, reviews, sources = fixture()
    installed = root / "catalog" / "synthetic-ui-test-only"
    (installed / "objects").mkdir(parents=True)
    (installed / "package.json").write_text(package.model_dump_json(), encoding="utf-8")
    (installed / "reviews.json").write_text(
        canonical([r.model_dump(mode="json") for r in reviews]), encoding="utf-8"
    )
    for sha, raw in sources.items():
        (installed / "objects" / sha).write_bytes(raw)
    engine = create_engine(
        "sqlite:///" + (root / "test.sqlite").as_posix(), connect_args={"check_same_thread": False}
    )
    ResearchRunRow.__table__.create(engine)

    @asynccontextmanager
    async def lifespan(app):
        yield
        for task in list(app.state.research_jobs.values()):
            task.cancel()
        await asyncio.gather(*app.state.research_jobs.values(), return_exceptions=True)
        engine.dispose()

    app = FastAPI(lifespan=lifespan)
    app.state.research_store = ResearchStore(
        sessionmaker(engine), LocalContentAddressedBlobStorage(root / "blobs"), root / "legacy"
    )
    app.state.research_jobs = {}
    app.state.settings = Settings()
    app.state.research_model_port = object()
    app.state.strategy_catalog = PackageCatalog(root / "catalog")

    @app.get("/api/quant/research/quotes")
    def quotes():
        return dict(quotes=[], retrieved_at=None)

    @app.get("/api/quant/overview")
    def overview():
        return dict(stocks=[], indices=[])

    @app.get("/api/investigations")
    @app.get("/api/quant/runs")
    def empty():
        return []

    app.include_router(router)
    app.include_router(research_router)
    app.mount("/", StaticFiles(directory=ROOT / "frontend/dist", html=True), name="frontend")
    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8841)
    args = parser.parse_args()
    uvicorn.run(create(args.root), host="127.0.0.1", port=args.port, log_level="warning")
