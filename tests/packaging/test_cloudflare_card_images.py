"""Use local Git and synthetic images; never authenticate or publish to Cloudflare."""

from copy import deepcopy
import io
import json
from pathlib import Path
import subprocess

from PIL import Image
import pytest

from scripts.product import cloudflare_card_images as tool
from scripts.product.git_card_images import GitCache


@pytest.fixture
def artwork(tmp_path):
    repository = tmp_path / "upstream"
    repository.mkdir()
    subprocess.run(["git", "init", "-b", "main", repository], check=True, capture_output=True)
    image = io.BytesIO()
    Image.new("RGB", (8, 12), "purple").save(image, "PNG")
    raw = image.getvalue()
    path = repository / "img/246/0.png"
    path.parent.mkdir(parents=True)
    path.write_bytes(raw)
    subprocess.run(["git", "-C", repository, "add", "."], check=True)
    subprocess.run(["git", "-C", repository, "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                    "commit", "-m", "synthetic artwork"], check=True, capture_output=True)
    commit = subprocess.check_output(["git", "-C", repository, "rev-parse", "HEAD"]).decode().strip()
    value = {
        "schema": 1, "repository": "https://github.com/example/cards", "commit": commit,
        "catalogFileSha256": "a" * 64,
        "cards": [{"printingId": "p1", "engineId": "e1", "path": "img/246/0.png", "blob": tool.blob_id(raw)}],
    }
    index = tmp_path / "index.json"
    index.write_text(json.dumps(value))
    def cache(path, _repository):
        return GitCache(path, str(repository), allow_local=True)
    def build(name="output", **options):
        return tool.build([index], tmp_path / "cache", tmp_path / name, cache_factory=cache, **options)
    return index, value, raw, cache, build


def test_real_git_export_uses_exact_app_urls_and_original_bytes(artwork, tmp_path):
    index, value, raw, _, build = artwork
    # Two printing identities referencing one upstream URL count as a single file.
    value["cards"].append({**value["cards"][0], "printingId": "p2"})
    index.write_text(json.dumps(value))
    report = build()
    out = tmp_path / "output"
    relative = f"example/cards/{value['commit']}/img/246/0.png"
    assert (out / "public" / relative).read_bytes() == raw
    assert report["images"] == 1 and report["gitBlobReads"] == 1
    assert report["automaticallyDeployed"] is report["appImportRequired"] is False
    manifest = tool.verify(out, pin=report["manifestSha256"])
    assert set(manifest["files"]) == {relative, "_headers"}
    assert not list(out.rglob("*.sqlite")) and not list(out.rglob("*.zip"))
    config = json.loads((out / "wrangler.json").read_text())
    assert config["assets"]["not_found_handling"] == "none"
    assert not {"main", "routes", "services", "r2_buckets", "run_worker_first"} & config.keys()


def test_export_reproducible_and_previous_verified_bytes_are_reused(artwork, tmp_path):
    _, _, _, cache, build = artwork
    first = build("one")
    second = build("two", previous=tmp_path / "one")
    assert first["manifestSha256"] == second["manifestSha256"]
    assert second["reusedFiles"] == 1 and second["gitBlobReads"] == 0
    # Cached commit and blobs also remain sufficient with no origin available.
    class OfflineGitCache(GitCache):
        def git(self, *args, **kwargs):
            if args[0] == "fetch":
                raise AssertionError("An already pinned cache must not fetch again")
            return super().git(*args, **kwargs)
    def offline(path, _repository):
        return OfflineGitCache(path, str(tmp_path / "upstream"), allow_local=True)
    third = tool.build([artwork[0]], tmp_path / "cache", tmp_path / "three", cache_factory=offline)
    assert third["manifestSha256"] == first["manifestSha256"]


def test_incremental_export_fetches_only_new_blob_and_can_retain_old_commit(artwork, tmp_path):
    index, value, _, cache, build = artwork
    first = build("one")
    old_index = tmp_path / "old-index.json"
    old_index.write_bytes(index.read_bytes())
    repository = tmp_path / "upstream"
    path = repository / "img/246/1.png"
    image = io.BytesIO()
    Image.new("RGB", (8, 12), "white").save(image, "PNG")
    raw = image.getvalue()
    path.write_bytes(raw)
    subprocess.run(["git", "-C", repository, "add", "."], check=True)
    subprocess.run(["git", "-C", repository, "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                    "commit", "-m", "new image"], check=True, capture_output=True)
    value["commit"] = subprocess.check_output(["git", "-C", repository, "rev-parse", "HEAD"]).decode().strip()
    value["cards"].append({"printingId": "p2", "engineId": "e2", "path": "img/246/1.png", "blob": tool.blob_id(raw)})
    index.write_text(json.dumps(value))
    report = tool.build([old_index, index], tmp_path / "cache", tmp_path / "two", previous=tmp_path / "one", cache_factory=cache)
    assert report["images"] == 3 and report["reusedFiles"] == 2 and report["gitBlobReads"] == 1
    assert report["manifestSha256"] != first["manifestSha256"]
    manifest = tool.verify(tmp_path / "two")
    assert len(manifest["sources"]) == 2


@pytest.mark.parametrize("field,value", [
    ("path", "../secret.png"), ("path", "img/../secret.png"),
    ("path", "img/image.png?token=x"), ("path", "img/image.png#fragment"),
    ("path", "img/evil.html"), ("path", "img/%2e%2e/secret.png"),
    ("blob", "main"), ("printingId", ""),
])
def test_unsafe_index_is_rejected_before_git(artwork, field, value):
    index, original, _, _, _ = artwork
    changed = deepcopy(original)
    changed["cards"][0][field] = value
    index.write_text(json.dumps(changed))
    with pytest.raises(ValueError):
        tool.plan([index])


@pytest.mark.parametrize("field,value", [("commit", "main"), ("repository", "https://user:password@github.com/example/cards"), ("schema", True)])
def test_only_public_repository_and_fixed_commit_are_accepted(artwork, field, value):
    index, original, _, _, _ = artwork
    original[field] = value
    index.write_text(json.dumps(original))
    with pytest.raises(ValueError):
        tool.plan([index])


def test_multiple_index_conflict_and_quota_reject_before_fetch(artwork, tmp_path):
    index, value, _, _, _ = artwork
    conflicting = deepcopy(value)
    conflicting["cards"][0]["blob"] = "b" * 40
    other = tmp_path / "conflict.json"
    other.write_text(json.dumps(conflicting))
    with pytest.raises(ValueError, match="Conflicting"):
        tool.plan([index, other])
    value["cards"].append({**value["cards"][0], "printingId": "p2", "path": "img/246/1.png"})
    index.write_text(json.dumps(value))
    def never(*args):
        raise AssertionError("Over-budget plans must not initialize Git")
    with pytest.raises(ValueError, match="budget exceeded"):
        tool.build([index], tmp_path / "cache", tmp_path / "output", max_files=2, cache_factory=never)
    assert not (tmp_path / "output").exists()


def test_failed_build_preserves_cache_and_does_not_publish_partial_output(artwork, tmp_path):
    index, value, _, _, build = artwork
    with pytest.raises(ValueError, match="disk budget"):
        build(max_total_bytes=1)
    assert not (tmp_path / "output").exists() and (tmp_path / "cache").exists()
    assert not list(tmp_path.glob(".ptcg-cdn-*"))
    value["cards"][0]["blob"] = "b" * 40
    index.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="pinned Git tree"):
        build()


def test_image_format_must_match_static_content_type(artwork, tmp_path):
    index, value, raw, _, _ = artwork
    value["cards"][0]["path"] = "img/246/0.jpg"
    index.write_text(json.dumps(value))
    class MislabeledCache:
        def __init__(self, *args):
            pass
        def fetch(self, commit):
            return commit
        def tree(self, commit):
            return {"img/246/0.jpg": tool.blob_id(raw)}
        def prefetch(self, keys):
            pass
        def blob(self, key, limit):
            return raw
    with pytest.raises(ValueError, match="Content-Type"):
        tool.build([index], tmp_path / "cache", tmp_path / "output", cache_factory=MislabeledCache)
    assert not (tmp_path / "output").exists()


def test_existing_output_and_linked_source_are_never_overwritten(artwork, tmp_path):
    index, _, _, _, build = artwork
    build()
    before = (tmp_path / "output/MANIFEST.json").read_bytes()
    with pytest.raises(ValueError, match="NEW output"):
        build()
    assert (tmp_path / "output/MANIFEST.json").read_bytes() == before
    link = tmp_path / "link.json"
    link.symlink_to(index)
    with pytest.raises(ValueError, match="symlinks"):
        tool.plan([link])


@pytest.mark.parametrize("change", ["extra", "image", "config", "pin", "link"])
def test_verify_refuses_tampering_and_extra_public_files(artwork, tmp_path, change):
    report = artwork[4]()
    out = tmp_path / "output"
    pin = report["manifestSha256"]
    if change == "extra":
        (out / "public/credentials.json").write_text("must not upload")
    elif change == "image":
        next((out / "public").rglob("*.png")).write_bytes(b"broken")
    elif change == "config":
        config = json.loads((out / "wrangler.json").read_text())
        config["main"] = "unexpected.js"
        (out / "wrangler.json").write_text(json.dumps(config))
    elif change == "link":
        (out / "public/linked").symlink_to(tmp_path / "upstream", target_is_directory=True)
    else:
        pin = "0" * 64
    with pytest.raises(ValueError):
        tool.verify(out, pin=pin)


def test_deploy_requires_verified_pin_explicit_account_and_no_shell(artwork, tmp_path, monkeypatch):
    report = artwork[4]()
    wrangler = tmp_path / "wrangler.js"
    wrangler.write_text("// subprocess is mocked; no Cloudflare request")
    options = dict(pin=report["manifestSha256"], account_id="a" * 32, wrangler=wrangler)
    def never(*args, **kwargs):
        raise AssertionError("Dry run must not start a process")
    monkeypatch.setattr(tool.subprocess, "run", never)
    assert tool.deploy(tmp_path / "output", **options, dry_run=True)["published"] is False
    calls = []
    monkeypatch.setattr(tool.subprocess, "run", lambda *a, **kw: calls.append((a, kw)))
    assert tool.deploy(tmp_path / "output", **options)["published"] is True
    args, kw = calls[0]
    assert args[0][1] == str(wrangler) and "--config" in args[0]
    assert kw["env"]["CLOUDFLARE_ACCOUNT_ID"] == "a" * 32 and "shell" not in kw
    assert kw["stdin"] is subprocess.DEVNULL
    with pytest.raises(ValueError, match="account ID"):
        tool.deploy(tmp_path / "output", **{**options, "account_id": "wrong"})
    with pytest.raises(ValueError, match="Manifest SHA"):
        tool.deploy(tmp_path / "output", **{**options, "pin": "b" * 64})
    assert len(calls) == 1


def test_cli_invalid_input_is_nonzero_without_any_output(tmp_path):
    tool_path = Path(tool.__file__)
    completed = subprocess.run([__import__("sys").executable, str(tool_path), "plan", "--index", str(tmp_path / "missing")],
                               capture_output=True, text=True)
    assert completed.returncode != 0 and "ERROR:" in completed.stderr


def test_cloudflare_local_state_and_secrets_are_not_public_source():
    from scripts.public.policy import allowed_source, private_name
    for name in ("scripts/product/cloudflare/.wrangler/config/default.toml",
                 "scripts/product/cloudflare/.dev.vars", "scripts/product/cloudflare/.dev.vars.production"):
        assert private_name(name) and not allowed_source(name)
