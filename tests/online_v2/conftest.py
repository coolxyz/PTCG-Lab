"""Every database test owns a new DB; an explicit dedicated DSN is mandatory."""

import os
from pathlib import Path
import uuid
import pytest
import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg import sql
from ptcg_online_server.config import Settings
from ptcg_online_server.repository import Repository


@pytest.fixture
def settings():
    secret = os.environ.get("PTCG_V2_TEST_DSN_FILE")
    if not secret:
        pytest.skip("Dedicated P4-A PostgreSQL acceptance not configured")
    dsn = Path(secret).read_text().strip()
    info = conninfo_to_dict(dsn)
    assert info["host"] == "127.0.0.1" and info["dbname"] == "ptcg_p4a"
    database = "ptcg_p4a_" + uuid.uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as db:
        db.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
    parts = {**info, "dbname": database}
    # Tests use URL DSNs just like the deployed Settings contract.
    from urllib.parse import quote

    url = f"postgresql://{quote(parts['user'])}:{quote(parts.get('password', ''))}@{parts['host']}:{parts.get('port', '5432')}/{database}"
    try:
        yield Settings(
            url, "p4a-test-server", "http://127.0.0.1:8784", allow_loopback_http=True
        )
    finally:
        with psycopg.connect(dsn, autocommit=True) as db:
            db.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(
                    sql.Identifier(database)
                )
            )


@pytest.fixture
def repo(settings):
    repo = Repository(settings)
    repo.migrate()
    return repo
