"""Build and apply a pinned, offline desktop rule update using the normal publish gate."""

import argparse
from contextlib import ExitStack, contextmanager
from hashlib import file_digest, sha256
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sys
from tempfile import TemporaryDirectory
from zipfile import ZIP_DEFLATED, ZipFile


def read(path):
    return json.loads(path.read_bytes())


def digest_file(path):
    with path.open("rb") as stream:
        return file_digest(stream, "sha256").hexdigest()


def pinned(value):
    if not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("完整的 SHA-256 摘要是必需的。")
    return value


def regular(root, name):
    relative = PurePosixPath(name)
    if relative.is_absolute() or any(
        part in (".", "..") or any(c in part for c in '\\:<>"|?*')
        for part in relative.parts
    ) or relative.as_posix() != name:
        raise ValueError(f"不安全的更新路径：{name}")
    path = root / name
    if any(p.is_symlink() or p.is_junction() for p in (path, *path.parents)):
        raise ValueError(f"不允许链接：{name}")
    if not path.is_file():
        raise ValueError(f"缺少更新文件：{name}")
    return path


def evidence_check(manifest, evidence, matches, source):
    from packages.sync.common import digest

    if not (
        evidence["passed"] and evidence["versionBound"] and evidence["games"] >= 1000
        and evidence["releaseId"] == manifest["releaseId"]
        and evidence["manifestHash"] == digest(manifest)
        and evidence["matchesHash"] == digest_file(matches)
        and evidence["verifierHash"] == digest_file(source / "scripts/sync/verify_runtime.py")
    ):
        raise ValueError("候选规则的模拟验收与发布清单不匹配。")


def prepare(source, sync_home, release_id, previous, output):
    from packages.sync.releases import Releases
    from packages.sync.verification import regression_evidence

    source, sync_home, output = map(lambda p: Path(p).resolve(), (source, sync_home, output))
    release_id, previous = pinned(release_id), pinned(previous)
    releases = Releases(sync_home, source)
    manifest = releases.verify(release_id)
    if manifest["metadata"].get("baseline") != previous or release_id == previous:
        raise ValueError("候选规则与批准的前置版本不匹配。")
    releases.verify(previous)
    candidate = releases.path(release_id)
    proof = sync_home / "verifications" / release_id
    evidence = read(proof / "evidence.json")
    evidence_check(manifest, evidence, proof / "matches.json", source)
    regression = regression_evidence(source)
    source_files = set(read(source / "artifacts/sync/regression-input.json")["files"])
    source_files.update({
        "artifacts/sync/backend.xml", "artifacts/sync/browser.json",
        "artifacts/sync/regression-input.json", "resources/artwork/builtin-snapshot.json",
    })
    metadata = {
        "schema": 1, "previous": previous, "releaseId": release_id,
        "engineVersion": manifest["engineVersion"], "effectVersion": manifest["effectVersion"],
        "regression": regression,
    }
    with ZipFile(output, "x", compression=ZIP_DEFLATED, compresslevel=3) as archive:
        archive.writestr("approval.json", json.dumps(metadata))
        for directory, base, names in (
            ("source", source, sorted(source_files)),
            (f"stage/releases/{release_id}", candidate, ["manifest.json", *manifest["files"]]),
            ("proof", proof, ["evidence.json", "matches.json"]),
        ):
            for name in names:
                archive.write(regular(base, name), f"{directory}/{name}")
    return {"archiveSha256": digest_file(output), **metadata}


def media_inputs(archive, payload):
    """Bind upgrade material to the engine and effects included in this desktop payload."""
    archive, payload = Path(archive), Path(payload)
    with ZipFile(archive) as update:
        approval = json.loads(update.read("approval.json"))
        prefix = "stage/releases/" + pinned(approval["releaseId"]) + "/"
        manifest = json.loads(update.read(prefix + "manifest.json"))
    for name in (
        "data/catalog/catalog.json", "data/simulation/effects.json",
        "artifacts/engine/overlay-hashes.json", "runtime/engine/ptcg/utils/checker.py",
    ):
        if manifest["files"][name] != digest_file(regular(payload, "app/" + name)):
            raise ValueError(f"安装包与批准规则的文件不匹配：{name}")
    if manifest["effectVersion"] != approval["effectVersion"]:
        raise ValueError("安装包效果版本与批准规则不匹配。")
    return {"archiveSha256": digest_file(archive), "releaseId": approval["releaseId"]}


@contextmanager
def profile_lock(profile, install_root):
    if sys.platform == "win32":
        from ptcg_desktop_api.native import StateLock
        from ptcg_desktop_api.windows_storage import WindowsDirectory
        from ptcg_online_client.contracts import ClientError

        with ExitStack() as locks:
            directory = WindowsDirectory(profile)
            locks.callback(directory.close)
            c = directory.con
            try:
                handle = directory.open_file(
                    profile / "shell.lock", c.GENERIC_READ | c.GENERIC_WRITE, c.OPEN_ALWAYS,
                )
            except directory.error as exc:
                code = "PROFILE_IN_USE" if exc.winerror in {32, 33} else "UNSAFE_STORAGE"
                raise ClientError(code) from None
            locks.callback(handle.Close)
            directory.inspect(handle)
            backend = StateLock(profile)
            locks.callback(backend.close)
            yield
    elif sys.platform == "darwin":
        from scripts.product.desktop_install import offline

        with offline(install_root, profile):
            yield
    else:
        raise ValueError("请在安装包所对应的 macOS 或 Windows 上执行。")


def copy_directory(source, target):
    if target.exists():
        if target.is_symlink() or target.is_junction() or not target.is_dir():
            raise ValueError("规则资料目录不安全。")
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".rule-", dir=target.parent) as temporary:
        stage = Path(temporary) / "copy"
        shutil.copytree(source, stage)
        stage.rename(target)


def activate(source, stage, proof, approval, profile):
    from packages.sync.releases import Releases
    from packages.sync.service import SyncService
    from packages.sync.verification import publish, regression_evidence

    previous, candidate = approval["previous"], approval["releaseId"]
    home = profile / "app.sync"
    current = read(home / "current.json")["releaseId"]
    if current not in (previous, candidate):
        raise ValueError(f"当前规则 {current[:12]} 与本批前置版本不符。")
    if regression_evidence(source) != approval["regression"]:
        raise ValueError("回归验收摘要不匹配。")
    released = Releases(stage, source)
    manifest = released.verify(candidate)
    if (manifest["metadata"].get("baseline") != previous
            or manifest["engineVersion"] != approval["engineVersion"]
            or manifest["effectVersion"] != approval["effectVersion"]):
        raise ValueError("候选规则版本不匹配。")
    evidence_check(manifest, read(proof / "evidence.json"), proof / "matches.json", source)
    if current == candidate:
        Releases(home, source).verify(candidate)
        return "already-current"
    if not (profile / "app.sqlite").is_file():
        raise ValueError("找不到要备份的应用数据库。")
    service = SyncService(home, profile / "app.sqlite", root=source)
    try:
        service.releases.verify(previous)
        copy_directory(released.path(candidate), service.releases.path(candidate))
        copy_directory(proof, home / "verifications" / candidate)
        service.releases.verify(candidate)
        job = service.jobs.create("offline-rule:" + candidate, {
            "kind": "plan", "commit": manifest["metadata"]["commit"], "baseline": previous,
        })
        service.jobs.update(job["id"], state="ready", battleCandidate=candidate,
                            battleBaseline=previous, battleReleaseStatus="simulation-verified")
        result = publish(service, job["id"], previous)
        if result["battleReleaseStatus"] != "published" or service.releases.current() != candidate:
            raise ValueError("规则发布未完成。")
        return "published"
    finally:
        service.close()


def apply(archive, expected, payload, payload_pin, profile, install_root=None, app=None, bundle_pin=None):
    archive, payload, profile = map(lambda p: Path(p).absolute(), (archive, payload, profile))
    if digest_file(archive) != pinned(expected):
        raise ValueError("规则升级包校验失败。")
    if digest_file(payload / "manifest.json") != pinned(payload_pin):
        raise ValueError("安装的 App 与此规则升级包不匹配。")
    with TemporaryDirectory(prefix="ptcg-rule-") as directory:
        work = Path(directory)
        # The archive is pinned by the shipped launchers before any files are imported.
        with ZipFile(archive) as update:
            update.extractall(work)
        source = work / "source"
        sys.path.insert(0, str(source))
        from ptcg_desktop_api.installation import InstallError, startup_guard
        from packages.sync.common import SyncError
        from ptcg_online_client.contracts import ClientError

        approval = read(work / "approval.json")
        if approval["schema"] != 1:
            raise ValueError("无法识别规则升级包。")
        try:
            if app is not None:
                from scripts.product.desktop_install import Installer

                try:
                    print(json.dumps(Installer(install_root, profile).install(app, pinned(bundle_pin), adopt=True)))
                except InstallError as exc:
                    if str(exc) != "INSTALL_ALREADY_CURRENT":
                        raise
            if not os.path.lexists(profile / "app.sync/current.json"):
                print("使用新版 App 内置规则。")
                return
            with profile_lock(profile, install_root):
                startup_guard(profile, payload)
                status = activate(source, work / "stage", work / "proof", approval, profile)
        except (ClientError, InstallError, SyncError) as exc:
            raise ValueError(f"{getattr(exc, 'code', exc)}。请退出 PTCG Lab，处理提示后重试。") from None
        verb = "已备份资料并启用" if status == "published" else "当前已启用"
        print(f"{verb}本机规则 {approval['releaseId'][:12]}。请新建一局人机对战。")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    operations = parser.add_subparsers(dest="operation", required=True)
    build = operations.add_parser("prepare")
    for name in ("source", "sync-home", "release-id", "previous", "output"):
        build.add_argument("--" + name, required=True)
    use = operations.add_parser("apply")
    for name in ("archive", "expected", "payload", "payload-pin", "profile"):
        use.add_argument("--" + name, required=True)
    for name in ("install-root", "app", "bundle-pin"):
        use.add_argument("--" + name)
    args = vars(parser.parse_args())
    operation = args.pop("operation")
    try:
        if operation == "prepare":
            sys.path.insert(0, str(Path(args["source"]).resolve()))
            print(json.dumps(prepare(**args)))
        else:
            apply(**args)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"本机规则升级未完成：{exc}\n")
