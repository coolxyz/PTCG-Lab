"""PostgreSQL transactions; no implicit schema installation or legacy DB adapter."""

from contextlib import contextmanager, ExitStack
from hashlib import sha256
from importlib.resources import files
import logging

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import (
    ConnectionPool,
    NullConnectionPool,
    PoolClosed,
    PoolTimeout,
    TooManyRequests,
)

from .metrics import Metrics

from .errors import ServiceError

# Global locks are deliberately limited to schema migration, guest admission and
# bounded rate accounting. Future match commands use per-match transactions.
MIGRATION_LOCK = 74040100
ADMISSION_LOCK = 74040101
RATE_LOCK = 74040102


def migrations():
    return [
        (
            version,
            files("ptcg_online_server").joinpath("migrations/" + name).read_bytes(),
        )
        for version, name in (
            (1, "001_sessions.sql"),
            (2, "002_lobby.sql"),
            (3, "003_matches.sql"),
        )
    ]


class PoolLogFilter(logging.Filter):
    def filter(self, record):
        # Driver/pool exceptions can contain server diagnostics. Aggregate metrics
        # retain failure counts; ordinary logs must not expose DSNs or SQL values.
        record.msg, record.args = "database_driver_event", ()
        record.exc_info = record.exc_text = record.stack_info = None
        return True


class Repository:
    def __init__(self, settings):
        self.settings = settings
        self.metrics = Metrics()
        self.pool = None
        self.closed = False
        self.stream_wakeup = None
        metrics = self.metrics

        class ObservedConnection(psycopg.Connection):
            @classmethod
            def connect(cls, *args, **kwargs):
                with metrics.time("db.connect"):
                    return super().connect(*args, **kwargs)

            def execute(self, *args, **kwargs):
                with metrics.time("db.sql"):
                    return super().execute(*args, **kwargs)

        self.connection_class = ObservedConnection
        self.connection_kwargs = {
            "row_factory": dict_row,
            "connect_timeout": 3,
            "application_name": "ptcg-online-v2",
            "options": "-c statement_timeout=5000 -c lock_timeout=2000 -c idle_in_transaction_session_timeout=5000",
        }

    def open(self):
        """Explicit API/benchmark lifecycle; construction performs no DB I/O."""
        if self.closed:
            raise ServiceError("INTERNAL_ERROR", 503, retryable=True)
        if self.pool is not None:
            return
        for name in ("psycopg", "psycopg.pool"):
            logger = logging.getLogger(name)
            if not any(isinstance(f, PoolLogFilter) for f in logger.filters):
                logger.addFilter(PoolLogFilter())
        factory = (
            ConnectionPool if self.settings.db_pool_enabled else NullConnectionPool
        )
        self.pool = factory(
            self.settings.database_url,
            connection_class=self.connection_class,
            kwargs=self.connection_kwargs,
            min_size=0,
            max_size=self.settings.db_pool_size,
            max_waiting=self.settings.db_pool_waiters,
            timeout=self.settings.db_pool_timeout,
            max_idle=30,
            reconnect_timeout=5,
            num_workers=1,
            name="ptcg-api",
            open=False,
        )
        self.pool.open()

    def close(self):
        self.closed = True
        if self.pool is not None:
            self.pool.close()

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *_):
        self.close()

    def diagnostics(self):
        # Fixed field names only. No connection string, query text or identities.
        return {
            **self.metrics.snapshot(),
            "pool": self.pool.get_stats() if self.pool else {},
        }

    @contextmanager
    def db(self):
        if self.closed:
            raise ServiceError("INTERNAL_ERROR", 503, retryable=True)
        try:
            with ExitStack() as stack:
                with self.metrics.time("db.acquire"):
                    db = stack.enter_context(
                        self.pool.connection()
                        if self.pool is not None
                        else self.connection_class.connect(
                            self.settings.database_url, **self.connection_kwargs
                        )
                    )
                with self.metrics.time("db.transaction"):
                    # Commit/rollback before returning the connection to the pool.
                    # Rule computation and queue waits remain outside this scope.
                    db.stream_changed = False
                    try:
                        with db.transaction():
                            yield db
                        changed = db.stream_changed
                    finally:
                        db.stream_changed = False
            # Only after a successful commit and connection release. A lost hint
            # is harmless: the stream hub periodically checks durable heads.
            if changed and self.stream_wakeup is not None:
                self.stream_wakeup()
        except (PoolTimeout, TooManyRequests):
            self.metrics.observe("db.rejected")
            raise ServiceError("CAPACITY_EXCEEDED", 503, retryable=True) from None
        except (psycopg.Error, PoolClosed):
            self.metrics.observe("db.error")
            raise ServiceError("INTERNAL_ERROR", 503, retryable=True) from None

    def check_schema(self, db):
        try:
            deployment = db.execute(
                "SELECT server_id FROM ptcg_v2.deployment WHERE singleton"
            ).fetchone()
            actual = db.execute(
                "SELECT version, checksum FROM ptcg_v2.schema_migrations ORDER BY version"
            ).fetchall()
        except psycopg.Error:
            raise ServiceError("INTERNAL_ERROR", 503, retryable=True) from None
        expected = [
            {"version": v, "checksum": sha256(sql).hexdigest()}
            for v, sql in migrations()
        ]
        if (
            not deployment
            or deployment["server_id"] != self.settings.server_id
            or actual != expected
        ):
            raise ServiceError("INTERNAL_ERROR", 503)

    @contextmanager
    def transaction(self):
        with self.db() as db:
            self.check_schema(db)
            yield db

    def healthy(self):
        with self.transaction():
            return True

    def migrate(self):
        """Explicit command only; refuse legacy tables and modified applied SQL."""
        with self.db() as db:
            db.execute("SELECT pg_advisory_xact_lock(%s)", (MIGRATION_LOCK,))
            foreign = db.execute(
                """SELECT 1 FROM information_schema.tables
                WHERE table_schema NOT IN ('pg_catalog', 'information_schema', 'ptcg_v2') LIMIT 1"""
            ).fetchone()
            if foreign:
                raise ServiceError("FORBIDDEN", 409)
            exists = db.execute(
                "SELECT to_regclass('ptcg_v2.schema_migrations') AS table_name"
            ).fetchone()["table_name"]
            if not exists:
                # CREATE SCHEMA intentionally fails if an unrecognized partial deployment exists.
                db.execute("CREATE SCHEMA ptcg_v2")
                db.execute(
                    "CREATE TABLE ptcg_v2.deployment (singleton boolean PRIMARY KEY CHECK(singleton), server_id text NOT NULL)"
                )
                db.execute(
                    "INSERT INTO ptcg_v2.deployment VALUES(true,%s)",
                    (self.settings.server_id,),
                )
                db.execute(
                    "CREATE TABLE ptcg_v2.schema_migrations (version integer PRIMARY KEY, checksum text NOT NULL, applied_at timestamptz NOT NULL DEFAULT now())"
                )
            deployment = db.execute(
                "SELECT server_id FROM ptcg_v2.deployment WHERE singleton"
            ).fetchone()
            if not deployment or deployment["server_id"] != self.settings.server_id:
                raise ServiceError("FORBIDDEN", 409)
            applied = {
                r["version"]: r["checksum"]
                for r in db.execute(
                    "SELECT version,checksum FROM ptcg_v2.schema_migrations"
                ).fetchall()
            }
            known = {v for v, _ in migrations()}
            if set(applied) - known:
                raise ServiceError("FORBIDDEN", 409)
            for version, sql in migrations():
                checksum = sha256(sql).hexdigest()
                if version in applied:
                    if applied[version] != checksum:
                        raise ServiceError("FORBIDDEN", 409)
                    continue
                db.execute(sql.decode())
                db.execute(
                    "INSERT INTO ptcg_v2.schema_migrations(version,checksum) VALUES(%s,%s)",
                    (version, checksum),
                )
            self.check_schema(db)

    def rate(self, peer, *, creating=False, joining=False, replaying=False):
        """Durable, bounded fixed windows; forwarded addresses are not trusted."""
        settings = self.settings
        digest = sha256((settings.server_id + ":" + peer).encode()).hexdigest()
        limits = [
            ("global", settings.global_per_minute),
            ("peer:" + digest, settings.requests_per_minute),
        ]
        if replaying:
            limits = [
                ("replay:" + digest, settings.replay_per_minute),
                ("replay-global", settings.replay_global_per_minute),
            ] + limits
        if creating:
            limits.append(("create:" + digest, settings.create_per_minute))
        if joining:
            limits.append(("join:" + digest, settings.join_per_minute))
        denied = False
        with self.transaction() as db:
            db.execute("SELECT pg_advisory_xact_lock(%s)", (RATE_LOCK,))
            db.execute(
                "DELETE FROM ptcg_v2.rate_buckets WHERE key IN (SELECT key FROM ptcg_v2.rate_buckets WHERE reset_at<=now() ORDER BY reset_at,key LIMIT %s)",
                (settings.maintenance_batch,),
            )
            size = db.execute(
                "SELECT count(*) AS n FROM ptcg_v2.rate_buckets"
            ).fetchone()["n"]
            for key, limit in limits:
                row = db.execute(
                    "SELECT count,reset_at>clock_timestamp() AS alive FROM ptcg_v2.rate_buckets WHERE key=%s FOR UPDATE",
                    (key,),
                ).fetchone()
                if (row and row["alive"] and row["count"] >= limit) or (
                    not row and size >= settings.max_rate_keys
                ):
                    denied = True
                    break
                if row:
                    db.execute(
                        """UPDATE ptcg_v2.rate_buckets SET count=CASE WHEN reset_at>clock_timestamp() THEN count+1 ELSE 1 END,
                        reset_at=CASE WHEN reset_at>clock_timestamp() THEN reset_at ELSE now()+interval '60 seconds' END WHERE key=%s""",
                        (key,),
                    )
                else:
                    db.execute(
                        "INSERT INTO ptcg_v2.rate_buckets VALUES(%s,1,now()+interval '60 seconds')",
                        (key,),
                    )
                    size += 1
        if denied:
            raise ServiceError("CAPACITY_EXCEEDED", 429, retryable=True)

    def maintain(self):
        """One bounded cleanup pass; keep accepted commands and replay history."""
        with self.metrics.time("maintenance.run"), self.transaction() as db:
            db.execute(
                "SELECT set_config('statement_timeout',%s,true)",
                (str(max(1, int(self.settings.maintenance_sql_seconds * 1000))),),
            )
            limit = (self.settings.maintenance_batch,)
            db.execute(
                """DELETE FROM ptcg_v2.rate_buckets WHERE key IN (
                SELECT key FROM ptcg_v2.rate_buckets WHERE reset_at<=now()
                ORDER BY reset_at,key FOR UPDATE SKIP LOCKED LIMIT %s)""",
                limit,
            )
            db.execute(
                """DELETE FROM ptcg_v2.access_tokens WHERE digest IN (
                SELECT digest FROM ptcg_v2.access_tokens WHERE expires_at<=now()
                ORDER BY expires_at,digest FOR UPDATE SKIP LOCKED LIMIT %s)""",
                limit,
            )
            db.execute(
                """UPDATE ptcg_v2.rooms SET status='closed',version=version+1
                WHERE id IN (SELECT id FROM ptcg_v2.rooms
                WHERE status='waiting' AND expires_at<=now()
                ORDER BY expires_at,id FOR UPDATE SKIP LOCKED LIMIT %s)""",
                limit,
            )
