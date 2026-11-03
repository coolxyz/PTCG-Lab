//! Desktop CSV vocabulary, explicit printing identities and atomic collection batches.
use crate::{
    catalog::{hash, id, Catalog},
    error::{Error, Result},
    store::Store,
};
use rusqlite::{params, OptionalExtension, Transaction};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::{BTreeMap, BTreeSet};
pub const CONDITIONS: [&str; 4] = ["未标注", "全新", "良好", "使用痕迹"];
const MAX_TEXT: usize = 1024 * 1024;
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Holding {
    pub printing_id: String,
    pub quantity: u32,
    pub condition: String,
    pub notes: String,
    pub wishlist: bool,
}
impl Holding {
    fn validate(&self, catalog: &Catalog) -> Result<()> {
        if !catalog.by_id.contains_key(&self.printing_id) {
            return Err(Error::new(
                "UNKNOWN_PRINTING",
                "收藏存在当前目录未收录的印刷 ID；请先更新目录",
            ));
        }
        if self.quantity > 9999
            || !CONDITIONS.contains(&self.condition.as_str())
            || self.notes.chars().count() > 500
            || self.notes.contains('\0')
        {
            return Err(Error::new(
                "BAD_HOLDING",
                "请检查数量、品相及备注（最多 500 字）",
            ));
        }
        Ok(())
    }
}
#[derive(Serialize, Deserialize)]
struct Delta {
    entry: Holding,
    previous: Option<Holding>,
    applied: Holding,
}
fn version(db: &rusqlite::Connection) -> Result<u64> {
    let raw: String = db.query_row(
        "SELECT value FROM settings WHERE key='collectionVersion'",
        [],
        |r| r.get(0),
    )?;
    raw.parse()
        .map_err(|_| Error::new("INVALID_STORAGE", "收藏版本损坏；不会重置资料"))
}
fn advance(tx: &Transaction<'_>, expected: u64) -> Result<()> {
    if expected >= 9007199254740991
        || tx.execute(
            "UPDATE settings SET value=?1 WHERE key='collectionVersion' AND value=?2",
            params![(expected + 1).to_string(), expected.to_string()],
        )? != 1
    {
        return Err(Error::new("VERSION_CONFLICT", "收藏已变化，请刷新后重试"));
    }
    Ok(())
}
fn rows(db: &rusqlite::Connection) -> Result<Vec<Holding>> {
    let mut q=db.prepare("SELECT printing_id,quantity,condition,notes,wishlist FROM holdings ORDER BY printing_id,condition")?;
    let rows = q
        .query_map([], |r| {
            Ok(Holding {
                printing_id: r.get(0)?,
                quantity: r.get(1)?,
                condition: r.get(2)?,
                notes: r.get(3)?,
                wishlist: r.get(4)?,
            })
        })?
        .collect::<std::result::Result<Vec<_>, _>>()?;
    Ok(rows)
}
fn write(tx: &Transaction<'_>, row: &Holding) -> Result<()> {
    tx.execute("INSERT INTO holdings VALUES(?1,?2,?3,?4,?5) ON CONFLICT(printing_id,condition) DO UPDATE SET quantity=excluded.quantity,notes=excluded.notes,wishlist=excluded.wishlist",params![row.printing_id,row.condition,row.quantity,row.notes,row.wishlist])?;
    Ok(())
}
pub fn parse(text: &str, catalog: &Catalog) -> Result<Vec<Holding>> {
    if text.len() > MAX_TEXT {
        return Err(Error::new(
            "IMPORT_TOO_LARGE",
            "收藏 CSV 最多 1 MiB、500 行",
        ));
    }
    let mut csv =
        csv::ReaderBuilder::new().from_reader(text.trim_start_matches('\u{feff}').as_bytes());
    let header = csv
        .headers()
        .map_err(|_| Error::new("BAD_CSV", "CSV 表头无效"))?
        .clone();
    let wanted = BTreeSet::from(["printingId", "quantity", "condition", "notes", "wishlist"]);
    if header.len() != 5 || header.iter().collect::<BTreeSet<_>>() != wanted {
        return Err(Error::new(
            "BAD_CSV",
            "请使用桌面收藏 CSV：printingId,quantity,condition,notes,wishlist",
        ));
    }
    let field =
        |r: &csv::StringRecord, k: &str| r[header.iter().position(|v| v == k).unwrap()].to_owned();
    let mut groups: BTreeMap<(String, String), (Holding, BTreeSet<String>)> = BTreeMap::new();
    for (n, row) in csv.records().enumerate() {
        if n >= 500 {
            return Err(Error::new("TOO_MANY_ROWS", "每批最多导入 500 行收藏"));
        }
        let row = row.map_err(|_| Error::new("BAD_CSV", "CSV 列数、引号或文字编码无效"))?;
        let raw = field(&row, "quantity");
        let q = raw.trim();
        if q.is_empty() || !q.bytes().all(|b| b.is_ascii_digit()) {
            return Err(Error::new("BAD_QUANTITY", "收藏数量须为 0 至 9999 的整数"));
        }
        let wish = field(&row, "wishlist").trim().to_ascii_lowercase();
        if !["true", "false", "1", "0", ""].contains(&wish.as_str()) {
            return Err(Error::new("BAD_CSV", "wishlist 须为 true 或 false"));
        }
        let mut notes = field(&row, "notes");
        if notes.starts_with('\'')
            && notes
                .chars()
                .nth(1)
                .is_some_and(|c| "'=+-@\t\r".contains(c))
        {
            notes.remove(0);
        }
        let mut condition = field(&row, "condition").trim().to_owned();
        if condition.is_empty() {
            condition = "未标注".into();
        }
        let entry = Holding {
            printing_id: field(&row, "printingId").trim().into(),
            quantity: q
                .parse()
                .map_err(|_| Error::new("BAD_QUANTITY", "收藏数量超限"))?,
            condition,
            notes,
            wishlist: wish == "true" || wish == "1",
        };
        entry.validate(catalog)?;
        let key = (entry.printing_id.clone(), entry.condition.clone());
        if let Some((old, notes)) = groups.get_mut(&key) {
            old.quantity = old
                .quantity
                .checked_add(entry.quantity)
                .ok_or(Error::new("BAD_QUANTITY", "合并后的数量超限"))?;
            old.wishlist |= entry.wishlist;
            notes.insert(entry.notes);
        } else {
            let notes = BTreeSet::from([entry.notes.clone()]);
            groups.insert(key, (entry, notes));
        }
    }
    if groups.is_empty() {
        return Err(Error::new("BAD_CSV", "CSV 没有可导入的记录"));
    }
    groups
        .into_values()
        .map(|(mut row, notes)| {
            row.notes = notes
                .into_iter()
                .filter(|s| !s.is_empty())
                .collect::<Vec<_>>()
                .join("；");
            row.validate(catalog)?;
            Ok(row)
        })
        .collect()
}
fn fingerprint(entries: &[Holding]) -> Result<String> {
    Ok(hash(&serde_json::to_vec(&json!(entries))?))
}
pub fn preview(text: &str, catalog: &Catalog) -> Result<Value> {
    let entries = parse(text, catalog)?;
    Ok(
        json!({"previewHash":fingerprint(&entries)?,"entries":entries,"rows":entries.len(),"quantity":entries.iter().map(|e|e.quantity as u64).sum::<u64>(),"mode":"additive"}),
    )
}
fn protected(text: &str) -> String {
    if text.starts_with(['\'', '=', '+', '-', '@', '\t', '\r']) {
        format!("'{text}")
    } else {
        text.into()
    }
}
impl Store {
    pub fn collection(&self) -> Result<Value> {
        Ok(json!({"version":version(&self.db)?,"entries":rows(&self.db)?}))
    }
    pub fn set_collection(
        &mut self,
        catalog: &Catalog,
        changes: Vec<Holding>,
        expected: u64,
    ) -> Result<Value> {
        if changes.is_empty()
            || changes.len() > 500
            || changes
                .iter()
                .map(|e| (&e.printing_id, &e.condition))
                .collect::<BTreeSet<_>>()
                .len()
                != changes.len()
        {
            return Err(Error::new(
                "BAD_CHANGES",
                "请选择 1 至 500 条不重复的收藏记录",
            ));
        }
        for row in &changes {
            row.validate(catalog)?;
        }
        let tx = self.db.transaction()?;
        advance(&tx, expected)?;
        for row in &changes {
            write(&tx, row)?;
        }
        tx.commit()?;
        self.collection()
    }
    pub fn holding(
        &mut self,
        catalog: &Catalog,
        printing: String,
        quantity: u32,
        wishlist: bool,
        expected: u64,
    ) -> Result<Value> {
        let notes = rows(&self.db)?
            .into_iter()
            .find(|e| e.printing_id == printing && e.condition == "未标注")
            .map(|e| e.notes)
            .unwrap_or_default();
        self.set_collection(
            catalog,
            vec![Holding {
                printing_id: printing,
                quantity,
                condition: "未标注".into(),
                notes,
                wishlist,
            }],
            expected,
        )
    }
    pub fn import_collection(
        &mut self,
        catalog: &Catalog,
        text: &str,
        expected: u64,
        preview_hash: &str,
    ) -> Result<Value> {
        let entries = parse(text, catalog)?;
        let fingerprint = fingerprint(&entries)?;
        if fingerprint != preview_hash {
            return Err(Error::new("PREVIEW_CHANGED", "导入内容已变化，请重新预览"));
        }
        let tx = self.db.transaction()?;
        let old: Option<(String, String)> = tx
            .query_row(
                "SELECT id,status FROM collection_batches WHERE fingerprint=?1",
                [&fingerprint],
                |r| Ok((r.get(0)?, r.get(1)?)),
            )
            .optional()?;
        if let Some((id, status)) = old {
            if status != "applied" {
                return Err(Error::new(
                    "BATCH_ALREADY_UNDONE",
                    "此批导入已撤销；请使用收藏编辑进行调整",
                ));
            }
            drop(tx);
            return Ok(json!({"id":id,"duplicate":true,"collection":self.collection()?}));
        }
        advance(&tx, expected)?;
        let mut current: BTreeMap<_, _> = rows(&tx)?
            .into_iter()
            .map(|e| ((e.printing_id.clone(), e.condition.clone()), e))
            .collect();
        let mut deltas = Vec::new();
        for entry in entries {
            let previous = current.remove(&(entry.printing_id.clone(), entry.condition.clone()));
            let mut applied = entry.clone();
            if let Some(old) = &previous {
                applied.quantity += old.quantity;
                applied.wishlist |= old.wishlist;
                if applied.notes.is_empty() {
                    applied.notes = old.notes.clone();
                }
            }
            applied.validate(catalog)?;
            write(&tx, &applied)?;
            deltas.push(Delta {
                entry,
                previous,
                applied,
            });
        }
        let batch = id()?;
        let created = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map_err(|_| Error::new("CLOCK_UNAVAILABLE", "系统时钟不可用"))?
            .as_secs();
        tx.execute(
            "INSERT INTO collection_batches VALUES(?1,?2,'applied',?3,?4)",
            params![batch, fingerprint, serde_json::to_string(&deltas)?, created],
        )?;
        tx.commit()?;
        Ok(json!({"id":batch,"duplicate":false,"collection":self.collection()?}))
    }
    pub fn collection_batches(&self) -> Result<Value> {
        let mut q = self.db.prepare(
            "SELECT id,status,created_at,deltas FROM collection_batches ORDER BY rowid DESC",
        )?;
        let rows = q
            .query_map([], |r| {
                Ok((
                    r.get::<_, String>(0)?,
                    r.get::<_, String>(1)?,
                    r.get::<_, u64>(2)?,
                    r.get::<_, String>(3)?,
                ))
            })?
            .collect::<std::result::Result<Vec<_>, _>>()?;
        let mut result = Vec::new();
        for (id, status, created, body) in rows {
            let deltas: Vec<Delta> = serde_json::from_str(&body)?;
            result.push(json!({"id":id,"status":status,"createdAt":created,"quantity":deltas.iter().map(|d|d.entry.quantity as u64).sum::<u64>()}));
        }
        Ok(json!(result))
    }
    pub fn undo_collection(&mut self, batch: &str, expected: u64) -> Result<Value> {
        let tx = self.db.transaction()?;
        let (status, raw): (String, String) = tx
            .query_row(
                "SELECT status,deltas FROM collection_batches WHERE id=?1",
                [batch],
                |r| Ok((r.get(0)?, r.get(1)?)),
            )
            .optional()?
            .ok_or(Error::new("BATCH_NOT_FOUND", "找不到导入批次"))?;
        if status == "undone" {
            return Ok(json!({"duplicate":true}));
        }
        advance(&tx, expected)?;
        let current: BTreeMap<_, _> = rows(&tx)?
            .into_iter()
            .map(|e| ((e.printing_id.clone(), e.condition.clone()), e))
            .collect();
        let deltas: Vec<Delta> = serde_json::from_str(&raw)?;
        for d in deltas {
            let mut row = current
                .get(&(d.entry.printing_id.clone(), d.entry.condition.clone()))
                .cloned()
                .ok_or(Error::new("UNDO_CONFLICT", "导入记录已变化，请先核对收藏"))?;
            row.quantity = row
                .quantity
                .checked_sub(d.entry.quantity)
                .ok_or(Error::new(
                    "UNDO_CONFLICT",
                    "导入卡牌已有减量，无法完整撤销",
                ))?;
            if row.notes == d.applied.notes {
                row.notes = d
                    .previous
                    .as_ref()
                    .map(|e| e.notes.clone())
                    .unwrap_or_default();
            }
            if row.wishlist == d.applied.wishlist {
                row.wishlist = d.previous.as_ref().is_some_and(|e| e.wishlist);
            }
            write(&tx, &row)?;
        }
        tx.execute(
            "UPDATE collection_batches SET status='undone' WHERE id=?1",
            [batch],
        )?;
        tx.commit()?;
        Ok(json!({"undone":true,"collection":self.collection()?}))
    }
    pub fn export_collection(&self) -> Result<String> {
        let mut writer = csv::WriterBuilder::new()
            .terminator(csv::Terminator::CRLF)
            .from_writer(Vec::new());
        writer
            .write_record(["printingId", "quantity", "condition", "notes", "wishlist"])
            .map_err(|_| Error::new("EXPORT_FAILED", "无法生成收藏 CSV"))?;
        for row in rows(&self.db)? {
            writer
                .write_record([
                    protected(&row.printing_id),
                    row.quantity.to_string(),
                    row.condition,
                    protected(&row.notes),
                    row.wishlist.to_string(),
                ])
                .map_err(|_| Error::new("EXPORT_FAILED", "无法生成收藏 CSV"))?;
        }
        let bytes = writer
            .into_inner()
            .map_err(|_| Error::new("EXPORT_FAILED", "无法生成收藏 CSV"))?;
        Ok(format!(
            "\u{feff}{}",
            String::from_utf8(bytes).map_err(|_| Error::new("EXPORT_FAILED", "CSV 编码无效"))?
        ))
    }
    pub fn referenced_printings(&self) -> Result<BTreeSet<String>> {
        let mut ids: BTreeSet<String> =
            rows(&self.db)?.into_iter().map(|e| e.printing_id).collect();
        for deck in self.decks()? {
            ids.extend(deck.entries.into_iter().map(|e| e.printing_id));
        }
        let mut q = self.db.prepare("SELECT entries FROM revisions")?;
        for row in q.query_map([], |r| r.get::<_, String>(0))? {
            let entries: Vec<crate::catalog::Entry> = serde_json::from_str(&row?)?;
            ids.extend(entries.into_iter().map(|e| e.printing_id));
        }
        Ok(ids)
    }
}
