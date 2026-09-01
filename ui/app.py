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
from ui.routes_razorpay import router as razorpay_router
from ui.razorpay_session import RazorpayProductSession
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
        with contextlib.suppress(Exception):
            app.state.razorpay_session.reset()


def create_app(config: PostgresConfig | None = None) -> FastAPI:
    app = FastAPI(
        title="SAMPARK - revenue recovery control center",
        description=(
            "Two surfaces over one system. `/` is the Razorpay Test Mode product demo: "
            "one real test-mode payment failure through the mediation layer. `/system` is "
            "the Phase 8 synthetic replay: contention, failure and recovery at scale. Both "
            "render the hash-chained audit log and nothing else (spec §12.1). Local "
            "demonstration console: no authentication, bound to localhost, and structurally "
            "unable to write outside its throwaway schema."
        ),
        version="9.1",
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
    app.state.razorpay_session = RazorpayProductSession(config=config)
    app.include_router(router)
    app.include_router(razorpay_router)

    if STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

        # `/` is the PRODUCT surface: the Razorpay Test Mode flow, plus the
        # link across to the system demo. `/system` is Phase 8's screen,
        # served from the unchanged `index.html` — the file
        # `tests/test_ui_renders_only_audit_events.py` reads and asserts
        # against, so moving its ROUTE changes no invariant it enforces.
        @app.get("/", include_in_schema=False)
        def product() -> FileResponse:
            return FileResponse(str(STATIC_DIR / "product.html"))

        @app.get("/system", include_in_schema=False)
        def system_trace() -> FileResponse:
            return FileResponse(str(STATIC_DIR / "index.html"))

    return app


app = create_app()
