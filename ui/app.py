"""FastAPI application — `uvicorn ui.app:app`.

Lifespan does two things that matter:

  * STARTUP sweeps stale `sampark_demo_%` schemas (older than six hours).
    This is cleanup layer 4, and the only one that recovers from a hard
    crash — the Phase 6 incident left 399 orphaned rows precisely because a
    `finally` block could not run against an already-dead connection.
  * SHUTDOWN drops the active demo schema.

Neither ever touches `public`: `sampark.demo.isolation` refuses any name
that is not `sampark_demo_<unix_ts>_<16 hex>`.
"""

from __future__ import annotations

import contextlib
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from sampark.demo import isolation
from sim.persistence import PostgresConfig, PostgresConfigError
from ui.routes import router
from ui.session import DemoSession

STATIC_DIR = Path(__file__).resolve().parent / "static"


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    session: DemoSession = app.state.session
    try:
        conn = session._connect()
    except Exception as exc:  # pragma: no cover - environment failure
        print("SAMPARK demo: Postgres unreachable at startup: " + str(exc), file=sys.stderr)
        conn = None
    if conn is not None:
        try:
            swept = isolation.sweep_stale(conn)
            if swept:
                print("SAMPARK demo: swept stale demo schemas: " + ", ".join(swept))
            count, _head = isolation.public_audit_fingerprint(conn)
            print("SAMPARK demo: protected public.audit_events has " + str(count) + " events (read-only)")
        finally:
            conn.close()
    try:
        yield
    finally:
        with contextlib.suppress(Exception):
            session.reset()


def create_app(config: PostgresConfig | None = None) -> FastAPI:
    app = FastAPI(
        title="SAMPARK - live trace",
        description=(
            "Phase 8 demo surface. Renders the hash-chained audit log and nothing else "
            "(spec §12.1). Local demonstration console: no authentication, bound to "
            "localhost, and structurally unable to write outside its throwaway schema."
        ),
        version="8.0",
        lifespan=lifespan,
    )
    if config is None:
        try:
            config = PostgresConfig.from_env()
        except PostgresConfigError as exc:  # pragma: no cover - environment failure
            raise RuntimeError(
                "SAMPARK demo needs Postgres env vars (POSTGRES_HOST/PORT/DB/USER/PASSWORD): " + str(exc)
            )
    app.state.session = DemoSession(config=config)
    app.include_router(router)

    if STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

        @app.get("/", include_in_schema=False)
        def index() -> FileResponse:
            return FileResponse(str(STATIC_DIR / "index.html"))

    return app


app = create_app()
