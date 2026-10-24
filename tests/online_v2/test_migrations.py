from dataclasses import replace
import pytest
from ptcg_online_server.repository import Repository
from ptcg_online_server.errors import ServiceError


def test_migration_is_explicit_idempotent_and_checksum_pinned(settings):
    repo = Repository(settings)
    with pytest.raises(ServiceError):
        repo.healthy()
    repo.migrate()
    repo.migrate()
    assert repo.healthy()
    with repo.db() as db:
        assert (
            db.execute(
                "SELECT count(*) AS n FROM ptcg_v2.schema_migrations"
            ).fetchone()["n"]
            == 1
        )
        db.execute("UPDATE ptcg_v2.schema_migrations SET checksum='edited'")
    with pytest.raises(ServiceError):
        repo.migrate()
    with pytest.raises(ServiceError):
        repo.healthy()


def test_migration_refuses_old_product_database_without_changing_it(settings):
    repo = Repository(settings)
    with repo.db() as db:
        db.execute("CREATE TABLE online_users(id text)")
        db.execute("INSERT INTO online_users VALUES('existing-user')")
    with pytest.raises(ServiceError, match="FORBIDDEN"):
        repo.migrate()
    with repo.db() as db:
        assert (
            db.execute("SELECT id FROM online_users").fetchone()["id"]
            == "existing-user"
        )
        assert (
            db.execute("SELECT to_regnamespace('ptcg_v2') AS name").fetchone()["name"]
            is None
        )


def test_schema_and_server_identity_cannot_silently_drift(repo):
    wrong = Repository(replace(repo.settings, server_id="another-server"))
    with pytest.raises(ServiceError):
        wrong.healthy()
    with pytest.raises(ServiceError):
        wrong.migrate()
    with repo.db() as db:
        db.execute(
            "INSERT INTO ptcg_v2.schema_migrations(version,checksum) VALUES(99,'future')"
        )
    with pytest.raises(ServiceError):
        repo.migrate()
