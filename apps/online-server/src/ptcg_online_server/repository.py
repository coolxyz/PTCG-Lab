"""PostgreSQL transactions; no implicit schema installation or legacy DB adapter."""

from contextlib import contextmanager
from hashlib import sha256
from importlib.resources import files

import psycopg
from psycopg.rows import dict_row

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
        for version, name in ((1, "001_sessions.sql"), (2, "002_lobby.sql"))
    ]


class Repository:
    def __init__(self, settings):
        self.settings = settings

    @contextmanager
    def db(self):
        try:
            with psycopg.connect(
                self.settings.database_url,
                row_factory=dict_row,
                connect_timeout=3,
                application_name="ptcg-online-v2",
                options="-c statement_timeout=5000 -c lock_timeout=2000 -c idle_in_transaction_session_timeout=5000",
            ) as db:
                yield db
        except psycopg.Error:
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

    def rate(self, peer, *, creating=False, joining=False):
        """Durable, bounded fixed windows; forwarded addresses are not trusted."""
        settings = self.settings
        digest = sha256((settings.server_id + ":" + peer).encode()).hexdigest()
        limits = [
            ("global", settings.global_per_minute),
            ("peer:" + digest, settings.requests_per_minute),
        ]
        if creating:
            limits.append(("create:" + digest, settings.create_per_minute))
        if joining:
            limits.append(("join:" + digest, settings.join_per_minute))
        denied = False
        with self.transaction() as db:
            db.execute("SELECT pg_advisory_xact_lock(%s)", (RATE_LOCK,))
            db.execute("DELETE FROM ptcg_v2.rate_buckets WHERE reset_at <= now()")
            size = db.execute(
                "SELECT count(*) AS n FROM ptcg_v2.rate_buckets"
            ).fetchone()["n"]
            for key, limit in limits:
                row = db.execute(
                    "SELECT count FROM ptcg_v2.rate_buckets WHERE key=%s", (key,)
                ).fetchone()
                if (row and row["count"] >= limit) or (
                    not row and size >= settings.max_rate_keys
                ):
                    denied = True
                    break
                if row:
                    db.execute(
                        "UPDATE ptcg_v2.rate_buckets SET count=count+1 WHERE key=%s",
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
