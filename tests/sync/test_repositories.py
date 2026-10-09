import pytest
from packages.sync.common import REPOSITORY, SyncError, write
from packages.sync.repositories import canonical, source_for
from packages.sync.service import SyncService
from packages.sync.identity import build_catalog
from packages.sync.normalize import normalize


def test_repo_url_normalization_and_rejection(tmp_path):
    assert canonical(" https://github.com/duanxr/PTCG-CHS-Datasets.git/ ") == REPOSITORY
    for url in [
        "http://github.com/a/b",
        "https://github.com.evil.test/a/b",
        "https://secret@github.com/a/b",
        "git@github.com:a/b",
        "file:///tmp/repo",
        "https://github.com/a/b?token=secret",
        "https://github.com/a/b/tree/main",
        "https://127.0.0.1/a/b",
    ]:
        with pytest.raises(SyncError):
            canonical(url)
    assert (
        source_for(tmp_path, "https://github.com/a/b").directory
        != source_for(tmp_path, "https://github.com/c/b").directory
    )


def test_setting_persists_without_network_and_is_local(tmp_path):
    service = SyncService(tmp_path / "sync", tmp_path / "user.sqlite", root=tmp_path)
    try:
        assert service.source.repository == REPOSITORY
        service.set_repository("https://github.com/example/CompatibleCards.git")
        assert service.source.repository == "https://github.com/example/CompatibleCards"
        again = SyncService(service.home, service.user_db, root=tmp_path)
        try:
            assert again.source.repository == service.source.repository
            service.set_repository(REPOSITORY)
            again.refresh_source()
            assert again.source.repository == REPOSITORY
        finally:
            again.close()
        job = service.jobs.create("pending", {"repository": REPOSITORY})
        with pytest.raises(SyncError, match="等待"):
            service.set_repository("https://github.com/example/Other")
        service.jobs.update(job["id"], state="failed")
        service.set_repository("https://github.com/example/Other")
        # A retry keeps the original repository stored with the job.
        assert service.jobs.get(job["id"])["repository"] == REPOSITORY
        commit = "a" * 40
        write(
            service.home / "snapshots" / commit / "source.json",
            {"repository": REPOSITORY},
        )
        assert service.image_source(commit).repository == REPOSITORY
    finally:
        service.close()


def test_source_attribution_uses_configured_repository():
    from test_sync import raw as synthetic_source

    raw = synthetic_source.__wrapped__()
    result = build_catalog(
        normalize(raw),
        {"cards": [], "products": []},
        {},
        "a" * 40,
        repository="https://github.com/example/CompatibleCards",
    )
    assert (
        result["catalog"]["upstream"]["repository"]
        == "https://github.com/example/CompatibleCards"
    )


def test_retry_uses_job_repository_after_setting_changes(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from packages.sync.source import GitSource

    service = SyncService(tmp_path / "sync", tmp_path / "user.sqlite", root=tmp_path)
    old = REPOSITORY
    job = service.jobs.create(
        "retry-source",
        {"repository": old, "kind": "check", "commit": None, "baseline": None},
    )
    service.jobs.update(job["id"], state="failed")
    service.set_repository("https://github.com/example/NewSource")
    seen = []

    def fake_source(root, repository):
        seen.append(repository)
        return SimpleNamespace(
            repository=repository,
            fetch=lambda: "a" * 40,
            validate_sha=GitSource.validate_sha,
        )

    monkeypatch.setattr("packages.sync.repositories.source_for", fake_source)
    try:
        service.run(job["id"])
        assert service.jobs.get(job["id"])["state"] == "completed"
        assert seen == [old]
    finally:
        service.close()
