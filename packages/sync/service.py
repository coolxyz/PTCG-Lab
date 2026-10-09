"""Shared CLI/API synchronization orchestration with durable local state."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone, timedelta
import hashlib
import json
from pathlib import Path
import sqlite3
import threading

from .common import ROOT, SyncError, read, write, digest, now, inside
from .normalize import normalize, difference, VERSION as PARSER_VERSION
from .identity import build_catalog
from .adapt import adapt, VERSION as ADAPTER_VERSION
from .jobs import Jobs
from .releases import Releases
from .runtime import RuntimePool
from .images import Images


class SyncService:
    def __init__(self, home=None, user_db=None, *, root=ROOT, source=None):
        self.root = Path(root)
        self.home = Path(home or self.root / ".catalog/sync")
        self.user_db = Path(user_db or self.root / "var/app.sqlite")
        self.jobs = Jobs(self.home)
        self.releases = Releases(self.home, self.root)
        from .repositories import configured, source_for
        self.custom_source = source
        self.source = source or source_for(self.root, configured(self.home, self.root))
        self.pool = RuntimePool(self.releases, self.user_db)
        self.images = Images(self.home, self.source, resolver=self.image_source)
        self.threads = {}
        self.thread_lock = threading.Lock()

    def refresh_source(self):
        if self.custom_source is None:
            from .repositories import configured, source_for
            self.source = source_for(self.root, configured(self.home, self.root))
            self.images.source = self.source

    def image_source(self, commit):
        if self.custom_source is not None:
            return self.custom_source
        from .repositories import source_for
        path = self.home / "snapshots" / commit / "source.json"
        repository = read(path).get("repository") if path.exists() else None
        return source_for(self.root, repository or self.source.repository)

    def set_repository(self, repository):
        from .repositories import canonical, source_for
        repository = canonical(repository)
        with self.thread_lock, self.jobs.lease():
            if any(j["state"] in ("queued", "running") or j.get("battleReleaseStatus") == "testing" for j in self.jobs.list()):
                raise SyncError("SYNC_BUSY", "请等待或取消正在执行的更新任务，再修改仓库", 409)
            write(self.home / "repository.json", {"repository": repository})
            self.source = source_for(self.root, repository)
            self.images.source = self.source
        return {"repository": repository, "branch": "main"}

    def status(self):
        self.refresh_source()
        if not self.jobs.active():
            for job in self.jobs.list():
                if job["state"] == "running":
                    self.jobs.update(job["id"], state="blocked", error={"code": "WORKER_INTERRUPTED", "message": "进程已中断，可从固定提交重试"})
        published = self.jobs.published()
        if (self.home / "baseline.json").exists():
            published.add(read(self.home / "baseline.json")["releaseId"])
        return {"repository": self.source.repository, "branch": "main", "automaticCheckEnabled": False, "currentRelease": self.releases.current(), "lastCheck": read(self.home / "last-check.json") if (self.home / "last-check.json").exists() else None, "jobs": self.jobs.list(), "releases": [{k: v for k, v in r.items() if k != "files"} for r in self.releases.list() if r["releaseId"] in published]}

    def start(self, kind="sync", commit=None, *, background=True, as_of=None, request_key=None):
        self.refresh_source()
        if kind not in ("check", "plan", "sync"):
            raise SyncError("INVALID_MODE", "请选择检查、预演或同步")
        if commit:
            self.source.validate_sha(commit)
        as_of = as_of or datetime.now(timezone(timedelta(hours=8))).date().isoformat()
        from datetime import date
        date.fromisoformat(as_of)
        if as_of < read(self.root / "rulesets/cn-standard-2026-09-16.json")["effectiveAt"]:
            raise SyncError("ENVIRONMENT_NOT_EFFECTIVE", "规则快照尚未生效")
        inputs = {"repository": self.source.repository, "kind": kind, "commit": commit, "baseline": self.releases.current(), "parser": PARSER_VERSION, "adapter": ADAPTER_VERSION, "asOf": as_of, "mappings": digest(read(self.root / "data/sync/mappings.json")), "overrides": digest(read(self.root / "data/sync/overrides.json")), "environment": digest([read(self.root / "rulesets/cn-standard-2026-09-16.json"), read(self.root / "data/cardpool/battle-scope.json")]), "implementation": digest({p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (self.root / "packages/sync").glob("*.py")})}
        # A check with no SHA must be repeatable in time; it is not a stale cached
        # claim that the remote is unchanged. Double clicks coalesce below.
        if not commit and not request_key:
            inputs["requestTime"] = now()
        if request_key:
            inputs["requestKey"] = request_key
        inputs["implementations"] = digest(read(self.root / "data/sync/implementations.json"))
        inputs["sourceCorrections"] = digest(self.source_corrections())
        job = self.jobs.create(digest({"repository": self.source.repository, "requestKey": request_key, "kind": kind, "commit": commit}) if request_key else digest(inputs), inputs)
        if job["state"] in ("completed", "partial", "no_change", "ready"):
            return job
        if background:
            self.launch(job["id"])
        else:
            self.run(job["id"])
        return self.jobs.get(job["id"])

    def launch(self, job_id):
        with self.thread_lock:
            if job_id in self.threads and self.threads[job_id].is_alive():
                return
            thread = threading.Thread(target=self.run, args=(job_id,), daemon=True)
            self.threads[job_id] = thread
            thread.start()

    def checkpoint(self, job_id, stage, **fields):
        if self.jobs.get(job_id)["cancelled"]:
            raise SyncError("CANCELLED", "同步已取消，现有发布保持不变")
        self.jobs.update(job_id, stage=stage, state="running", **fields)

    def baseline(self):
        path = self.home / "baseline.json"
        if not path.exists():
            self.releases.backup_user(self.user_db)
            rid = self.releases.archive(metadata={"kind": "baseline"})
            write(path, {"releaseId": rid})
        return read(path)["releaseId"]

    def run(self, job_id):
        try:
            with self.jobs.lease():
                job = self.jobs.get(job_id)
                from .repositories import source_for
                source = self.custom_source or source_for(self.root, job.get("repository", self.source.repository))
                self.checkpoint(job_id, "fetching")
                sha = job.get("commit") or source.fetch()
                source.validate_sha(sha)
                self.jobs.update(job_id, commit=sha)
                current = self.releases.current()
                if current != job["baseline"]:
                    raise SyncError("RELEASE_CONFLICT", "活动发布已变化，请重新创建同步批次", 409)
                meta = self.releases.manifest(current)["metadata"] if current else {}
                write(self.home / "last-check.json", {"commit": sha, "checkedAt": now(), "changed": sha != meta.get("commit")})
                if job["kind"] == "check":
                    self.jobs.update(job_id, state="completed", stage="checked", changed=sha != meta.get("commit"))
                    return
                fingerprint = digest({k: job.get(k) for k in ("repository", "parser", "adapter", "asOf", "mappings", "overrides", "environment", "implementation", "implementations", "sourceCorrections")})
                if meta.get("commit") == sha and meta.get("syncInputs") == fingerprint:
                    self.jobs.update(job_id, state="no_change", stage="checked", releaseId=current)
                    return
                if meta.get("commit") and meta.get("source", source.repository) == source.repository and not source.is_ancestor(meta["commit"], sha):
                    raise SyncError("UPSTREAM_HISTORY_CHANGED", "上游历史回退或改写，需建立新的迁移预演")
                self.checkpoint(job_id, "validating_source")
                snapshot = self.home / "snapshots" / sha
                if not (snapshot / "source.json").exists():
                    raw, tree = source.snapshot(sha, snapshot)
                else:
                    raw, tree = read(snapshot / "upstream.json"), read(snapshot / "source.json")["tree"]
                self.checkpoint(job_id, "normalizing")
                normalized = normalize(raw, tree, self.source_corrections())
                base_id = current or self.baseline()
                base = self.releases.path(base_id)
                previous = read(self.home / "snapshots" / meta["commit"] / "normalized.json") if meta.get("commit") else None
                self.checkpoint(job_id, "diffing")
                changes = difference(previous, normalized)
                write(snapshot / "normalized.json", normalized)
                write(snapshot / "image-index.json", {cid: {"path": c["imagePath"], "blob": c["imageBlob"]} for cid, c in normalized["cards"].items()})
                prior_migration = read(self.home / "results" / meta["jobId"] / "migration.json") if meta.get("jobId") else None
                self.checkpoint(job_id, "mapping")
                migration = build_catalog(normalized, read(base / "data/catalog/catalog.json"), read(base / "data/catalog/card-details.json"), sha, read(self.root / "data/sync/mappings.json"), read(self.root / "data/sync/overrides.json"), prior_migration, repository=source.repository)
                self.checkpoint(job_id, "adapting")
                implementations = self.implementations()
                effect_root = self.root if implementations else base
                result = adapt(migration, normalized, read(effect_root / "data/simulation/effects.json"), read(effect_root / "data/cardpool/plain-pokemon.json"), read(self.root / "rulesets/cn-standard-2026-09-16.json"), read(self.root / "data/cardpool/battle-scope.json"), job["asOf"], prior_migration, implementations)
                location = self.home / "results" / job_id
                write(location / "migration.json", migration)
                write(location / "adaptation.json", result)
                write(location / "diff.json", changes)
                write(location / "tasks.json", result["tasks"])
                self.checkpoint(job_id, "testing", report=migration["report"], statuses=result["statuses"], tasks=len(result["tasks"]), proposals=len(result["proposals"]), diff={k: {t: len(v) for t, v in values.items()} if isinstance(values, dict) else len(values) if isinstance(values, list) else values for k, values in changes.items()})
                self.validate_catalog(migration["catalog"])
                if job["kind"] == "plan":
                    self.jobs.update(job_id, state="ready", stage="planned", catalogReleaseStatus="not-published", battleReleaseStatus="pending")
                    return
                # Preserve original executable code for existing verified mappings.
                # A new runtime code revision is released only through battle verification.
                old_root = self.releases.root
                self.releases.root = base
                try:
                    rid = self.releases.archive(catalog=migration["catalog"], details=migration["details"], metadata={"kind": "catalog", "commit": sha, "jobId": job_id, "source": source.repository, "syncInputs": fingerprint, "baseline": base_id})
                finally:
                    self.releases.root = old_root
                self.jobs.update(job_id, candidate=rid)
                self.checkpoint(job_id, "ready")
                self.publish(job_id)
        except SyncError as exc:
            self.jobs.update(job_id, state="cancelled" if exc.code == "CANCELLED" else "blocked" if exc.status == 409 or exc.code == "UPSTREAM_HISTORY_CHANGED" else "failed", error={"code": exc.code, "message": exc.message})
        except Exception as exc:
            self.jobs.update(job_id, state="failed", error={"code": "SYNC_INTERNAL", "message": str(exc)[:500]})

    @staticmethod
    def validate_catalog(catalog):
        ids = [c["printingId"] for c in catalog["cards"]]
        if len(ids) != len(set(ids)):
            raise SyncError("DUPLICATE_IDENTITY", "生成目录存在重复身份")
        product_ids = {p["id"] for p in catalog["products"]}
        if any(set(c.get("productIds", [])) - product_ids for c in catalog["cards"]):
            raise SyncError("PRODUCT_REFERENCE", "商品引用不完整")
        if any(e["printingId"] not in set(ids) for t in catalog["templates"] for e in t["entries"]):
            raise SyncError("TEMPLATE_REFERENCE", "模板卡组引用丢失")

    def source_corrections(self):
        path = self.root / "data/sync/source-corrections.json"
        return read(path) if path.exists() else {"schema": 1, "cards": {}}

    def implementations(self):
        records = read(self.root / "data/sync/implementations.json").get("cards", {})
        for cid, record in records.items():
            if not all(record.get(k) for k in ("ruleHash", "effectKey", "engineLine", "testFiles")):
                raise SyncError("IMPLEMENTATION_INCOMPLETE", "机制产物必须绑定规则指纹、效果和定向测试")
            for name in record["testFiles"]:
                if not name.startswith("tests/") or not inside(self.root, name).is_file():
                    raise SyncError("IMPLEMENTATION_TEST_MISSING", "机制产物定向测试不存在")
        return records

    def publish(self, job_id):
        job = self.jobs.get(job_id)
        if not job.get("candidate"):
            raise SyncError("NO_CANDIDATE", "尚无已验证候选")
        if job["cancelled"]:
            raise SyncError("CANCELLED", "任务已取消")
        rid = job["candidate"]
        # Health check uses a disposable database; never creates user state.
        from .runtime import Worker
        worker = Worker(self.releases, rid, self.home / "staging" / (job_id + "-health.sqlite"))
        try:
            response = worker.request("GET", "/api/meta")
            if response["status"] != 200:
                raise SyncError("HEALTH_FAILED", "候选版本健康检查失败")
        finally:
            worker.close()
        self.jobs.update(job_id, stage="publishing")
        self.releases.backup_user(self.user_db)
        self.extend_identity_archive(rid)
        self.releases.activate(rid, job["baseline"], self.jobs.path)
        counts = job.get("statuses", {})
        incomplete = any(n for k, n in counts.items() if k not in ("supported-existing", "outside-environment"))
        self.jobs.update(job_id, state="partial" if incomplete else "completed", stage="published", catalogReleaseStatus="published", battleReleaseStatus="partial" if incomplete else "unchanged", releaseId=rid)

    def extend_identity_archive(self, rid):
        path = self.home / "identity-archive.json"
        old = read(path) if path.exists() else {}
        for c in read(self.releases.path(rid) / "data/catalog/catalog.json")["cards"]:
            pid = c["printingId"]
            variants = {v["variantId"]: v for v in old.get(pid, {}).get("variants", [])}
            variants.update({v["variantId"]: v for v in c.get("variants", [])})
            old[pid] = {**c, "variants": list(variants.values())}
        write(path, old)

    def retry(self, job_id):
        job = self.jobs.get(job_id)
        if job["state"] == "running" and self.jobs.active():
            raise SyncError("JOB_RUNNING", "任务仍在运行", 409)
        with self.jobs.db() as db:
            db.execute("UPDATE jobs SET cancelled=0 WHERE id=?", (job_id,))
        self.launch(job_id)
        return self.jobs.get(job_id)

    def rollback(self, release_id, expected):
        with self.jobs.lease():
            allowed = self.jobs.published()
            if (self.home / "baseline.json").exists():
                allowed.add(read(self.home / "baseline.json")["releaseId"])
            if release_id not in allowed:
                raise SyncError("RELEASE_NOT_PUBLISHED", "候选版本不能通过回滚绕过验收", 409)
            self.releases.manifest(release_id)
            if self.releases.current():
                self.extend_identity_archive(self.releases.current())
            result = self.releases.activate(release_id, expected, self.jobs.path)
            return {k: v for k, v in result.items() if k != "files"}

    def tasks(self, job_id):
        self.jobs.get(job_id)
        p = self.home / "results" / job_id / "tasks.json"
        return read(p) if p.exists() else []

    def close(self):
        self.pool.close()
