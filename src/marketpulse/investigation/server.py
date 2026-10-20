"""Composition root for the Investigation Console API.

This module intentionally does not reuse the legacy MarketPulse HTTP app.  It
owns the Investigation database migration, repository/session lifetime, and
the two Phase 5 routers required by the local console.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncIterator
from contextlib import ExitStack, asynccontextmanager
from pathlib import Path

import uvicorn
from alembic import command
from alembic.config import Config
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import Engine, text

from marketpulse.config import Settings
from marketpulse.investigation.api import ReplayCaseRunner
from marketpulse.investigation.api import router as investigation_router
from marketpulse.investigation.live_runtime import LiveInvestigationService, LivePorts
from marketpulse.investigation.operations.ownership import ServerOwnership
from marketpulse.investigation.operations.watchdog import RecoveryWatchdog
from marketpulse.investigation.persistence.base import (
    create_investigation_engine,
    create_session_factory,
)
from marketpulse.investigation.persistence.repositories import InvestigationRepository
from marketpulse.investigation.review.api import router as review_router

LOGGER = logging.getLogger("marketpulse.investigation.server")


def _alembic_config(database_url: str) -> Config:
    source_root = Path(__file__).resolve().parents[3]
    source_config = source_root / "alembic.ini"
    if source_config.is_file():
        config = Config(source_config)
    else:
        packaged_root = Path(__file__).resolve().parent / "persistence"
        config = Config(packaged_root / "alembic.ini")
    config.attributes["database_url"] = database_url
    return config


def _upgrade_database(database_url: str) -> None:
    command.upgrade(_alembic_config(database_url), "head")


def create_app(
    *,
    settings: Settings | None = None,
    east_palestine_replay: ReplayCaseRunner | None = None,
    live_ports: LivePorts | None = None,
) -> FastAPI:
    runtime_settings = settings or Settings.from_env(require_api_key=False)
    quant_source_root = Path(__file__).resolve().parents[3]
    database_url = runtime_settings.database_url.get_secret_value()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_investigation_engine(database_url)
        with ExitStack() as resources:
            resources.callback(engine.dispose)
            ownership = ServerOwnership(engine, database_url)
            ownership.acquire()
            resources.callback(ownership.release)
            _upgrade_database(database_url)
            app.state.server_ownership = ownership
            sessions = create_session_factory(engine)
            repository = InvestigationRepository(sessions)
            app.state.inv_engine = engine
            app.state.inv_sessions = sessions
            app.state.inv_repository = repository
            app.state.review_session_factory = sessions
            app.state.settings = runtime_settings
            live_runner = LiveInvestigationService(
                sessions=sessions,
                repository=repository,
                settings=runtime_settings,
                blob_root=Path(os.getenv("INVESTIGATION_BLOB_ROOT", "data/investigation-blobs")),
                ports=live_ports,
            )
            from marketpulse.quant.api import PublicQuantRuntime, missing_dependencies

            app.state.quant_runtime = None
            app.state.quant_missing_dependencies = missing_dependencies()
            if not app.state.quant_missing_dependencies:
                from marketpulse.quant.execution.service import QuantService
                from marketpulse.quant.storage.snapshots import SnapshotStore

                quant_service = QuantService(
                    SnapshotStore(sessions, live_runner.blobs), root=quant_source_root
                )
                live_runner.quant_service = quant_service
                app.state.quant_runtime = PublicQuantRuntime(
                    quant_service,
                    live_runner,
                    Path(os.getenv("QUANT_CALL_JOURNAL_ROOT", "data/quant-call-journal")),
                )
            from marketpulse.quant.live.store import ResearchStore

            research_root = Path(
                os.getenv("QUANT_CALL_JOURNAL_ROOT", "data/quant-call-journal")
            ).parent
            app.state.research_store = ResearchStore(
                sessions, live_runner.blobs, research_root / "live-research"
            )
            from marketpulse.quant.knowledge.store import KnowledgeStore

            app.state.knowledge_store = KnowledgeStore(sessions, live_runner.blobs)
            from marketpulse.quant.knowledge.rule_store import RuleStore

            app.state.knowledge_rules = RuleStore(app.state.knowledge_store)
            app.state.research_jobs = {}
            from marketpulse.quant.decision.next_session.service import (
                recover as recover_predictions,
            )
            from marketpulse.quant.decision.next_session.store import ExperimentStore

            recover_predictions(ExperimentStore(app.state.research_store))
            app.state.research_store.interrupt_abandoned()
            live_runner.recover_interrupted()
            app.state.live_investigation = live_runner
            replay_runner = east_palestine_replay
            if replay_runner is None:
                from marketpulse.investigation.case_replay import (
                    EastPalestineReplayService,
                    default_blob_root,
                    default_case_root,
                )

                replay_runner = EastPalestineReplayService(
                    sessions=sessions,
                    repository=repository,
                    case_root=default_case_root(),
                    blob_root=default_blob_root(),
                )
            app.state.east_palestine_replay = replay_runner
            watchdog = RecoveryWatchdog(live_runner, ownership)
            app.state.recovery_watchdog = watchdog
            watchdog.start()
            daily = None
            if os.getenv("ALPHAAGENT_DAILY_ENABLED", "false").lower() == "true":
                from marketpulse.quant.daily.runtime import DailyRuntime

                daily = DailyRuntime(app)
                app.state.daily_runtime = daily
                daily.start()
            try:
                yield
            finally:
                if daily:
                    await daily.close()
                for task in list(app.state.research_jobs.values()):
                    task.cancel()
                if app.state.research_jobs:
                    await asyncio.gather(
                        *list(app.state.research_jobs.values()), return_exceptions=True
                    )
                await watchdog.close()
                await live_runner.shutdown()

    app = FastAPI(
        title="Search Report Investigation API",
        version="0.1.0",
        lifespan=lifespan,
    )
    if runtime_settings.review_allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(runtime_settings.review_allowed_origins),
            allow_credentials=True,
            allow_methods=["GET", "POST", "DELETE"],
            allow_headers=["Content-Type", "Idempotency-Key", "X-Requested-With"],
        )
    app.include_router(investigation_router)
    from marketpulse.quant.api import router as quant_router

    app.include_router(quant_router)
    from marketpulse.quant.live.research_api import compat_router
    from marketpulse.quant.live.research_api import router as research_router

    app.include_router(research_router)
    app.include_router(compat_router)
    from marketpulse.quant.completion import router as completion_router

    app.include_router(completion_router)
    from marketpulse.quant.decision.api import router as decision_router

    app.include_router(decision_router)
    from marketpulse.quant.knowledge.api import router as knowledge_router

    app.include_router(knowledge_router)
    from marketpulse.quant.knowledge.planning_api import router as investment_router

    app.include_router(investment_router)
    from marketpulse.quant.knowledge.research_api import router as investment_research_router

    app.include_router(investment_research_router)
    from marketpulse.quant.knowledge.advisor_api import router as holding_advisor_router

    app.include_router(holding_advisor_router)
    from marketpulse.quant.knowledge.portfolio_api import router as portfolio_advisor_router

    app.include_router(portfolio_advisor_router)

    from marketpulse.quant.knowledge.question_api import router as question_router

    app.include_router(question_router)

    from marketpulse.quant.knowledge.paired_evaluation_api import router as evaluation_router

    app.include_router(evaluation_router)
    from marketpulse.quant.daily.api import router as daily_router

    app.include_router(daily_router)
    app.include_router(review_router)

    @app.get("/api/health")
    def health(request: Request) -> dict[str, str]:
        if (
            not request.app.state.server_ownership.healthy()
            or not request.app.state.recovery_watchdog.healthy()
        ):
            raise HTTPException(status_code=503, detail="recovery supervisor is unhealthy")
        engine = request.app.state.inv_engine
        assert isinstance(engine, Engine)
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return {
            "status": "ok",
            "service": "search-report-investigation",
            "database": "ok",
        }

    @app.get("/api/ops/status")
    def operations_status(request: Request) -> dict[str, object]:
        return dict(request.app.state.recovery_watchdog.status())

    if os.getenv("INVESTIGATION_SERVE_FRONTEND", "false").lower() in {
        "1",
        "true",
        "yes",
    }:
        frontend_root = Path(__file__).resolve().parents[3] / "frontend" / "dist"
        if frontend_root.is_dir():
            @app.middleware("http")
            async def revalidate_frontend_entry(request: Request, call_next):
                response = await call_next(request)
                if request.url.path in {"/", "/index.html"}:
                    response.headers["Cache-Control"] = "no-cache"
                return response

            app.mount("/", StaticFiles(directory=frontend_root, html=True), name="console")

    return app


app = create_app()


def main() -> None:
    logging.basicConfig(level=os.getenv("MARKETPULSE_LOG_LEVEL", "INFO").upper())
    uvicorn.run(
        "marketpulse.investigation.server:app",
        host=os.getenv("INVESTIGATION_API_HOST", "127.0.0.1"),
        port=int(os.getenv("INVESTIGATION_API_PORT", "8000")),
        reload=False,
        workers=1,
    )


if __name__ == "__main__":
    main()
