"""Immutable release directories and one atomic activation point."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import uuid
from contextlib import closing

from .common import ROOT, SyncError, digest, read, write, now, inside

DATA_FILES = ["data/catalog/catalog.json", "data/catalog/card-details.json", "data/engine/catalog.json", "data/simulation/effects.json", "data/cardpool/plain-pokemon.json", "data/cardpool/battle-scope.json", "data/cardpool/source-exceptions.json", "rulesets/cn-standard-2026-09-16.json", "artifacts/engine/overlay-hashes.json", "scripts/engine/build_engine.py", "apps/api/requirements.lock"]


class Releases:
    def __init__(self, home, root=ROOT):
        self.home, self.root = Path(home), Path(root)
        self.directory = self.home / "releases"
        self.directory.mkdir(parents=True, exist_ok=True)

    def current(self):
        path = self.home / "current.json"
        return read(path)["releaseId"] if path.exists() else None

    def path(self, release_id):
        if not isinstance(release_id, str) or len(release_id) != 64 or any(c not in "0123456789abcdef" for c in release_id):
            raise SyncError("INVALID_RELEASE", "发布编号无效")
        return inside(self.directory, release_id)

    def manifest(self, release_id):
        path = self.path(release_id) / "manifest.json"
        if not path.exists():
            raise SyncError("RELEASE_NOT_FOUND", "发布不存在", 404)
        return read(path)

    def list(self):
        return sorted([read(p) for p in self.directory.glob("*/manifest.json")], key=lambda r: r["createdAt"], reverse=True)

    def archive(self, catalog=None, details=None, effects=None, plain=None, metadata=None):
        stage = self.home / "staging" / ("release-" + uuid.uuid4().hex)
        stage.mkdir(parents=True)
        # Only first-party runtime code, checked overlay and fixed data dependencies.
        for folder in ("packages", "apps/api", "runtime/engine"):
            src = self.root / folder
            shutil.copytree(src, stage / folder, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        for name in DATA_FILES:
            target = stage / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self.root / name, target)
        # Preserve review provenance with the executable candidate. Older
        # releases without these optional records remain readable.
        if (self.root / 'data/sync').exists():
            shutil.copytree(self.root / 'data/sync', stage / 'data/sync')
        old_history = self.root / "data/cardpool/release-history"
        if old_history.exists():
            shutil.copytree(old_history, stage / "data/cardpool/release-history")
        for name, value in (("catalog/catalog", catalog), ("catalog/card-details", details), ("simulation/effects", effects), ("cardpool/plain-pokemon", plain)):
            if value is not None:
                write(stage / f"data/{name}.json", value)
        # Compute the exact archived implementation ID, without importing the engine.
        code = "from pathlib import Path; import hashlib,json; r=Path('.'); f=sorted((r/'packages/rules').glob('*.py')); f += [r/p for p in ['packages/engine_adapter/base.py','packages/engine_adapter/cn_format.py','scripts/engine/build_engine.py','artifacts/engine/overlay-hashes.json','data/engine/catalog.json','rulesets/cn-standard-2026-09-16.json','data/cardpool/plain-pokemon.json']]; print(hashlib.sha256(json.dumps({p.as_posix():p.read_text(encoding='utf-8') for p in f},ensure_ascii=False,sort_keys=True).encode()).hexdigest())"
        engine = subprocess.run([sys.executable, "-X", "utf8", "-c", code], cwd=stage, capture_output=True, text=True, check=True).stdout.strip()
        effect_path = stage / "data/simulation/effects.json"
        if effects is not None:
            value = read(effect_path)
            value["engineVersion"] = engine
            write(effect_path, value)
        # SQLite is a derived query artifact of this exact catalog, never an
        # independent mutable source of truth.
        catalog_value = read(stage / "data/catalog/catalog.json")
        with closing(sqlite3.connect(stage / "data/catalog/catalog.sqlite")) as db, db:
            db.executescript("""
            PRAGMA foreign_keys=ON;
            CREATE TABLE products(id TEXT PRIMARY KEY,name TEXT,release_date TEXT,source_url TEXT,revision INTEGER,card_count INTEGER);
            CREATE TABLE cards(printing_id TEXT PRIMARY KEY,product_code TEXT,collector_number TEXT,name TEXT,category TEXT,effect_status TEXT,metadata_json TEXT NOT NULL);
            CREATE TABLE card_products(printing_id TEXT REFERENCES cards,product_id TEXT REFERENCES products,PRIMARY KEY(printing_id,product_id));
            CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT);
            CREATE INDEX cards_name ON cards(name);
            """)
            db.executemany("INSERT INTO products VALUES(?,?,?,?,?,?)", [(p["id"],p["name"],p.get("releasedAt"),p.get("source",{}).get("url"),p.get("source",{}).get("revision"),p.get("tableCount")) for p in catalog_value["products"]])
            db.executemany("INSERT INTO cards VALUES(?,?,?,?,?,?,?)", [(c["printingId"],c.get("productCode"),c.get("collectorNumber"),c["cnName"],c.get("category"),c["effectStatus"],__import__("json").dumps(c,ensure_ascii=False)) for c in catalog_value["cards"]])
            db.executemany("INSERT INTO card_products VALUES(?,?)", [(c["printingId"],p) for c in catalog_value["cards"] for p in set(c.get("productIds", []))])
            db.execute("INSERT INTO metadata VALUES('version',?)", (catalog_value["version"],))
            if db.execute("PRAGMA foreign_key_check").fetchall() or db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise SyncError("CATALOG_DATABASE_INVALID", "生成数据库引用或完整性校验失败")
        hashes = {p.relative_to(stage).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in stage.rglob("*") if p.is_file()}
        release_id = digest({"files": hashes, "metadata": metadata or {}})
        manifest = {"releaseId": release_id, "createdAt": now(), "metadata": metadata or {}, "files": hashes, "catalogVersion": read(stage / "data/catalog/catalog.json")["version"], "engineVersion": engine, "effectVersion": hashes["data/simulation/effects.json"]}
        write(stage / "manifest.json", manifest)
        target = self.path(release_id)
        if not target.exists():
            stage.rename(target)
        # An identical candidate already exists. Keep stage for forensic cleanup;
        # never recursively remove computed paths during publication.
        return release_id

    def verify(self, release_id):
        manifest = self.manifest(release_id)
        root = self.path(release_id)
        if digest({"files": manifest["files"], "metadata": manifest["metadata"]}) != release_id:
            raise SyncError("RELEASE_CORRUPT", "发布清单身份校验失败")
        for name, expected in manifest["files"].items():
            path = inside(root, name)
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                raise SyncError("RELEASE_CORRUPT", "发布文件校验失败：" + name)
        return manifest

    def activate(self, release_id, expected, database):
        self.verify(release_id)
        # The same database lock is used for publish and rollback, even across processes.
        with closing(sqlite3.connect(database, timeout=30)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            if self.current() != expected:
                raise SyncError("RELEASE_CONFLICT", "活动版本已改变，请重新计算差异", 409)
            write(self.home / "current.json", {"releaseId": release_id, "previous": expected, "activatedAt": now()})
            db.execute("INSERT INTO activations(release_id,previous,created_at) VALUES(?,?,?)", (release_id, expected, now()))
        return self.manifest(release_id)

    def backup_user(self, path):
        path = Path(path)
        if not path.exists():
            return None
        target = self.home / "backups" / uuid.uuid4().hex
        target.mkdir(parents=True)
        with closing(sqlite3.connect(path)) as src, closing(sqlite3.connect(target / "user.sqlite")) as dst:
            src.backup(dst)
            if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise SyncError("BACKUP_FAILED", "用户数据库备份校验失败")
        images = path.parent / (path.stem + "-card-images")
        if images.exists():
            shutil.copytree(images, target / "card-images")
        write(target / "backup.json", {"createdAt": now(), "source": str(path), "databaseSha256": hashlib.sha256((target / "user.sqlite").read_bytes()).hexdigest()})
        return str(target)
