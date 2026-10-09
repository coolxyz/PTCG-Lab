"""Build and validate executable candidates; a proposal is never a release."""
from __future__ import annotations

import hashlib
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

from .common import SyncError, read, write, digest, now
from .adapt import enable_proposals


def build(service, job_id):
    job = service.jobs.get(job_id)
    location = service.home / "results" / job_id
    if not (location / "migration.json").exists():
        raise SyncError("MIGRATION_REQUIRED", "请先完成资料预演或同步")
    migration = read(location / "migration.json")
    result = read(location / "adaptation.json")
    binding_path = service.root / "data/sync/card-name-bindings.json"
    bindings = read(binding_path)["bindings"] if binding_path.exists() else []
    catalog, effects, plain = enable_proposals(migration["catalog"], result, bindings)
    reference = service.root / "data/sync/reference-decks.json"
    if not catalog.get("templates") and reference.exists():
        # Only restore source-backed reference presets; user decks are separate.
        catalog["templates"] = read(reference)["templates"]
        catalog["version"] = digest({k:v for k,v in catalog.items() if k!="version"})
    service.validate_catalog(catalog)
    # First-party code is snapshotted from the development checkout; upstream
    # content can only supply data and closed-grammar parameters.
    rid = service.releases.archive(catalog=catalog, details=migration["details"], effects=effects, plain=plain, metadata={"kind": "battle", "commit": job["commit"], "jobId": job_id, "proposals": len(result["proposals"]), "environment": digest(result["environment"]), "baseline": service.releases.current()})
    service.jobs.update(job_id, battleCandidate=rid, battleBaseline=service.releases.current(), battleReleaseStatus="awaiting-verification")
    return rid


def verify(service, job_id, games=1000, workers=4):
    with service.jobs.lease():
        job = service.jobs.get(job_id)
        rid = job.get("battleCandidate") or build(service, job_id)
        service.releases.verify(rid)
        result_path = service.home / "verifications" / rid
        result_path.mkdir(parents=True, exist_ok=True)
        service.jobs.update(job_id, stage="testing-battle", battleReleaseStatus="testing")
        command = [sys.executable, "-X", "utf8", str(service.root / "scripts/sync/verify_runtime.py"), "--root", str(service.releases.path(rid).resolve()), "--output", str((result_path / "matches.json").resolve()), "--games", str(games), "--workers", str(workers)]
        with (result_path / "matches.log").open("wb") as log:
            process = subprocess.Popen(command, cwd=service.root, stdout=log, stderr=subprocess.STDOUT)
            while True:
                try:
                    returncode = process.wait(timeout=2)
                    break
                except subprocess.TimeoutExpired:
                    if service.jobs.get(job_id)["cancelled"]:
                        process.terminate()
                        process.wait(timeout=10)
                        raise SyncError("CANCELLED", "对战验证已取消")
        if returncode != 0 or not (result_path / "matches.json").exists():
            service.jobs.update(job_id, battleReleaseStatus="failed", stage="battle-verification-failed")
            raise SyncError("BATTLE_TEST_FAILED", "候选对战测试未通过，请查看验证日志")
        report = read(result_path / "matches.json")
        manifest = service.releases.manifest(rid)
        bound = all(report[k] == manifest[k] for k in ("engineVersion", "effectVersion", "catalogVersion"))
        evidence = {"releaseId": rid, "createdAt": now(), "manifestHash": digest(manifest), "verifierHash": hashlib.sha256((service.root / "scripts/sync/verify_runtime.py").read_bytes()).hexdigest(), "matchesHash": hashlib.sha256((result_path / "matches.json").read_bytes()).hexdigest(), "versionBound": bound, "games": report["games"], "passed": bound and report["passed"] and report["games"] >= 1000}
        write(result_path / "evidence.json", evidence)
        service.jobs.update(job_id, battleReleaseStatus="simulation-verified" if evidence["passed"] else "smoke-only", stage="battle-tested", battleEvidence=evidence)
        return evidence


def regression_evidence(root):
    backend = root / "artifacts/sync/backend.xml"
    browser = root / "artifacts/sync/browser.json"
    stamp = root / "artifacts/sync/regression-input.json"
    if not all(p.exists() for p in (backend, browser, stamp)):
        raise SyncError("REGRESSION_REQUIRED", "正式发布需要完整后端、浏览器和输入版本证据")
    inputs = read(stamp)
    if set(inputs.get("files", {})) == set():
        raise SyncError("REGRESSION_INVALID", "回归输入清单不能为空")
    for name, expected in inputs["files"].items():
        p = root / name
        if not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest() != expected:
            raise SyncError("REGRESSION_STALE", "源码在回归后变化，需要重新验证")
    suites = ET.parse(backend).getroot()
    leaf = list(suites) if suites.tag == "testsuites" else [suites]
    if not sum(int(s.get("tests", 0)) for s in leaf) or any(int(s.get(k, 0)) for s in leaf for k in ("failures", "errors", "skipped")):
        raise SyncError("REGRESSION_FAILED", "后端回归不满足发布条件")
    stats = read(browser)["stats"]
    if not stats.get("expected") or any(stats.get(k, 0) for k in ("unexpected", "flaky", "skipped")):
        raise SyncError("BROWSER_FAILED", "浏览器回归不满足发布条件")
    return {"backend": hashlib.sha256(backend.read_bytes()).hexdigest(), "browser": hashlib.sha256(browser.read_bytes()).hexdigest(), "inputs": digest(inputs)}


def publish(service, job_id, expected):
    with service.jobs.lease():
        job = service.jobs.get(job_id)
        rid = job.get("battleCandidate")
        if not rid:
            raise SyncError("NO_BATTLE_CANDIDATE", "尚无对战候选")
        location = service.home / "verifications" / rid
        evidence = read(location / "evidence.json") if (location / "evidence.json").exists() else {}
        if evidence.get("verifierHash") != hashlib.sha256((service.root / "scripts/sync/verify_runtime.py").read_bytes()).hexdigest():
            raise SyncError("VERIFIER_CHANGED", "对战验证器已变化，请重新验收")
        if not evidence.get("passed") or evidence.get("manifestHash") != digest(service.releases.verify(rid)) or evidence.get("matchesHash") != hashlib.sha256((location / "matches.json").read_bytes()).hexdigest():
            raise SyncError("BATTLE_EVIDENCE_REQUIRED", "缺少与候选版本一致的正式对战验收")
        regression = regression_evidence(service.root)
        manifest = service.releases.manifest(rid)
        for name, expected_hash in manifest["files"].items():
            if name.startswith(("packages/rules/", "packages/battle/", "packages/simulation/", "packages/engine_adapter/")) and (not (service.root / name).exists() or hashlib.sha256((service.root / name).read_bytes()).hexdigest() != expected_hash):
                raise SyncError("CANDIDATE_CODE_STALE", "候选执行代码与完整回归代码不一致，请重新构建和验证")
        if job["battleBaseline"] != expected:
            raise SyncError("RELEASE_CONFLICT", "对战候选基线不一致", 409)
        service.extend_identity_archive(rid)
        service.releases.backup_user(service.user_db)
        service.releases.activate(rid, expected, service.jobs.path)
        write(location / "publication.json", {"releaseId": rid, "createdAt": now(), "regression": regression, "evidence": evidence})
        remaining = len(service.tasks(job_id))
        service.jobs.update(job_id, stage="published", state="partial" if remaining else "completed", releaseId=rid, battleReleaseStatus="published-partial" if remaining else "published", catalogReleaseStatus="published")
        return service.jobs.get(job_id)
