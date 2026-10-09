"""Variant allocation preserves the existing printing-level inventory total."""
import sqlite3
from contextlib import closing
from pathlib import Path

from .common import SyncError, read
from .finishes import label as finish_label


class Variants:
    def __init__(self, service):
        self.service = service

    def initialize(self):
        with closing(sqlite3.connect(self.service.user_db)) as db, db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='collection'").fetchone():
                return
            db.executescript("""
            CREATE TABLE IF NOT EXISTS collection_variants(printing_id TEXT NOT NULL,condition TEXT NOT NULL,variant_id TEXT NOT NULL,quantity INTEGER NOT NULL CHECK(quantity>=0),PRIMARY KEY(printing_id,condition,variant_id));
            CREATE TRIGGER IF NOT EXISTS variant_total_insert BEFORE INSERT ON collection WHEN NEW.quantity < (SELECT COALESCE(SUM(quantity),0) FROM collection_variants WHERE printing_id=NEW.printing_id AND condition=NEW.condition) BEGIN SELECT RAISE(ABORT,'VARIANT_ALLOCATION_CONFLICT'); END;
            CREATE TRIGGER IF NOT EXISTS variant_total_update BEFORE UPDATE OF quantity ON collection WHEN NEW.quantity < (SELECT COALESCE(SUM(quantity),0) FROM collection_variants WHERE printing_id=NEW.printing_id AND condition=NEW.condition) BEGIN SELECT RAISE(ABORT,'VARIANT_ALLOCATION_CONFLICT'); END;
            CREATE TRIGGER IF NOT EXISTS variant_total_delete BEFORE DELETE ON collection WHEN (SELECT COALESCE(SUM(quantity),0) FROM collection_variants WHERE printing_id=OLD.printing_id AND condition=OLD.condition)>0 BEGIN SELECT RAISE(ABORT,'VARIANT_ALLOCATION_CONFLICT'); END;
            """)

    def catalog(self):
        path = self.service.home / "identity-archive.json"
        cards = read(path) if path.exists() else {}
        release = self.service.releases.current()
        source = self.service.releases.path(release) if release else self.service.root
        for card in read(source / "data/catalog/catalog.json")["cards"]:
            pid = card["printingId"]
            variants = {v["variantId"]: v for v in cards.get(pid, {}).get("variants", [])}
            variants.update({v["variantId"]: v for v in card.get("variants", [])})
            cards[pid] = {**card, "variants": list(variants.values())}
        return cards

    def view(self, printing_id=None):
        self.initialize()
        cards = self.catalog()
        with closing(sqlite3.connect(self.service.user_db)) as db:
            db.row_factory = sqlite3.Row
            version = db.execute("SELECT value FROM meta WHERE key='collectionVersion'").fetchone()[0]
            entries = []
            for row in db.execute("SELECT * FROM collection WHERE quantity>0"):
                if printing_id and row['printing_id'] != printing_id:
                    continue
                c = cards.get(row["printing_id"], {})
                allocations = [dict(r) for r in db.execute("SELECT variant_id AS variantId,quantity FROM collection_variants WHERE printing_id=? AND condition=? AND quantity>0", (row["printing_id"], row["condition"]))]
                entries.append({"printingId": row["printing_id"], "condition": row["condition"], "quantity": row["quantity"], "unassigned": row["quantity"]-sum(r["quantity"] for r in allocations), "name": c.get("cnName", row["printing_id"]), "variants": self.display_variants(c), "allocations": allocations})
            result = {"version": version, "entries": entries}
            if printing_id:
                if printing_id not in cards:
                    raise SyncError('UNKNOWN_PRINTING','卡牌不存在',404)
                result['variants'] = self.display_variants(cards[printing_id])
            return result

    @staticmethod
    def display_variants(card):
        result=[]
        for variant in card.get('variants',[]):
            result.append({**variant,'finishLabel':finish_label(card,variant)})
        # Sort the presentation only; keep stored identities and holdings intact.
        if str(card.get('productCode', '')).startswith('151C'):
            order = {'普通版': 0, '精灵球版': 1, '大师球版': 2}
            result.sort(key=lambda variant: order.get(variant['finishLabel'], 3))
        return result

    def set_holding(self, printing_id, condition, variant_id, quantity, expected):
        """Adjust one physical finish and its parent total in one transaction."""
        from packages.collection.domain import CONDITIONS
        self.initialize()
        card=self.catalog().get(printing_id,{})
        if condition not in CONDITIONS:
            raise SyncError('BAD_CONDITION','请选择有效品相')
        if variant_id not in {v['variantId'] for v in card.get('variants',[])}:
            raise SyncError('UNKNOWN_VARIANT','该卡牌不包含所选收藏版本')
        if type(quantity) is not int or not 0<=quantity<=9999:
            raise SyncError('BAD_QUANTITY','版本数量须为 0 至 9999')
        with closing(sqlite3.connect(self.service.user_db)) as db, db:
            db.execute('BEGIN IMMEDIATE')
            version=db.execute("SELECT value FROM meta WHERE key='collectionVersion'").fetchone()[0]
            if version!=expected:
                raise SyncError('VERSION_CONFLICT','收藏已变化，请刷新后重试',409)
            key=(printing_id,condition)
            old=db.execute('SELECT quantity FROM collection_variants WHERE printing_id=? AND condition=? AND variant_id=?',(*key,variant_id)).fetchone()
            row=db.execute('SELECT quantity FROM collection WHERE printing_id=? AND condition=?',key).fetchone()
            total=(row[0] if row else 0)+quantity-(old[0] if old else 0)
            if not 0<=total<=9999:
                raise SyncError('BAD_QUANTITY','该品相的总量须为 0 至 9999')
            # Lower allocation first so the legacy total guard stays effective.
            if old and quantity<old[0]:
                db.execute('UPDATE collection_variants SET quantity=? WHERE printing_id=? AND condition=? AND variant_id=?',(quantity,*key,variant_id))
            db.execute("INSERT INTO collection VALUES(?,?,?,'',0) ON CONFLICT(printing_id,condition) DO UPDATE SET quantity=excluded.quantity",(*key,total))
            db.execute('INSERT OR REPLACE INTO collection_variants VALUES(?,?,?,?)',(*key,variant_id,quantity))
            db.execute("UPDATE meta SET value=value+1 WHERE key='collectionVersion'")
        return self.view(printing_id)

    def set(self, printing_id, condition, variant_id, quantity, expected):
        self.initialize()
        card = self.catalog().get(printing_id, {})
        if variant_id not in {v["variantId"] for v in card.get("variants", [])}:
            raise SyncError("UNKNOWN_VARIANT", "该卡牌不包含所选收藏版本")
        if type(quantity) is not int or not 0 <= quantity <= 9999:
            raise SyncError("BAD_QUANTITY", "版本数量须为 0 至 9999")
        with closing(sqlite3.connect(self.service.user_db)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            version = db.execute("SELECT value FROM meta WHERE key='collectionVersion'").fetchone()[0]
            if version != expected:
                raise SyncError("VERSION_CONFLICT", "收藏已变化，请刷新后重试", 409)
            row = db.execute("SELECT quantity FROM collection WHERE printing_id=? AND condition=?", (printing_id, condition)).fetchone()
            other = db.execute("SELECT COALESCE(SUM(quantity),0) FROM collection_variants WHERE printing_id=? AND condition=? AND variant_id<>?", (printing_id, condition, variant_id)).fetchone()[0]
            if not row or other + quantity > row[0]:
                raise SyncError("INSUFFICIENT_UNASSIGNED", "未分配数量不足；分配版本不会增加持有总量", 409)
            db.execute("INSERT OR REPLACE INTO collection_variants VALUES(?,?,?,?)", (printing_id, condition, variant_id, quantity))
            db.execute("UPDATE meta SET value=value+1 WHERE key='collectionVersion'")
        return self.view()
