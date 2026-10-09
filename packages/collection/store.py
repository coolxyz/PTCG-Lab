from __future__ import annotations
import json, sqlite3, uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from packages.collection.domain import *


def now():
    return datetime.now(timezone.utc).isoformat()


def uid():
    return uuid.uuid4().hex


class Store:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value INTEGER NOT NULL);
            INSERT OR IGNORE INTO meta VALUES('collectionVersion',0);
            CREATE TABLE IF NOT EXISTS collection(printing_id TEXT NOT NULL,condition TEXT NOT NULL,quantity INTEGER NOT NULL CHECK(quantity>=0),notes TEXT NOT NULL,wishlist INTEGER NOT NULL,PRIMARY KEY(printing_id,condition));
            CREATE TABLE IF NOT EXISTS changes(id TEXT PRIMARY KEY,batch_id TEXT,body TEXT NOT NULL,created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS imports(id TEXT PRIMARY KEY,fingerprint TEXT UNIQUE NOT NULL,status TEXT NOT NULL,body TEXT NOT NULL,created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS decks(id TEXT PRIMARY KEY,name TEXT NOT NULL,entries TEXT NOT NULL,version INTEGER NOT NULL,updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS revisions(id TEXT PRIMARY KEY,deck_id TEXT NOT NULL,number INTEGER NOT NULL,hash TEXT NOT NULL,body TEXT NOT NULL,created_at TEXT NOT NULL,UNIQUE(deck_id,number),UNIQUE(deck_id,hash));
            CREATE TABLE IF NOT EXISTS deleted_revisions(id TEXT PRIMARY KEY,deleted_at TEXT NOT NULL);
            PRAGMA user_version=1;
            """)

    @contextmanager
    def db(self, write=False):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except sqlite3.IntegrityError as exc:
            db.rollback()
            if "VARIANT_ALLOCATION_CONFLICT" in str(exc):
                raise DomainError("VARIANT_ALLOCATION_CONFLICT", "请先在数据更新页面减少收藏版本分配，再减少持有总量", 409) from exc
            raise
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _collection(db):
        return [
            {
                "printingId": r["printing_id"],
                "condition": r["condition"],
                "quantity": r["quantity"],
                "notes": r["notes"],
                "wishlist": bool(r["wishlist"]),
            }
            for r in db.execute(
                "SELECT * FROM collection ORDER BY printing_id,condition"
            )
        ]

    @staticmethod
    def _version(db):
        return db.execute(
            "SELECT value FROM meta WHERE key='collectionVersion'"
        ).fetchone()[0]

    def collection(self):
        with self.db() as db:
            return {"version": self._version(db), "entries": self._collection(db)}

    @staticmethod
    def _write_entry(db, r):
        require(r["printingId"] in CARDS, "UNKNOWN_PRINTING", "卡牌版本不存在")
        require(r["condition"] in CONDITIONS, "BAD_CONDITION", "请选择有效品相")
        require(
            type(r["quantity"]) is int and 0 <= r["quantity"] <= 9999,
            "BAD_QUANTITY",
            "收藏数量须为0至9999整数",
        )
        require(len(r["notes"]) <= 500, "NOTES_TOO_LONG", "备注最多500字")
        db.execute(
            "INSERT OR REPLACE INTO collection VALUES(?,?,?,?,?)",
            (
                r["printingId"],
                r["condition"],
                r["quantity"],
                r["notes"],
                int(r["wishlist"]),
            ),
        )

    def update_collection(self, changes, expected):
        require(1 <= len(changes) <= 500, "BAD_CHANGES", "请选择1至500条收藏记录")
        require(
            len({(c["printingId"], c["condition"]) for c in changes}) == len(changes),
            "DUPLICATE_CHANGE",
            "同一批次不能重复设置同版次与品相",
        )
        with self.db(True) as db:
            require(
                self._version(db) == expected,
                "VERSION_CONFLICT",
                "收藏已在其他页面变更，请刷新后重试",
                409,
            )
            before = self._collection(db)
            for r in changes:
                self._write_entry(db, r)
            db.execute(
                "INSERT INTO changes VALUES(?,?,?,?)",
                (
                    uid(),
                    None,
                    json.dumps(
                        {"before": before, "after": changes}, ensure_ascii=False
                    ),
                    now(),
                ),
            )
            db.execute("UPDATE meta SET value=value+1 WHERE key='collectionVersion'")
        return self.collection()

    def import_collection(self, text, resolutions, expected):
        preview = parse_import(text, "collection", resolutions)
        require(
            preview["ready"], "IMPORT_UNRESOLVED", "请先解决导入中的数量错误和版本歧义"
        )
        entries = sorted(
            preview["entries"], key=lambda e: (e["printingId"], e["condition"])
        )
        fingerprint = content_hash(entries)
        with self.db(True) as db:
            existing = db.execute(
                "SELECT * FROM imports WHERE fingerprint=?", (fingerprint,)
            ).fetchone()
            if existing:
                require(
                    existing["status"] == "applied",
                    "BATCH_ALREADY_UNDONE",
                    "这批导入已撤销；需要重新导入时请使用批量编辑",
                    409,
                )
                return {"id": existing["id"], "duplicate": True}
            require(
                self._version(db) == expected,
                "VERSION_CONFLICT",
                "收藏已变更，请刷新后重试",
                409,
            )
            batch = uid()
            deltas = []
            current = {
                (r["printingId"], r["condition"]): r for r in self._collection(db)
            }
            for r in entries:
                previous = current.get((r["printingId"], r["condition"]))
                result = {
                    **r,
                    "quantity": r["quantity"]
                    + (previous["quantity"] if previous else 0),
                    "wishlist": r["wishlist"]
                    or (previous["wishlist"] if previous else False),
                }
                if previous and not result["notes"]:
                    result["notes"] = previous["notes"]
                self._write_entry(db, result)
                deltas.append({"entry": r, "previous": previous, "applied": result})
            db.execute(
                "INSERT INTO imports VALUES(?,?,?,?,?)",
                (
                    batch,
                    fingerprint,
                    "applied",
                    json.dumps(deltas, ensure_ascii=False),
                    now(),
                ),
            )
            db.execute(
                "INSERT INTO changes VALUES(?,?,?,?)",
                (uid(), batch, json.dumps(deltas, ensure_ascii=False), now()),
            )
            db.execute("UPDATE meta SET value=value+1 WHERE key='collectionVersion'")
            return {"id": batch, "duplicate": False}

    def batches(self):
        with self.db() as db:
            return [
                {
                    "id": r["id"],
                    "status": r["status"],
                    "createdAt": r["created_at"],
                    "quantity": sum(
                        x["entry"]["quantity"] for x in json.loads(r["body"])
                    ),
                }
                for r in db.execute("SELECT * FROM imports ORDER BY created_at DESC")
            ]

    def undo_import(self, batch, expected):
        with self.db(True) as db:
            r = db.execute("SELECT * FROM imports WHERE id=?", (batch,)).fetchone()
            require(r is not None, "NOT_FOUND", "导入批次不存在", 404)
            if r["status"] == "undone":
                return {"undone": True, "duplicate": True}
            require(
                self._version(db) == expected,
                "VERSION_CONFLICT",
                "收藏已变更，请刷新后重试",
                409,
            )
            current = {
                (e["printingId"], e["condition"]): e for e in self._collection(db)
            }
            for delta in json.loads(r["body"]):
                e = delta["entry"]
                c = current[(e["printingId"], e["condition"])]
                require(
                    c["quantity"] >= e["quantity"],
                    "UNDO_CONFLICT",
                    "导入的卡牌已有减量，无法完整撤销；请先核对收藏",
                    409,
                )
                result = {**c, "quantity": c["quantity"] - e["quantity"]}
                if c["notes"] == delta["applied"]["notes"]:
                    result["notes"] = (delta["previous"] or {}).get("notes", "")
                if c["wishlist"] == delta["applied"]["wishlist"]:
                    result["wishlist"] = (delta["previous"] or {}).get(
                        "wishlist", False
                    )
                self._write_entry(db, result)
            db.execute("UPDATE imports SET status='undone' WHERE id=?", (batch,))
            db.execute(
                "INSERT INTO changes VALUES(?,?,?,?)",
                (uid(), batch, json.dumps({"undo": True}), now()),
            )
            db.execute("UPDATE meta SET value=value+1 WHERE key='collectionVersion'")
        return {"undone": True, "duplicate": False}

    @staticmethod
    def _deck(r):
        return {
            "id": r["id"],
            "name": r["name"],
            "entries": json.loads(r["entries"]),
            "version": r["version"],
            "updatedAt": r["updated_at"],
        }

    def decks(self):
        with self.db() as db:
            return [
                self._deck(r)
                for r in db.execute("SELECT * FROM decks ORDER BY updated_at DESC")
            ]

    def deck(self, id):
        with self.db() as db:
            r = db.execute("SELECT * FROM decks WHERE id=?", (id,)).fetchone()
            require(r is not None, "NOT_FOUND", "卡组不存在", 404)
            return self._deck(r)

    def create_deck(self, name, entries):
        require(bool(name.strip()) and len(name) <= 80, "BAD_NAME", "名称需为1至80字")
        entries = normalize(entries)
        id = uid()
        with self.db(True) as db:
            db.execute(
                "INSERT INTO decks VALUES(?,?,?,?,?)",
                (id, name.strip(), json.dumps(entries), 1, now()),
            )
        return self.deck(id)

    def save_deck(self, id, name, entries, version):
        require(bool(name.strip()) and len(name) <= 80, "BAD_NAME", "名称需为1至80字")
        entries = normalize(entries)
        with self.db(True) as db:
            found = db.execute("SELECT version FROM decks WHERE id=?", (id,)).fetchone()
            require(found is not None, "NOT_FOUND", "卡组不存在", 404)
            require(
                found[0] == version,
                "VERSION_CONFLICT",
                "卡组已被其他页面修改，请重新打开后再编辑",
                409,
            )
            db.execute(
                "UPDATE decks SET name=?,entries=?,version=version+1,updated_at=? WHERE id=?",
                (name.strip(), json.dumps(entries), now(), id),
            )
        return self.deck(id)

    def delete_deck(self, id, version):
        with self.db(True) as db:
            found = db.execute("SELECT version FROM decks WHERE id=?", (id,)).fetchone()
            require(found is not None, "NOT_FOUND", "卡组不存在", 404)
            require(found[0] == version, "VERSION_CONFLICT",
                    "卡组已被其他页面修改，请刷新列表后再删除", 409)
            # Immutable revisions are still referenced by existing matches/replays.
            db.execute("DELETE FROM decks WHERE id=?", (id,))
        return {"deleted": id}

    def delete_revision(self, deck_id, revision_id, version):
        with self.db(True) as db:
            deck = db.execute("SELECT version FROM decks WHERE id=?", (deck_id,)).fetchone()
            require(deck is not None, "NOT_FOUND", "卡组不存在", 404)
            require(deck[0] == version, "VERSION_CONFLICT", "卡组已更新，请刷新后再删除", 409)
            row = db.execute("SELECT id FROM revisions WHERE id=? AND deck_id=?", (revision_id, deck_id)).fetchone()
            require(row is not None, "NOT_FOUND", "版本不存在", 404)
            # Keep immutable content for matches and idempotent receipts.
            db.execute("INSERT OR IGNORE INTO deleted_revisions VALUES(?,?)", (revision_id, now()))
        return {"deleted": revision_id}

    def freeze(self, id, version):
        with self.db(True) as db:
            r = db.execute("SELECT * FROM decks WHERE id=?", (id,)).fetchone()
            require(r is not None, "NOT_FOUND", "卡组不存在", 404)
            d = self._deck(r)
            require(
                d["version"] == version, "VERSION_CONFLICT", "请先保存当前草稿", 409
            )
            result = validation(d["entries"])
            require(
                result["legality"] == "valid",
                "DECK_INVALID",
                "请先解决卡组结构与赛制错误",
            )
            body = {
                "entries": d["entries"],
                "catalogVersion": CATALOG_VERSION,
                "formatId": RAW["formatId"],
                "formatHash": content_hash(RULES),
                "validation": result,
                "effectReleaseVersion": result["effectReleaseVersion"],
                "effectMappings": {
                    e["printingId"]: CARDS[e["printingId"]]["engineId"]
                    for e in d["entries"]
                },
            }
            h = content_hash(body)
            # Deck identity is its normalized card list, independent of release metadata.
            for old in db.execute("SELECT * FROM revisions WHERE deck_id=? ORDER BY number", (id,)):
                saved = json.loads(old["body"])
                require(content_hash(saved) == old["hash"], "REVISION_CORRUPT", "卡组版本校验失败", 409)
                if sorted(saved["entries"], key=lambda e: e["printingId"]) == normalize(d["entries"]):
                    db.execute("DELETE FROM deleted_revisions WHERE id=?", (old["id"],))
                    return self._revision(old)
            number = (
                db.execute(
                    "SELECT COUNT(*) FROM revisions WHERE deck_id=?", (id,)
                ).fetchone()[0]
                + 1
            )
            rid = uid()
            db.execute(
                "INSERT INTO revisions VALUES(?,?,?,?,?,?)",
                (rid, id, number, h, json.dumps(body, ensure_ascii=False), now()),
            )
            return self._revision(
                db.execute("SELECT * FROM revisions WHERE id=?", (rid,)).fetchone()
            )

    @staticmethod
    def _revision(r):
        return {
            "id": r["id"],
            "number": r["number"],
            "hash": r["hash"],
            "createdAt": r["created_at"],
            **json.loads(r["body"]),
        }

    def revisions(self, id):
        self.deck(id)
        with self.db() as db:
            return [
                self._revision(r)
                for r in db.execute(
                    "SELECT * FROM revisions WHERE deck_id=? AND id NOT IN (SELECT id FROM deleted_revisions) ORDER BY number DESC",
                    (id,),
                )
            ]
