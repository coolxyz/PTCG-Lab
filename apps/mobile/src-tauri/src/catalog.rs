use crate::error::{Error, Result};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::{
    collections::{BTreeMap, BTreeSet},
    fs,
    path::Path,
};

pub fn hash(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}
pub fn id() -> Result<String> {
    let mut bytes = [0; 16];
    getrandom::fill(&mut bytes)
        .map_err(|_| Error::new("RANDOM_UNAVAILABLE", "系统随机源不可用"))?;
    Ok(bytes.iter().map(|b| format!("{b:02x}")).collect())
}

#[derive(Debug, Clone, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Entry {
    pub printing_id: String,
    pub quantity: u32,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Manifest {
    schema: u32,
    catalog_version: String,
    files: BTreeMap<String, String>,
}

pub struct Catalog {
    pub version: String,
    pub display_version: Option<String>,
    pub cards: Vec<Value>,
    pub by_id: BTreeMap<String, usize>,
}
impl Catalog {
    pub fn load(root: &Path) -> Result<Self> {
        let manifest: Manifest = serde_json::from_slice(&read(root, "manifest.json", 8192)?)?;
        let names: BTreeSet<_> = manifest.files.keys().map(String::as_str).collect();
        if manifest.schema != 1 || names != BTreeSet::from(["catalog.json", "artwork.json"]) {
            return Err(Error::new("INVALID_RESOURCE", "移动展示资源清单无效"));
        }
        for (name, digest) in &manifest.files {
            if hash(&read(root, name, 64 * 1024 * 1024)?) != *digest {
                return Err(Error::new(
                    "INVALID_RESOURCE",
                    "移动展示资源校验失败；未加载不匹配的卡表",
                ));
            }
        }
        let data: Value = serde_json::from_slice(&read(root, "catalog.json", 64 * 1024 * 1024)?)?;
        if data["version"] != manifest.catalog_version {
            return Err(Error::new("INVALID_RESOURCE", "目录版本与清单不一致"));
        }
        Self::from_value(data)
    }
    pub fn from_value(mut value: Value) -> Result<Self> {
        let version = value["version"]
            .as_str()
            .filter(|v| !v.is_empty())
            .ok_or(Error::new("INVALID_RESOURCE", "目录版本缺失"))?
            .to_owned();
        let display_version = value
            .get("displayCatalogVersion")
            .and_then(Value::as_str)
            .map(String::from);
        let cards = match value.as_object_mut().and_then(|o| o.remove("cards")) {
            Some(Value::Array(cards)) if !cards.is_empty() && cards.len() <= 100000 => cards,
            _ => return Err(Error::new("INVALID_RESOURCE", "卡表内容无效")),
        };
        let mut by_id = BTreeMap::new();
        for (i, card) in cards.iter().enumerate() {
            let pid = card["printingId"]
                .as_str()
                .filter(|v| !v.is_empty() && v.len() <= 256)
                .ok_or(Error::new("INVALID_RESOURCE", "卡牌印刷身份缺失"))?;
            if !card["cnName"].is_string() || by_id.insert(pid.into(), i).is_some() {
                return Err(Error::new("INVALID_RESOURCE", "卡牌身份重复或资料不完整"));
            }
        }
        Ok(Self {
            display_version,
            version,
            cards,
            by_id,
        })
    }
    pub fn normalize(&self, entries: &[Entry]) -> Result<Vec<Entry>> {
        if entries.len() > 500 {
            return Err(Error::new("BAD_ENTRIES", "草稿最多 500 行"));
        }
        let mut counts: BTreeMap<String, u32> = BTreeMap::new();
        for entry in entries {
            if !self.by_id.contains_key(&entry.printing_id) {
                return Err(Error::new(
                    "UNKNOWN_PRINTING",
                    "卡组中存在本机目录没有收录的印刷版本",
                ));
            }
            if !(1..=60).contains(&entry.quantity) {
                return Err(Error::new("BAD_QUANTITY", "每行卡牌数量须为 1 至 60"));
            }
            *counts.entry(entry.printing_id.clone()).or_default() += entry.quantity;
        }
        if counts.values().any(|n| *n > 60) || counts.values().sum::<u32>() > 600 {
            return Err(Error::new(
                "BAD_QUANTITY",
                "同一版本最多 60 张，草稿最多 600 张",
            ));
        }
        Ok(counts
            .into_iter()
            .map(|(printing_id, quantity)| Entry {
                printing_id,
                quantity,
            })
            .collect())
    }
    pub fn structural(&self, entries: &[Entry]) -> Result<Value> {
        let entries = self.normalize(entries)?;
        let count = entries.iter().map(|r| r.quantity).sum::<u32>();
        let basic = entries
            .iter()
            .any(|r| self.cards[self.by_id[&r.printing_id]]["isBasicPokemon"] == true);
        Ok(
            serde_json::json!({"count":count,"hasBasicPokemon":basic,"structureReady":count==60 && basic,
            "scope":"structure-only","message":"仅检查卡组结构；实际对战准入和当前规则版本由服务器确认"}),
        )
    }
}

pub fn read(root: &Path, name: &str, limit: u64) -> Result<Vec<u8>> {
    let path = root.join(name);
    let info = fs::symlink_metadata(&path)?;
    if !info.is_file() || info.file_type().is_symlink() || info.len() > limit {
        return Err(Error::new("INVALID_RESOURCE", "展示资源文件无效或过大"));
    }
    Ok(fs::read(path)?)
}
