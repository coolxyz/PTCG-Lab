use crate::{
    catalog::{hash, id, Catalog, Entry},
    error::{Error, Result},
};
use rusqlite::{params, Connection, OptionalExtension};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{fs, path::Path};

#[derive(Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Deck {
    pub id: String,
    pub name: String,
    pub entries: Vec<Entry>,
    pub version: u64,
}

pub struct Store {
    pub(crate) db: Connection,
}
impl Store {
    pub fn open(root: &Path) -> Result<Self> {
        fs::create_dir_all(root)?;
        let path = root.join("mobile.sqlite");
        if fs::symlink_metadata(&path).is_ok_and(|m| !m.is_file() || m.file_type().is_symlink()) {
            return Err(Error::new("UNSAFE_STORAGE", "用户数据库路径无效"));
        }
        let mut db = Connection::open(path)?;
        db.busy_timeout(std::time::Duration::from_secs(3))?;
        let schema: u32 = db.pragma_query_value(None, "user_version", |r| r.get(0))?;
        if schema > 2 {
            return Err(Error::new(
                "NEWER_DATABASE",
                "此数据由较新版本创建，请升级 App；不会重置用户数据",
            ));
        }
        db.execute_batch(
            "PRAGMA foreign_keys=ON; PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL;",
        )?;
        if schema == 0 {
            let tables:u64=db.query_row("SELECT count(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'",[],|r|r.get(0))?;
            if tables != 0 {
                return Err(Error::new(
                    "INVALID_STORAGE",
                    "未识别的已有数据库；不会创建或覆盖资料表",
                ));
            }
            let tx = db.transaction()?;
            tx.execute_batch("CREATE TABLE decks(id TEXT PRIMARY KEY,name TEXT NOT NULL,entries TEXT NOT NULL,version INTEGER NOT NULL);
                CREATE TABLE revisions(id TEXT PRIMARY KEY,deck_id TEXT NOT NULL,catalog_version TEXT NOT NULL,entries TEXT NOT NULL,content_hash TEXT NOT NULL);
                CREATE TABLE holdings(printing_id TEXT PRIMARY KEY,quantity INTEGER NOT NULL CHECK(quantity BETWEEN 0 AND 9999),wishlist INTEGER NOT NULL);
                CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                INSERT INTO settings VALUES('collectionVersion','0');
                PRAGMA user_version=1;")?;
            tx.commit()?;
        }
        let integrity: String = db.query_row("PRAGMA quick_check(1)", [], |r| r.get(0))?;
        if integrity != "ok" {
            return Err(Error::new(
                "INVALID_STORAGE",
                "数据库校验失败；原资料已保留",
            ));
        }
        if schema <= 1 {
            for sql in [
                "SELECT id,name,entries,version FROM decks LIMIT 0",
                "SELECT id,deck_id,catalog_version,entries,content_hash FROM revisions LIMIT 0",
                "SELECT key,value FROM settings LIMIT 0",
            ] {
                db.prepare(sql).map_err(|_| {
                    Error::new("INVALID_STORAGE", "迁移前数据库结构无效；未更改原资料")
                })?;
            }
            let tx = db.transaction()?;
            tx.execute_batch("ALTER TABLE holdings RENAME TO holdings_v1;
                CREATE TABLE holdings(printing_id TEXT NOT NULL,condition TEXT NOT NULL,quantity INTEGER NOT NULL CHECK(quantity BETWEEN 0 AND 9999),notes TEXT NOT NULL,wishlist INTEGER NOT NULL,PRIMARY KEY(printing_id,condition));
                INSERT INTO holdings SELECT printing_id,'未标注',quantity,'',wishlist FROM holdings_v1;
                DROP TABLE holdings_v1;
                CREATE TABLE collection_batches(id TEXT PRIMARY KEY,fingerprint TEXT UNIQUE NOT NULL,status TEXT NOT NULL,deltas TEXT NOT NULL,created_at INTEGER NOT NULL);
                PRAGMA user_version=2;")?;
            tx.commit()?;
        }
        for sql in [
            "SELECT id,name,entries,version FROM decks LIMIT 0",
            "SELECT id,deck_id,catalog_version,entries,content_hash FROM revisions LIMIT 0",
            "SELECT printing_id,condition,quantity,notes,wishlist FROM holdings LIMIT 0",
            "SELECT id,fingerprint,status,deltas,created_at FROM collection_batches LIMIT 0",
            "SELECT key,value FROM settings LIMIT 0",
        ] {
            db.prepare(sql)
                .map_err(|_| Error::new("INVALID_STORAGE", "数据库结构无效；不会重建用户资料"))?;
        }
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            for name in ["mobile.sqlite", "mobile.sqlite-wal", "mobile.sqlite-shm"] {
                let file = root.join(name);
                if file.exists() {
                    fs::set_permissions(&file, fs::Permissions::from_mode(0o600))?;
                    crate::protection::secure_file(&file)?;
                }
            }
            fs::set_permissions(root, fs::Permissions::from_mode(0o700))?;
        }
        Ok(Self { db })
    }
    pub fn decks(&self) -> Result<Vec<Deck>> {
        let mut statement = self
            .db
            .prepare("SELECT id,name,entries,version FROM decks ORDER BY rowid DESC")?;
        let mut result = Vec::new();
        for row in statement.query_map([], |r| {
            Ok((
                r.get::<_, String>(0)?,
                r.get::<_, String>(1)?,
                r.get::<_, String>(2)?,
                r.get::<_, u64>(3)?,
            ))
        })? {
            let (id, name, entries, version) = row?;
            result.push(Deck {
                id,
                name,
                entries: serde_json::from_str(&entries)?,
                version,
            });
        }
        Ok(result)
    }
    pub fn deck(&self, key: &str) -> Result<Deck> {
        self.decks()?
            .into_iter()
            .find(|r| r.id == key)
            .ok_or(Error::new("DECK_NOT_FOUND", "找不到这个卡组"))
    }
    pub fn save(
        &mut self,
        catalog: &Catalog,
        key: Option<String>,
        name: String,
        entries: Vec<Entry>,
        expected: Option<u64>,
    ) -> Result<Deck> {
        let name = name.trim().to_owned();
        if name.is_empty() || name.chars().count() > 80 {
            return Err(Error::new("BAD_NAME", "卡组名称须为 1 至 80 个字"));
        }
        let entries = catalog.normalize(&entries)?;
        let body = serde_json::to_string(&entries)?;
        let tx = self.db.transaction()?;
        let (key, version) = if let Some(key) = key {
            let expected = expected.ok_or(Error::new("VERSION_REQUIRED", "更新卡组需要版本号"))?;
            if expected >= 9007199254740991 {
                return Err(Error::new("VERSION_CONFLICT", "卡组版本无效"));
            }
            let changed = tx.execute(
                "UPDATE decks SET name=?1,entries=?2,version=version+1 WHERE id=?3 AND version=?4",
                params![name, body, key, expected],
            )?;
            if changed != 1 {
                return Err(Error::new(
                    "VERSION_CONFLICT",
                    "卡组已变化，请重新加载后再保存",
                ));
            }
            (key, expected + 1)
        } else {
            if expected.is_some() {
                return Err(Error::new("INVALID_REQUEST", "新卡组不接受旧版本号"));
            }
            let count: u64 = tx.query_row("SELECT count(*) FROM decks", [], |r| r.get(0))?;
            if count >= 1000 {
                return Err(Error::new(
                    "STORAGE_CAPACITY",
                    "本机卡组已达 1000 个，请先整理",
                ));
            }
            let key = id()?;
            tx.execute(
                "INSERT INTO decks VALUES(?1,?2,?3,1)",
                params![key, name, body],
            )?;
            (key, 1)
        };
        tx.commit()?;
        Ok(Deck {
            id: key,
            name,
            entries,
            version,
        })
    }
    pub fn delete(&mut self, key: String, expected: u64) -> Result<Value> {
        if self.db.execute(
            "DELETE FROM decks WHERE id=?1 AND version=?2",
            params![key, expected],
        )? != 1
        {
            return Err(Error::new(
                "VERSION_CONFLICT",
                "卡组已变化，请重新加载后再删除",
            ));
        }
        // Frozen revisions remain immutable even when their editable draft is removed.
        Ok(json!({"deleted":true}))
    }
    pub fn freeze(&mut self, catalog: &Catalog, key: String, expected: u64) -> Result<Value> {
        let deck = self.deck(&key)?;
        if deck.version != expected {
            return Err(Error::new(
                "VERSION_CONFLICT",
                "卡组已变化，请保存最新版本后冻结",
            ));
        }
        let entries = catalog.normalize(&deck.entries)?;
        let content = json!({"catalogVersion":catalog.version,"entries":entries});
        let content_hash = hash(&serde_json::to_vec(&content)?);
        let revision = id()?;
        self.db.execute(
            "INSERT INTO revisions VALUES(?1,?2,?3,?4,?5)",
            params![
                revision,
                key,
                catalog.version,
                serde_json::to_string(&entries)?,
                content_hash
            ],
        )?;
        Ok(
            json!({"id":revision,"deckId":key,"catalogVersion":catalog.version,"entries":entries,"contentHash":content_hash,"validation":catalog.structural(&entries)?}),
        )
    }
    pub fn setting(&self, key: &str) -> Result<Option<Value>> {
        let raw: Option<String> = self
            .db
            .query_row("SELECT value FROM settings WHERE key=?1", [key], |r| {
                r.get(0)
            })
            .optional()?;
        raw.map(|v| serde_json::from_str(&v).map_err(Into::into))
            .transpose()
    }
    pub fn set_setting(&self, key: &str, value: &Value) -> Result<()> {
        self.db.execute("INSERT INTO settings VALUES(?1,?2) ON CONFLICT(key) DO UPDATE SET value=excluded.value",params![key,serde_json::to_string(value)?])?;
        Ok(())
    }
    pub fn activate_display(&mut self, package: &str, expected: Option<&str>) -> Result<()> {
        let tx = self.db.transaction()?;
        let current: Option<String> = tx
            .query_row(
                "SELECT value FROM settings WHERE key='displayActivation'",
                [],
                |r| r.get(0),
            )
            .optional()?;
        let current = current
            .map(|s| serde_json::from_str::<Value>(&s))
            .transpose()?;
        if current.as_ref().and_then(|v| v["package"].as_str()) != expected {
            return Err(Error::new(
                "PREVIEW_CHANGED",
                "目录已变化，请重开 App 后重新预览",
            ));
        }
        tx.execute("INSERT INTO settings VALUES('displayActivation',?1) ON CONFLICT(key) DO UPDATE SET value=excluded.value",[json!({"package":package}).to_string()])?;
        tx.execute("INSERT INTO settings VALUES('displayCandidate','null') ON CONFLICT(key) DO UPDATE SET value=excluded.value",[])?;
        tx.commit()?;
        Ok(())
    }
    pub fn revisions(&self, deck: &str) -> Result<Value> {
        let mut statement=self.db.prepare("SELECT id,catalog_version,entries,content_hash FROM revisions WHERE deck_id=?1 ORDER BY rowid DESC")?;
        let mut rows = Vec::new();
        for row in statement.query_map([deck], |r| {
            Ok((
                r.get::<_, String>(0)?,
                r.get::<_, String>(1)?,
                r.get::<_, String>(2)?,
                r.get::<_, String>(3)?,
            ))
        })? {
            let (id, catalog, entries, content_hash) = row?;
            let entries: Vec<Entry> = serde_json::from_str(&entries)?;
            if hash(&serde_json::to_vec(
                &json!({"catalogVersion":catalog,"entries":entries}),
            )?) != content_hash
            {
                return Err(Error::new(
                    "INVALID_REVISION",
                    "冻结版本校验失败；不会改写原身份",
                ));
            }
            rows.push(json!({"id":id,"catalogVersion":catalog,"entries":entries,"contentHash":content_hash}));
        }
        Ok(json!({"revisions":rows}))
    }
    pub fn checkpoint(&self) -> Result<()> {
        self.db.execute_batch("PRAGMA wal_checkpoint(FULL);")?;
        Ok(())
    }
}
