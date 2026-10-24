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
            == 3
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


def test_incremental_upgrade_keeps_p4a_tokens_and_migration_checksum(
    settings, monkeypatch
):
    from ptcg_online_server import repository
    from ptcg_online_server.auth import Sessions

    original = repository.migrations
    with monkeypatch.context() as m:
        m.setattr(repository, "migrations", lambda: original()[:1])
        old = Repository(settings)
        old.migrate()
        tokens = Sessions(old).create(name="existing", device_name="p4a")
        with old.db() as db:
            checksum = db.execute(
                "SELECT checksum FROM ptcg_v2.schema_migrations WHERE version=1"
            ).fetchone()["checksum"]
    current = Repository(settings)
    current.migrate()
    assert (
        Sessions(current).authenticate(tokens["accessToken"])["userId"]
        == tokens["userId"]
    )
    with current.db() as db:
        assert (
            db.execute(
                "SELECT checksum FROM ptcg_v2.schema_migrations WHERE version=1"
            ).fetchone()["checksum"]
            == checksum
        )


def test_p4b_initialized_record_is_promoted_without_reinitialization(
    settings, monkeypatch
):
    from ptcg_online_server import repository
    from ptcg_online_server.initialization import Initializer
    from ptcg_online_server.lobby import Lobby
    from ptcg_online_server.matches import Matches
    from tests.online_v2.test_lobby import RELEASE, prepare
    from tests.online_v2.test_matches import GameWorker

    settings = replace(settings, approved_rule_release_id=RELEASE)
    original = repository.migrations
    worker = GameWorker()
    with monkeypatch.context() as patch:
        patch.setattr(repository, "migrations", lambda: original()[:2])
        old = Repository(settings)
        old.migrate()
        a, b, room, _ = prepare(Lobby(old, worker))
        assert Initializer(old, worker).tick()
        with old.db() as db:
            before = db.execute(
                "SELECT * FROM ptcg_v2.initializations WHERE match_id=%s",
                (room["matchId"],),
            ).fetchone()

    def forbidden(*args):
        pytest.fail("an existing initial record must not draw another hand")

    monkeypatch.setattr(worker, "initialize", forbidden)
    current = Repository(settings)
    current.migrate()
    service = Matches(current, worker)
    assert service.promote_one()
    assert not service.promote_one()
    for token, seat in ((a, "PLAYER1"), (b, "PLAYER2")):
        view = service.get(token, room["matchId"])
        assert view["matchSeq"] == 0 and view["viewerSeat"] == seat
    with current.db() as db:
        assert (
            before
            == db.execute(
                "SELECT * FROM ptcg_v2.initializations WHERE match_id=%s",
                (room["matchId"],),
            ).fetchone()
        )
