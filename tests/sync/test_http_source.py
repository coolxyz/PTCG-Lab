"""Synthetic Git objects verify source integrity without Git or external traffic."""

import json

import pytest
from packages.sync.common import SyncError
from packages.sync.http_source import HTTPSource, object_sha


def fixture_source(tmp_path):
    blobs = {}
    rows = []

    def blob(path, data, mode="100644"):
        key = object_sha("blob", data)
        blobs[key] = data
        rows.append(dict(path=path, mode=mode, type="blob", sha=key))

    blob("ptcg_chs_infos.json", b'{"collections":[]}')
    blob("img/1.png", b"opaque-image")
    child = object_sha("tree", b"100644 1.png\0" + bytes.fromhex(rows[1]["sha"]))
    rows.append(dict(path="img", mode="040000", type="tree", sha=child))
    root = object_sha(
        "tree",
        b"40000 img\0"
        + bytes.fromhex(child)
        + b"100644 ptcg_chs_infos.json\0"
        + bytes.fromhex(rows[0]["sha"]),
    )
    calls = []
    commit = "a" * 40

    def transport(path, limit, *, raw=False):
        calls.append(path)
        if path.endswith("commits/main"):
            value = {"sha": commit}
        elif "/git/commits/" in path:
            value = {"sha": commit, "tree": {"sha": root}, "parents": []}
        elif "/git/trees/" in path:
            value = {"sha": root, "truncated": False, "tree": rows}
        elif "/git/blobs/" in path:
            return blobs[path.rsplit("/", 1)[1]]
        elif "/compare/" in path:
            value = {
                "base_commit": {"sha": commit},
                "merge_base_commit": {"sha": commit},
                "status": "ahead",
            }
        else:
            raise AssertionError(path)
        return json.dumps(value).encode()

    source = HTTPSource(
        tmp_path.resolve() / "source",
        "https://github.com/example/Cards",
        transport=transport,
    )
    return source, transport, calls, rows, blobs


def test_pinned_tree_blob_snapshot_and_restart_cache_without_git(tmp_path, monkeypatch):
    import subprocess

    monkeypatch.setattr(
        subprocess, "run", lambda *a, **kw: pytest.fail("Git executable must not run")
    )
    source, _, calls, rows, _ = fixture_source(tmp_path)
    commit = source.fetch()
    assert commit == "a" * 40
    assert source.blob(commit, "img/1.png") == b"opaque-image"
    data, tree = source.snapshot(commit, tmp_path / "snapshot")
    assert data == {"collections": []} and len(tree) == 2
    assert len([c for c in calls if "/git/trees/" in c]) == 1
    again = HTTPSource(
        source.directory,
        source.repository,
        transport=lambda *a, **kw: pytest.fail("Fixed object should remain offline"),
    )
    assert again.blob(commit, "img/1.png") == b"opaque-image"
    assert source.is_ancestor(commit, "b" * 40)


@pytest.mark.parametrize(
    "kind",
    [
        "truncated",
        "duplicate",
        "tree-hash",
        "blob-hash",
        "path",
        "missing-parent",
        "origin",
        "cache-corrupt",
    ],
)
def test_untrusted_source_does_not_become_a_snapshot(tmp_path, kind):
    source, transport, _, rows, blobs = fixture_source(tmp_path)
    if kind == "duplicate":
        rows.append(dict(rows[0]))
    elif kind == "tree-hash":
        rows[0]["sha"] = "b" * 40
    elif kind == "blob-hash":
        blobs[rows[1]["sha"]] = b"changed"
    elif kind == "path":
        rows[0]["path"] = "../outside"
    elif kind == "missing-parent":
        rows[1]["path"] = "absent/1.png"
    elif kind == "truncated":

        def bad(path, limit, *, raw=False):
            result = transport(path, limit, raw=raw)
            if "/git/trees/" in path:
                value = json.loads(result)
                value["truncated"] = True
                result = json.dumps(value).encode()
            return result

        source.transport = bad
    elif kind == "origin":
        source.initialize()
        (source.directory / "origin.json").write_text(
            '{"schema":1,"repository":"https://github.com/other/Cards"}'
        )
    else:
        source.blob("a" * 40, "img/1.png")
        (source.directory / "blobs" / rows[1]["sha"]).write_bytes(b"broken")
    with pytest.raises(SyncError):
        source.blob("a" * 40, "img/1.png")
    assert not (tmp_path / "snapshot").exists()


@pytest.mark.parametrize(
    "path", ["../bad", "img//1.png", "img/./1.png", "/abs", "img\\bad", "x:bad"]
)
def test_path_rejects_noncanonical_or_platform_escape(path):
    with pytest.raises(SyncError):
        HTTPSource.validate_path(path)


def test_default_source_and_fork_read_do_not_spawn_git(tmp_path, monkeypatch):
    from packages.sync.repositories import source_for, legacy_repository
    import subprocess

    monkeypatch.setattr(
        subprocess, "run", lambda *a, **kw: pytest.fail("No external Git")
    )
    root = tmp_path.resolve()
    config = root / ".catalog/upstream/PTCG-CHS-Datasets/.git/config"
    config.parent.mkdir(parents=True)
    config.write_text('[remote "origin"]\nurl = https://github.com/example/Cards\n')
    assert legacy_repository(root) == "https://github.com/example/Cards"
    assert isinstance(source_for(root, "https://github.com/example/Cards"), HTTPSource)
