//! Desktop/mobile interchange keeps explicit printing IDs; names are never guessed.
use crate::{
    catalog::Entry,
    error::{Error, Result},
    store::Deck,
};
use serde::Deserialize;
use serde_json::{json, Value};
use std::collections::BTreeSet;

pub const MAX_TEXT: usize = 128 * 1024;
const MAX_ROWS: usize = 500;

#[derive(Clone, Copy, Default, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Format {
    Csv,
    Text,
    #[default]
    Json,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Document {
    schema: Option<u32>,
    catalog_version: Option<String>,
    name: String,
    entries: Vec<Entry>,
}

fn quantity(value: &str) -> Result<u32> {
    let value = value.trim();
    if value.is_empty() || !value.bytes().all(|b| b.is_ascii_digit()) {
        return Err(Error::new("BAD_QUANTITY", "卡牌数量须为 1 至 60 的整数"));
    }
    value
        .parse::<u32>()
        .ok()
        .filter(|n| (1..=60).contains(n))
        .ok_or(Error::new("BAD_QUANTITY", "每行卡牌数量须为 1 至 60"))
}

pub fn import_deck(text: &str, catalog_version: &str) -> Result<(String, Vec<Entry>)> {
    if text.len() > MAX_TEXT {
        return Err(Error::new(
            "IMPORT_TOO_LARGE",
            "卡组文件最多 128 KiB、500 行",
        ));
    }
    if text.contains('\0') {
        return Err(Error::new("INVALID_DECK_TEXT", "文件包含无效的空字符"));
    }
    let text = text.trim_start_matches('\u{feff}').trim();
    let (name, entries) = if text.starts_with('{') {
        let doc: Document = serde_json::from_str(text).map_err(|_| {
            Error::new(
                "INVALID_DECK_TEXT",
                "卡组 JSON 格式无效，请检查 name 和 entries",
            )
        })?;
        if doc.schema.is_some_and(|s| s != 1)
            || doc
                .catalog_version
                .as_deref()
                .is_some_and(|v| v != catalog_version)
        {
            return Err(Error::new(
                "CATALOG_MISMATCH",
                "卡组资料版本不匹配；不会猜测或改写卡牌身份",
            ));
        }
        (doc.name, doc.entries)
    } else if text.lines().next().is_some_and(|line| line.contains(',')) {
        let mut csv = csv::ReaderBuilder::new()
            .trim(csv::Trim::All)
            .from_reader(text.as_bytes());
        let header = csv
            .headers()
            .map_err(|_| Error::new("BAD_CSV", "CSV 表头无效"))?
            .clone();
        let fields: BTreeSet<_> = header.iter().collect();
        if fields.len() != header.len()
            || !fields.contains("printingId")
            || !fields.contains("quantity")
        {
            return Err(Error::new(
                "BAD_CSV",
                "请使用桌面卡组 CSV，包含 printingId、quantity 列且列名不重复",
            ));
        }
        let pid = header.iter().position(|s| s == "printingId").unwrap();
        let qty = header.iter().position(|s| s == "quantity").unwrap();
        let mut entries = Vec::new();
        for (n, row) in csv.records().enumerate() {
            if n >= MAX_ROWS {
                return Err(Error::new("TOO_MANY_ROWS", "卡组最多导入 500 行"));
            }
            let row =
                row.map_err(|_| Error::new("BAD_CSV", "CSV 列数或内容无效；请重新从桌面导出"))?;
            entries.push(Entry {
                printing_id: row[pid].into(),
                quantity: quantity(&row[qty])?,
            });
        }
        ("导入的卡组".into(), entries)
    } else {
        let mut entries = Vec::new();
        for line in text
            .lines()
            .map(str::trim)
            .filter(|s| !s.is_empty() && !s.starts_with('#'))
        {
            if entries.len() >= MAX_ROWS {
                return Err(Error::new("TOO_MANY_ROWS", "卡组最多导入 500 行"));
            }
            let words: Vec<_> = line.split_whitespace().collect();
            if words.len() != 2 {
                return Err(Error::new(
                    "INVALID_DECK_TEXT",
                    "文本每行使用“数量 印刷ID”，也可导入桌面 CSV 或卡组 JSON",
                ));
            }
            entries.push(Entry {
                printing_id: words[1].into(),
                quantity: quantity(words[0])?,
            });
        }
        ("导入的卡组".into(), entries)
    };
    if entries.is_empty() {
        return Err(Error::new("INVALID_DECK_TEXT", "卡组内容不能为空"));
    }
    if entries.len() > MAX_ROWS {
        return Err(Error::new("TOO_MANY_ROWS", "卡组最多导入 500 行"));
    }
    Ok((name, entries))
}

pub fn export(deck: &Deck, catalog: &str, format: Format) -> Result<Value> {
    match format {
        Format::Json => {
            Ok(json!({"schema":1,"catalogVersion":catalog,"name":deck.name,"entries":deck.entries}))
        }
        Format::Text => Ok(
            json!({"text":deck.entries.iter().map(|e|format!("{} {}", e.quantity, e.printing_id)).collect::<Vec<_>>().join("\n"),"filename":"ptcg-deck.txt"}),
        ),
        Format::Csv => {
            let mut writer = csv::WriterBuilder::new()
                .terminator(csv::Terminator::CRLF)
                .from_writer(Vec::new());
            writer
                .write_record(["printingId", "quantity"])
                .map_err(|_| Error::new("EXPORT_FAILED", "卡组导出失败"))?;
            for entry in &deck.entries {
                // Same spreadsheet formula escaping as the desktop exporter.
                let pid = if entry
                    .printing_id
                    .starts_with(['\'', '=', '+', '-', '@', '\t', '\r'])
                {
                    format!("'{}", entry.printing_id)
                } else {
                    entry.printing_id.clone()
                };
                writer
                    .write_record([pid, entry.quantity.to_string()])
                    .map_err(|_| Error::new("EXPORT_FAILED", "卡组导出失败"))?;
            }
            let bytes = writer
                .into_inner()
                .map_err(|_| Error::new("EXPORT_FAILED", "卡组导出失败"))?;
            let text = String::from_utf8(bytes)
                .map_err(|_| Error::new("EXPORT_FAILED", "卡组导出失败"))?;
            Ok(json!({"text":format!("\u{feff}{text}"),"filename":"ptcg-deck.csv"}))
        }
    }
}
