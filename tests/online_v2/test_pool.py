"""P6-A bounded reuse, transaction safety, lifecycle and sanitized observations."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
import logging
import time

import pytest
from fastapi.testclient import TestClient
from ptcg_online_server.api import create_app
from ptcg_online_server.config import Settings
from ptcg_online_server.errors import ServiceError
from ptcg_online_server.metrics import Metrics
from ptcg_online_server.repository import Repository


@pytest.mark.parametrize("enabled", [False, True])
def test_pool_reuse_rollback_and_idle_transactions(repo, enabled):
    with Repository(
        replace(repo.settings, db_pool_enabled=enabled, db_pool_size=1)
    ) as pooled:
        pids = []
        for _ in range(3):
            with pooled.transaction() as db:
                pids.append(
                    db.execute("SELECT pg_backend_pid() AS pid").fetchone()["pid"]
                )
        assert (len(set(pids)) == 1) is enabled
        with pytest.raises(RuntimeError):
            with pooled.transaction() as db:
                db.execute(
                    "INSERT INTO ptcg_v2.rate_buckets VALUES('rollback-canary',1,now()+interval '60 seconds')"
                )
                raise RuntimeError("rollback")
        with pooled.transaction() as db:
            assert (
                db.execute(
                    "SELECT count(*) AS n FROM ptcg_v2.rate_buckets WHERE key='rollback-canary'"
                ).fetchone()["n"]
                == 0
            )
        with repo.db() as db:
            assert (
                db.execute(
                    "SELECT count(*) AS n FROM pg_stat_activity WHERE application_name='ptcg-online-v2' AND pid <> pg_backend_pid() AND state LIKE 'idle in transaction%'"
                ).fetchone()["n"]
                == 0
            )
        assert pooled.diagnostics()["metrics"]["db.connect"]["count"] == (
            1 if enabled else 5
        )
    with pytest.raises(ServiceError, match="INTERNAL_ERROR"):
        with pooled.transaction():
            pass


def test_pool_capacity_wait_timeout_and_shutdown_wakes_waiter(repo):
    with Repository(
        replace(repo.settings, db_pool_size=1, db_pool_waiters=1, db_pool_timeout=0.3)
    ) as pooled:
        with ThreadPoolExecutor(max_workers=2) as threads:

            def query():
                with pooled.transaction():
                    pass

            with pooled.transaction():
                first = threads.submit(query)
                deadline = time.monotonic() + 2
                while (
                    pooled.pool.get_stats()["requests_waiting"] < 1
                    and time.monotonic() < deadline
                ):
                    time.sleep(0.005)
                assert pooled.pool.get_stats()["requests_waiting"] == 1
                start = time.monotonic()
                with pytest.raises(ServiceError, match="CAPACITY_EXCEEDED") as refused:
                    query()
                assert refused.value.status == 503 and refused.value.retryable
                assert time.monotonic() - start < 0.2
                with pytest.raises(ServiceError, match="CAPACITY_EXCEEDED"):
                    first.result(timeout=2)
                # A queued caller wakes on close, without waiting its full deadline.
                again = threads.submit(query)
                deadline = time.monotonic() + 2
                while (
                    pooled.pool.get_stats()["requests_waiting"] < 1
                    and time.monotonic() < deadline
                ):
                    time.sleep(0.005)
                pooled.close()
                with pytest.raises(ServiceError, match="INTERNAL_ERROR"):
                    again.result(timeout=2)
        assert pooled.pool.get_stats()["pool_size"] <= 1


def test_broken_connection_is_not_reused_and_failed_transaction_cannot_commit(repo):
    with Repository(replace(repo.settings, db_pool_size=1)) as pooled:
        with pytest.raises(ServiceError, match="INTERNAL_ERROR"):
            with pooled.transaction() as db:
                pid = db.execute("SELECT pg_backend_pid() AS pid").fetchone()["pid"]
                db.execute(
                    "INSERT INTO ptcg_v2.rate_buckets VALUES('dead-connection',1,now()+interval '60 seconds')"
                )
                with repo.db() as killer:
                    killer.execute("SELECT pg_terminate_backend(%s)", (pid,))
                db.execute("SELECT 1")
        with pooled.transaction() as db:
            assert db.execute("SELECT pg_backend_pid() AS pid").fetchone()["pid"] != pid
            assert (
                db.execute(
                    "SELECT count(*) AS n FROM ptcg_v2.rate_buckets WHERE key='dead-connection'"
                ).fetchone()["n"]
                == 0
            )


def test_lifespan_owns_pool_and_diagnostics_are_not_public(repo):
    app = create_app(repo.settings, background=False)
    owned = app.state.repository
    assert owned.pool is None
    with TestClient(app, base_url=repo.settings.public_origin) as client:
        assert owned.pool is not None and not owned.pool.closed
        assert client.get("/readyz").json()["database"] is True
        assert client.get("/metrics").status_code == 404
    assert owned.closed and owned.pool.closed


def test_metrics_are_fixed_bounded_copied_and_threadsafe():
    metrics = Metrics()
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: metrics.observe("db.sql", 0.001), range(1000)))
    value = metrics.snapshot()
    assert value["metrics"]["db.sql"]["count"] == 1000
    assert sum(value["metrics"]["db.sql"]["buckets"]) == 1000
    value["metrics"]["db.sql"]["buckets"][0] = -1
    assert metrics.snapshot()["metrics"]["db.sql"]["buckets"][0] == 1000
    with pytest.raises(ValueError, match="UNKNOWN_METRIC"):
        metrics.observe("secret-match-id")
    assert "secret-match-id" not in json.dumps(metrics.snapshot())


def test_pool_logs_and_metrics_do_not_contain_queries_or_secrets(repo, caplog):
    with Repository(repo.settings) as pooled:
        with caplog.at_level(logging.WARNING):
            logging.getLogger("psycopg.pool").warning("failed %s", "secret-password")
        assert "secret-password" not in caplog.text
        with pooled.transaction() as db:
            db.execute("SELECT %s AS private_value", ("private-hand-canary",))
        wire = json.dumps(pooled.diagnostics())
        assert "private-hand-canary" not in wire and "SELECT" not in wire
        assert repo.settings.database_url not in wire


@pytest.mark.parametrize(
    "field,value",
    [
        ("db_pool_enabled", 1),
        ("db_pool_size", 0),
        ("db_pool_size", 33),
        ("db_pool_waiters", 0),
        ("db_pool_waiters", 257),
        ("db_pool_timeout", 0),
        ("db_pool_timeout", float("nan")),
        ("db_pool_timeout", True),
        ("db_pool_timeout", 6),
    ],
)
def test_pool_budgets_reject_unbounded_and_invalid_values(field, value):
    with pytest.raises(ValueError, match="INVALID_DATABASE_BUDGET"):
        Settings(
            "postgresql://unused", "pool-test", "https://example.test", **{field: value}
        )


def test_pool_environment_has_explicit_rollback_switch(monkeypatch):
    monkeypatch.delenv("PTCG_ONLINE_V2_DATABASE_URL_FILE", raising=False)
    monkeypatch.setenv("PTCG_ONLINE_V2_DATABASE_URL", "postgresql://unused")
    monkeypatch.setenv("PTCG_ONLINE_V2_SERVER_ID", "pool-test")
    monkeypatch.setenv("PTCG_ONLINE_V2_PUBLIC_ORIGIN", "https://example.test")
    monkeypatch.setenv("PTCG_ONLINE_V2_DB_POOL_ENABLED", "0")
    assert Settings.from_env().db_pool_enabled is False
    monkeypatch.setenv("PTCG_ONLINE_V2_DB_POOL_ENABLED", "maybe")
    with pytest.raises(ValueError):
        Settings.from_env()
