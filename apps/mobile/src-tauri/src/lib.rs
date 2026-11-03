//! iOS owner: immutable display metadata, local product data and bounded image I/O.
//! No Python, rule execution, process launcher, HTTP listener or GUI credentials.
pub mod artwork;
pub mod catalog;
pub mod collection;
pub mod decks;
pub use decks::import_deck;
pub mod error;
pub mod files;
#[cfg(feature = "native")]
mod native;
pub mod online;
pub mod permissions;
pub mod protection;
pub mod resources;
pub mod store;
pub mod tls;
#[cfg(feature = "native")]
pub use native::run;

use artwork::{Artwork, HttpDownload, Source};
use catalog::{Catalog, Entry};
use error::{Error, Result};
use serde::Deserialize;
use serde_json::{json, Value};
use std::{
    path::Path,
    sync::{
        atomic::{AtomicBool, AtomicU64},
        Arc, Mutex, RwLock,
    },
};
use store::Store;

#[derive(Deserialize)]
#[serde(
    tag = "op",
    rename_all = "kebab-case",
    rename_all_fields = "camelCase",
    deny_unknown_fields
)]
pub enum Request {
    Capabilities {},
    Catalog {
        query: String,
        offset: usize,
        limit: usize,
        owned_only: bool,
    },
    Card {
        printing_id: String,
    },
    Collection {},
    SetCollection {
        changes: Vec<collection::Holding>,
        expected_version: u64,
    },
    PreviewCollection {
        text: String,
    },
    ImportCollection {
        text: String,
        expected_version: u64,
        preview_hash: String,
    },
    CollectionBatches {},
    UndoCollection {
        batch: String,
        expected_version: u64,
    },
    ExportCollection {},
    CatalogStatus {},
    PruneCatalogUpdates {},
    ApplyCatalog {
        package: String,
        base: String,
    },
    SetHolding {
        printing_id: String,
        quantity: u32,
        wishlist: bool,
        expected_version: u64,
    },
    Decks {},
    SaveDeck {
        id: Option<String>,
        name: String,
        entries: Vec<Entry>,
        expected_version: Option<u64>,
    },
    DeleteDeck {
        id: String,
        expected_version: u64,
    },
    Revisions {
        deck_id: String,
    },
    FreezeDeck {
        id: String,
        expected_version: u64,
    },
    ImportDeck {
        text: String,
    },
    ExportDeck {
        id: String,
        format: Option<decks::Format>,
    },
    ArtworkSettings {},
    SetArtworkSource {
        settings: Source,
    },
    Checkpoint {},
}
pub struct Core {
    pub catalog: RwLock<Catalog>,
    pub resources: Mutex<resources::Resources>,
    pub cache: std::path::PathBuf,
    pub store: Mutex<Store>,
    pub artwork: Mutex<Artwork>,
    pub online: Mutex<online::Owner>,
    pub active: Arc<AtomicBool>,
    pub online_epoch: Arc<AtomicU64>,
}
impl Core {
    pub fn stage_catalog(&self, bytes: &[u8], sha: &str) -> Result<Value> {
        protection::available()?;
        let catalog = self
            .catalog
            .read()
            .map_err(|_| Error::new("LOCAL_FAILED", "目录暂不可用"))?;
        let resources = self
            .resources
            .lock()
            .map_err(|_| Error::new("LOCAL_FAILED", "资料更新暂不可用"))?;
        let store = self
            .store
            .lock()
            .map_err(|_| Error::new("STORAGE_FAILED", "资料暂不可用"))?;
        resources.stage(bytes, sha, &catalog, &store)
    }
    fn apply_catalog(&self, package: &str, base: &str) -> Result<Value> {
        let mut online = self
            .online
            .try_lock()
            .map_err(|_| Error::new("PROFILE_BUSY", "对战操作处理中，请稍后应用目录更新"))?;
        online
            .catalog_update_allowed(self)
            .map_err(|error| match error.code.as_str() {
                "PROFILE_BUSY" => {
                    Error::new("PROFILE_BUSY", "对战连接正在处理，请稍后应用目录更新")
                }
                "PENDING_COMMAND" => Error::new(
                    "UPDATE_BLOCKED_ONLINE",
                    "请先恢复待确认的对战、身份或大厅操作，再应用目录更新",
                ),
                _ => Error::new(
                    "ONLINE_STATE_UNAVAILABLE",
                    "暂时无法确认对战身份记录，更新未应用；请保留原资料并检查身份状态",
                ),
            })?;
        let mut catalog = self
            .catalog
            .write()
            .map_err(|_| Error::new("LOCAL_FAILED", "目录暂不可用"))?;
        let mut resources = self
            .resources
            .lock()
            .map_err(|_| Error::new("LOCAL_FAILED", "资料更新暂不可用"))?;
        let mut artwork = self
            .artwork
            .lock()
            .map_err(|_| Error::new("LOCAL_FAILED", "图片缓存暂不可用"))?;
        let mut store = self
            .store
            .lock()
            .map_err(|_| Error::new("STORAGE_FAILED", "资料暂不可用"))?;
        let (path, next) = resources.prepare_apply(package, base, &catalog, &store)?;
        let next_base = catalog::hash(&std::fs::read(path.join("manifest.json"))?);
        let images = Artwork::load(
            &path,
            self.cache.clone(),
            artwork.source.clone(),
            Box::new(HttpDownload::new()?),
        )?;
        // Everything fallible is prepared first. A crash after this SQLite commit
        // reopens the same complete release; credentials and frozen IDs are untouched.
        store.activate_display(package, resources.activation())?;
        *artwork = images;
        *catalog = next;
        resources.did_apply(path, next_base, package.into());
        online.stop();
        self.online_epoch
            .fetch_add(1, std::sync::atomic::Ordering::AcqRel);
        Ok(json!({"applied":true,"catalogVersion":catalog.version,"base":resources.base}))
    }
    pub fn set_online_active(&self, active: bool) {
        self.active
            .store(active, std::sync::atomic::Ordering::Release);
        if !active {
            self.online_epoch
                .fetch_add(1, std::sync::atomic::Ordering::AcqRel);
        }
    }
    pub fn online_catalog(&self, catalog_version: &str, asset_version: &str) -> Result<Value> {
        let catalog = self
            .catalog
            .read()
            .map_err(|_| Error::new("LOCAL_FAILED", "目录暂不可用"))?;
        if catalog.display_version.as_deref() != Some(catalog_version) {
            return Ok(serde_json::json!({"cards":[],"missing":true}));
        }
        let artwork = self
            .artwork
            .lock()
            .map_err(|_| Error::new("LOCAL_FAILED", "图片索引暂不可用"))?;
        let mut rows: std::collections::BTreeMap<String, Value> = std::collections::BTreeMap::new();
        let mut aliases: std::collections::BTreeMap<String, std::collections::BTreeSet<String>> =
            std::collections::BTreeMap::new();
        let mut descriptions: std::collections::BTreeMap<String, Value> =
            std::collections::BTreeMap::new();
        let mut attacks: std::collections::BTreeMap<String, Value> =
            std::collections::BTreeMap::new();
        for card in &catalog.cards {
            let Some(engine) = card["engineId"].as_str().filter(|s| !s.is_empty()) else {
                continue;
            };
            if descriptions.get(engine).is_none_or(|old| {
                card["chineseDetails"]["sections"].as_array().map(Vec::len)
                    > old["sections"].as_array().map(Vec::len)
            }) {
                descriptions.insert(engine.into(), card["chineseDetails"].clone());
            }
            if attacks
                .get(engine)
                .is_none_or(|old| old.as_array().is_none_or(|a| a.is_empty()))
            {
                if let Some(rows) = card["attacks"].as_array() {
                    attacks.insert(
                        engine.into(),
                        serde_json::json!(rows
                            .iter()
                            .filter_map(|a| a["name"].as_str())
                            .map(|name| serde_json::json!({"name":name}))
                            .collect::<Vec<_>>()),
                    );
                }
            }
            let group = aliases.entry(engine.into()).or_default();
            if let Some(name) = card["englishName"].as_str().filter(|s| !s.is_empty()) {
                group.insert(name.into());
            }
            if let Some(names) = card["aliases"].as_array() {
                for name in names.iter().filter_map(Value::as_str) {
                    group.insert(name.into());
                }
            }
            let printing = card["printingId"].as_str().unwrap();
            let url = artwork.reference(printing);
            let row = serde_json::json!({"engineId":engine,"printingId":printing,"englishName":card["englishName"].as_str().unwrap_or(""),"cnName":card["cnName"],"effectStatus":card["effectStatus"],
                "attacks":card["attacks"].as_array().map(|a|a.iter().filter_map(|a|a["name"].as_str()).map(|name|serde_json::json!({"name":name})).collect::<Vec<_>>()).unwrap_or_default(),
                "chineseDetails":card["chineseDetails"],"image":{"url":url,"label":"本机默认卡面（按效果映射）"}});
            let replace = rows.get(engine).is_none_or(|old| {
                old["image"]["url"].is_null() && !row["image"]["url"].is_null()
                    || old["image"]["url"].is_null() == row["image"]["url"].is_null()
                        && row["printingId"].as_str() < old["printingId"].as_str()
            });
            if replace {
                rows.insert(engine.into(), row);
            }
        }
        let cards = rows
            .into_iter()
            .map(|(engine, mut row)| {
                row["chineseDetails"] = descriptions
                    .remove(&engine)
                    .unwrap_or(serde_json::json!({}));
                row["attacks"] = attacks.remove(&engine).unwrap_or(serde_json::json!([]));
                row["aliases"] =
                    serde_json::to_value(aliases.remove(&engine).unwrap_or_default()).unwrap();
                row
            })
            .collect::<Vec<_>>();
        let version = crate::catalog::hash(&serde_json::to_vec(&cards)?);
        if asset_version != "unavailable" && asset_version != version {
            return Ok(serde_json::json!({"cards":[],"missing":true}));
        }
        Ok(
            serde_json::json!({"cards":cards,"catalogVersion":catalog_version,"assetVersion":version,"missing":false}),
        )
    }
    pub fn open(resources: &Path, data: &Path, cache: &Path) -> Result<Self> {
        if !resources.is_dir() {
            return Err(Error::new(
                "RESOURCE_MISSING",
                "App 缺少随包卡库，请安装完整的移动版本；已保存资料不会被删除",
            ));
        }
        Catalog::load(resources)?;
        protection::prepare(data)?;
        let store = Store::open(data)?;
        let resources = resources::Resources::open(resources, &data.canonicalize()?, &store)?;
        let catalog = Catalog::load(&resources.active)?;
        let source = store
            .setting("artwork")?
            .map(serde_json::from_value)
            .transpose()?
            .unwrap_or_default();
        let artwork = Artwork::load(
            &resources.active,
            cache.to_path_buf(),
            source,
            Box::new(HttpDownload::new()?),
        )?;
        let active = Arc::new(AtomicBool::new(true));
        let online_epoch = Arc::new(AtomicU64::new(0));
        Ok(Self {
            online: Mutex::new(online::Owner::new(
                data.canonicalize()?.join("online"),
                active.clone(),
                online_epoch.clone(),
            )),
            active,
            online_epoch,
            catalog: RwLock::new(catalog),
            resources: Mutex::new(resources),
            cache: cache.to_path_buf(),
            store: Mutex::new(store),
            artwork: Mutex::new(artwork),
        })
    }
    pub fn request(&self, request: Request) -> Result<Value> {
        protection::available()?;
        if let Request::ApplyCatalog { package, base } = request {
            return self.apply_catalog(&package, &base);
        }
        let catalog = self
            .catalog
            .read()
            .map_err(|_| Error::new("LOCAL_FAILED", "目录暂不可用"))?;
        match request {
            Request::CatalogStatus {} => {
                let resources = self
                    .resources
                    .lock()
                    .map_err(|_| Error::new("LOCAL_FAILED", "资料更新暂不可用"))?;
                let store = self
                    .store
                    .lock()
                    .map_err(|_| Error::new("STORAGE_FAILED", "资料暂不可用"))?;
                resources.status(&catalog, &store)
            }
            Request::PruneCatalogUpdates {} => {
                let resources = self
                    .resources
                    .lock()
                    .map_err(|_| Error::new("LOCAL_FAILED", "资料更新暂不可用"))?;
                let store = self
                    .store
                    .lock()
                    .map_err(|_| Error::new("STORAGE_FAILED", "资料暂不可用"))?;
                resources.prune(&store)
            }
            Request::Capabilities {} => Ok(
                json!({"schema":1,"platform":"ios","catalogVersion":catalog.version,
                "implemented":{"catalog":true,"collection":true,"decks":true,"artworkCache":true,"localPractice":false,"localRuleVerification":false,"onlineBattle":true,"systemShare":cfg!(all(target_os="ios",feature="native")),"catalogUpdates":true,"fullCollection":true},
                "stage":"P8-E3"}),
            ),
            Request::Catalog {
                query,
                offset,
                limit,
                owned_only,
            } => {
                if query.len() > 300 || !(1..=100).contains(&limit) || offset > 100000 {
                    return Err(Error::new("INVALID_REQUEST", "搜索条件或分页范围无效"));
                }
                let query = query.trim().to_lowercase();
                let owned: std::collections::BTreeSet<String> = if owned_only {
                    let store = self
                        .store
                        .lock()
                        .map_err(|_| Error::new("STORAGE_FAILED", "本机收藏暂不可用"))?;
                    store.collection()?["entries"]
                        .as_array()
                        .unwrap()
                        .iter()
                        .filter(|r| {
                            r["quantity"].as_u64().unwrap_or(0) > 0 || r["wishlist"] == true
                        })
                        .filter_map(|r| r["printingId"].as_str().map(String::from))
                        .collect()
                } else {
                    std::collections::BTreeSet::new()
                };
                let rows: Vec<_> = catalog
                    .cards
                    .iter()
                    .filter(|card| {
                        !owned_only
                            || card["printingId"]
                                .as_str()
                                .is_some_and(|s| owned.contains(s))
                    })
                    .filter(|card| {
                        query.is_empty()
                            || [
                                "cnName",
                                "englishName",
                                "printingId",
                                "productCode",
                                "collectorNumber",
                            ]
                            .iter()
                            .any(|key| {
                                card[key]
                                    .as_str()
                                    .is_some_and(|s| s.to_lowercase().contains(&query))
                            })
                    })
                    .collect();
                Ok(
                    json!({"cards":rows.iter().skip(offset).take(limit).collect::<Vec<_>>(),"total":rows.len(),"catalogVersion":catalog.version}),
                )
            }
            Request::Card { printing_id } => catalog
                .by_id
                .get(&printing_id)
                .map(|i| catalog.cards[*i].clone())
                .ok_or(Error::new("UNKNOWN_PRINTING", "找不到这个卡牌版本")),
            Request::ArtworkSettings {} => {
                let images = self
                    .artwork
                    .lock()
                    .map_err(|_| Error::new("STORAGE_FAILED", "图片缓存暂不可用"))?;
                Ok(json!({"settings":images.source,"cachedBytes":images.cached_bytes()?}))
            }
            Request::SetArtworkSource { settings } => {
                settings.validate()?;
                let mut images = self
                    .artwork
                    .lock()
                    .map_err(|_| Error::new("STORAGE_FAILED", "图片缓存暂不可用"))?;
                let store = self
                    .store
                    .lock()
                    .map_err(|_| Error::new("STORAGE_FAILED", "本机数据暂不可用"))?;
                store.set_setting("artwork", &serde_json::to_value(&settings)?)?;
                images.configure(settings)?;
                Ok(json!({"settings":images.source,"cachedBytes":images.cached_bytes()?}))
            }
            request => {
                let mut store = self
                    .store
                    .lock()
                    .map_err(|_| Error::new("STORAGE_FAILED", "本机数据暂不可用"))?;
                match request {
                    Request::Collection {} => store.collection(),
                    Request::SetCollection {
                        changes,
                        expected_version,
                    } => store.set_collection(&catalog, changes, expected_version),
                    Request::PreviewCollection { text } => collection::preview(&text, &catalog),
                    Request::ImportCollection {
                        text,
                        expected_version,
                        preview_hash,
                    } => store.import_collection(&catalog, &text, expected_version, &preview_hash),
                    Request::CollectionBatches {} => store.collection_batches(),
                    Request::UndoCollection {
                        batch,
                        expected_version,
                    } => store.undo_collection(&batch, expected_version),
                    Request::ExportCollection {} => Ok(
                        json!({"text":store.export_collection()?,"filename":"ptcg-collection.csv"}),
                    ),
                    Request::SetHolding {
                        printing_id,
                        quantity,
                        wishlist,
                        expected_version,
                    } => store.holding(&catalog, printing_id, quantity, wishlist, expected_version),
                    Request::Decks {} => Ok(serde_json::to_value(store.decks()?)?),
                    Request::SaveDeck {
                        id,
                        name,
                        entries,
                        expected_version,
                    } => Ok(serde_json::to_value(store.save(
                        &catalog,
                        id,
                        name,
                        entries,
                        expected_version,
                    )?)?),
                    Request::DeleteDeck {
                        id,
                        expected_version,
                    } => store.delete(id, expected_version),
                    Request::Revisions { deck_id } => store.revisions(&deck_id),
                    Request::FreezeDeck {
                        id,
                        expected_version,
                    } => store.freeze(&catalog, id, expected_version),
                    Request::ImportDeck { text } => {
                        let (name, entries) = import_deck(&text, &catalog.version)?;
                        Ok(serde_json::to_value(
                            store.save(&catalog, None, name, entries, None)?,
                        )?)
                    }
                    Request::ExportDeck { id, format } => {
                        let deck = store.deck(&id)?;
                        decks::export(&deck, &catalog.version, format.unwrap_or_default())
                    }
                    Request::Checkpoint {} => {
                        store.checkpoint()?;
                        Ok(json!({"saved":true}))
                    }
                    _ => Err(Error::new("INVALID_REQUEST", "此操作不属于移动端本地能力")),
                }
            }
        }
    }
}
