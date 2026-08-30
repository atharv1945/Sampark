"""The demo session — one run at a time, over one isolated schema.

Concurrency model, stated once because everything else depends on it:

  * ONE run per process. `start()` raises `RunAlreadyActiveError` (HTTP 409)
    if a run is in flight. A demo is a demo; parallel runs would multiply
    schemas, cleanup paths and determinism surface for no reviewer value.
  * The runner executes on ONE background thread, using ONE connection.
  * Chaos requests arrive on HTTP threads and use a SECOND connection
    (`DemoRunner.chaos_conn`). A psycopg connection is not safe for
    concurrent use, so they never share one. Appending from two connections
    is safe by design — `sampark.audit.chain.append` takes
    `pg_advisory_xact_lock`, which serialises appenders across connections.
  * Each SSE stream opens its OWN short-lived read connection and closes it
    when the client disconnects. Readers never block writers.

Cleanup has four layers, because the Phase 6 incident proved a `finally`
block is not enough (it cannot run against an already-dead connection):

    1. `reset()` drops the schema.
    2. `start()` drops any prior schema before creating a new one.
    3. application shutdown drops the active schema.
    4. application startup sweeps `sampark_demo_%` schemas older than six
       hours — the only layer that recovers from a hard crash.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass
from datetime import date

import psycopg

from sampark.demo import isolation
from sampark.demo.chaos import ChaosControlId, ChaosInapplicableError
from sampark.demo.runner import DemoRunner
from sampark.demo.scenario import DEFAULT_SEED, DemoScenario, build_scenario
from sim.persistence import PostgresConfig


class RunAlreadyActiveError(RuntimeError):
    """A run is already in flight. Mapped to HTTP 409."""


class NoActiveRunError(RuntimeError):
    """No demo schema exists yet. Mapped to HTTP 409."""


@dataclass
class DemoSession:
    config: PostgresConfig

    _lock: threading.RLock = None  # type: ignore[assignment]
    run_id: str | None = None
    schema: str | None = None
    scenario: DemoScenario | None = None
    runner: DemoRunner | None = None
    _thread: threading.Thread | None = None
    _conn: psycopg.Connection | None = None
    _chaos_conn: psycopg.Connection | None = None

    def __post_init__(self) -> None:
        self._lock = threading.RLock()

    # --- connections ---------------------------------------------------

    def _connect(self) -> psycopg.Connection:
        conn = psycopg.connect(self.config.conninfo(), connect_timeout=5)
        conn.autocommit = True
        return conn

    def open_reader(self) -> psycopg.Connection:
        """A fresh read connection pointed at the active demo schema.

        Caller owns its lifetime and MUST close it. Used by the SSE stream
        and by the read endpoints, so neither ever shares a connection with
        the runner thread.
        """
        schema = self.require_schema()
        conn = self._connect()
        isolation.set_search_path(conn, schema)
        return conn

    def require_schema(self) -> str:
        with self._lock:
            if self.schema is None:
                raise NoActiveRunError("no demo run is active - POST /api/run first")
            return self.schema

    # --- lifecycle -----------------------------------------------------

    def start(self, seed: int = DEFAULT_SEED, pace: bool = True) -> dict[str, object]:
        with self._lock:
            if self.is_running():
                raise RunAlreadyActiveError(
                    "a demo run is already in progress - POST /api/reset first"
                )
            self._teardown_locked()

            scenario = build_scenario(seed=seed)
            conn = self._connect()
            schema = isolation.create_demo_schema(conn)
            chaos_conn = self._connect()
            isolation.set_search_path(chaos_conn, schema)

            runner = DemoRunner(
                conn=conn, scenario=scenario, schema=schema, pace=pace, chaos_conn=chaos_conn
            )
            self._conn = conn
            self._chaos_conn = chaos_conn
            self.schema = schema
            self.scenario = scenario
            self.runner = runner
            self.run_id = uuid.uuid4().hex[:12]

            runner.prepare()

            thread = threading.Thread(target=runner.run, name="sampark-demo-runner", daemon=True)
            self._thread = thread
            thread.start()

            return {
                "run_id": self.run_id,
                "demo_schema": schema,
                "seed": scenario.seed,
                "first_window": scenario.first_window.isoformat(),
                "last_window": scenario.last_window.isoformat(),
                "window_count": len(scenario.windows),
                "customer_count": len(scenario.customer_ids),
                "risk_item_count": len(scenario.ledger.risk_items),
                "honest_action_count": len(scenario.honest_actions),
                "rogue_request_count": len(scenario.rogue_requests),
                "compression_ratio_s_per_sim_hour": scenario.clock.compression_ratio_s_per_sim_hour,
                "badge_text": scenario.clock.badge_text(),
                "wall_seconds_budget": scenario.clock.wall_seconds_budget,
            }

    def is_running(self) -> bool:
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    def reset(self) -> dict[str, object]:
        with self._lock:
            dropped = self.schema
            self._teardown_locked()
            return {"dropped_schema": dropped}

    def _teardown_locked(self) -> None:
        """Stop the runner, then drop the schema and close both connections.

        ORDER MATTERS, and getting it wrong caused a real leak. Originally
        this dropped the schema first and left the runner thread alive; the
        thread then kept issuing statements on a connection whose
        `search_path` had been repointed at `public`, and one
        `seed_budget_window` landed a row in `public.budget_windows`.

        Now the thread is asked to stop at its next window boundary and given
        a short grace period to reach it. A window is a transaction boundary,
        so stopping there cannot leave a half-issued grant. If the thread does
        not stop in time we proceed anyway — `drop_demo_schema` leaves the
        connection with an EMPTY search_path, so any further unqualified
        statement fails loudly instead of silently resolving against `public`.
        """
        if self.runner is not None:
            self.runner.request_stop()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=10.0)

        if self._conn is not None and self.schema is not None:
            try:
                isolation.drop_demo_schema(self._conn, self.schema)
            except Exception:
                # The schema may already be gone, or the connection dead.
                # Layer 4 (the startup sweep) is the backstop for both.
                pass
        for conn in (self._chaos_conn, self._conn):
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
        self._conn = None
        self._chaos_conn = None
        self._thread = None
        self.runner = None
        self.scenario = None
        self.schema = None
        self.run_id = None

    # --- chaos ---------------------------------------------------------

    def fire_chaos(self, control_id: ChaosControlId, target: str | None) -> str:
        with self._lock:
            if self.runner is None:
                raise NoActiveRunError("no demo run is active - POST /api/run first")
            return self.runner.fire_chaos(control_id, target)

    # --- status --------------------------------------------------------

    def status(self) -> dict[str, object]:
        with self._lock:
            if self.runner is None or self.scenario is None:
                return {"state": "idle", "run_id": None, "demo_schema": None}
            st = self.runner.status
            current: date | None = st.current_window
            return {
                "state": st.state,
                "run_id": self.run_id,
                "demo_schema": self.schema,
                "seed": self.scenario.seed,
                "window_index": st.window_index,
                "windows_total": st.windows_total,
                "current_window": current.isoformat() if current is not None else None,
                "error": st.error,
                "rollback_count": self.runner.rollback_count,
                "provider_retry_count": self.runner.retry_count,
                "model_degraded": self.runner.degraded,
                "scorer": self.runner.scorer.inner_name,
                "scorer_killed": self.runner.scorer.killed,
                "badge_text": self.scenario.clock.badge_text(),
                "compression_ratio_s_per_sim_hour": (
                    self.scenario.clock.compression_ratio_s_per_sim_hour
                ),
                "thread_alive": self.is_running(),
            }

    def chaos_snapshot(self) -> list[dict[str, object]]:
        with self._lock:
            from sampark.demo.chaos import ChaosState

            state = self.runner.chaos if self.runner is not None else ChaosState()
            return state.snapshot()

    def scenario_brief(self) -> dict[str, object]:
        with self._lock:
            if self.scenario is None:
                return {}
            return {
                "seed": self.scenario.seed,
                "customer_ids": list(self.scenario.customer_ids),
                "windows": [w.isoformat() for w in self.scenario.windows],
                "rogue_requests": [
                    {
                        "label": r.label,
                        "stage": r.stage,
                        "channel": r.channel,
                        "intent": r.intent,
                        "incentive_bps": r.incentive_bps,
                        "issued_at": r.issued_at.isoformat(),
                        "expectation": r.expectation,
                    }
                    for r in self.scenario.rogue_requests
                ],
            }
