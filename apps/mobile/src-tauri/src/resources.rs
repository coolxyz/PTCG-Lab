//! Immutable display-only releases. SQLite commits the active pointer after durable staging.
use crate::{
    artwork,
    catalog::{hash, id, Catalog},
    error::{Error, Result},
    store::Store,
};
use serde::Deserialize;
use serde_json::{json, Value};
use std::{
    fs::{self, File, OpenOptions},
    io::{Read, Write},
    path::{Path, PathBuf},
};
pub const MAX_BUNDLE: usize = 80 * 1024 * 1024;
#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Bundle {
    schema: u32,
    kind: String,
    catalog: String,
    artwork: String,
    manifest: String,
}
#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct DisplayCard {
    engine_id: String,
    cn_name: String,
    category: Option<String>,
    hp: Option<u32>,
    english_name: Option<String>,
    aliases: Option<Vec<String>>,
    chinese_details: Option<Details>,
}
#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct Details {
    #[serde(default)]
    sections: Vec<Section>,
    source: Option<String>,
    revision: Option<u64>,
    text_status: Option<String>,
}
#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct Section {
    kind: String,
    name: String,
    text: String,
    cost: Option<Vec<String>>,
    damage: Option<String>,
    locale: Option<String>,
    version_label: Option<String>,
    missing_text: Option<bool>,
    source: Option<String>,
}
fn display_shape(value: &Value) -> bool {
    let Ok(card) = DisplayCard::deserialize(value) else {
        return false;
    };
    fn string(value: &str, max: usize) -> bool {
        value.chars().count() <= max && !value.contains('\0')
    }
    fn link(value: &Option<String>) -> bool {
        value.as_ref().is_none_or(|s| {
            string(s, 2048)
                && url::Url::parse(s).is_ok_and(|u| {
                    u.scheme() == "https" && u.username().is_empty() && u.password().is_none()
                })
        })
    }
    if card.engine_id.is_empty()
        || !string(&card.engine_id, 512)
        || !string(&card.cn_name, 256)
        || card.hp.is_some_and(|n| n > 99999)
        || card.category.as_ref().is_some_and(|s| !string(s, 64))
        || card.english_name.as_ref().is_some_and(|s| !string(s, 256))
        || card
            .aliases
            .as_ref()
            .is_some_and(|a| a.len() > 512 || a.iter().any(|s| !string(s, 512)))
    {
        return false;
    }
    if let Some(details) = card.chinese_details {
        if details.sections.len() > 100
            || !link(&details.source)
            || details.revision.is_some_and(|v| v > 9007199254740991)
            || details
                .text_status
                .as_ref()
                .is_some_and(|s| !string(s, 128))
        {
            return false;
        }
        for section in details.sections {
            if !string(&section.kind, 64)
                || !string(&section.name, 256)
                || !string(&section.text, 16000)
                || !link(&section.source)
                || section
                    .cost
                    .as_ref()
                    .is_some_and(|a| a.len() > 100 || a.iter().any(|s| !string(s, 64)))
                || [
                    section.damage.as_deref(),
                    section.locale.as_deref(),
                    section.version_label.as_deref(),
                ]
                .into_iter()
                .flatten()
                .any(|s| !string(s, 256))
            {
                return false;
            }
            let _ = section.missing_text;
        }
    }
    true
}
pub fn digest(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}
fn safe_dir(path: &Path) -> Result<()> {
    if !path.exists() {
        fs::create_dir(path)?;
    }
    let meta = fs::symlink_metadata(path)?;
    if !meta.is_dir() || meta.file_type().is_symlink() {
        return Err(Error::new(
            "UNSAFE_STORAGE",
            "资料更新目录无效；未覆盖原目录",
        ));
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(path, fs::Permissions::from_mode(0o700))?;
    }
    Ok(())
}
fn bound(path: &Path) -> Result<Catalog> {
    let catalog = Catalog::load(path)?;
    if catalog.cards.iter().any(|card| !display_shape(card)) {
        return Err(Error::new(
            "INVALID_RESOURCE",
            "展示卡牌字段或中文说明格式无效；未切换目录",
        ));
    }
    if catalog.display_version.as_ref().is_none_or(|s| !digest(s)) {
        return Err(Error::new("INVALID_RESOURCE", "资料包缺少有效语义目录身份"));
    }
    let images = artwork::validate_resources(path)?;
    if images.iter().any(|id| !catalog.by_id.contains_key(id))
        || catalog.cards.iter().any(|c| {
            c["hasArtwork"].as_bool() != Some(images.contains(c["printingId"].as_str().unwrap()))
        })
    {
        return Err(Error::new("INVALID_RESOURCE", "资料包卡表与图片索引不一致"));
    }
    Ok(catalog)
}
pub struct Resources {
    pub active: PathBuf,
    pub base: String,
    root: PathBuf,
    activation: Option<String>,
}
impl Resources {
    pub fn open(builtin: &Path, data: &Path, store: &Store) -> Result<Self> {
        let root = data.join("display-updates");
        safe_dir(&root)?;
        let saved = store.setting("displayActivation")?;
        let activation = saved
            .as_ref()
            .and_then(|v| v["package"].as_str())
            .map(String::from);
        let active = if let Some(value) = saved {
            let key = value["package"]
                .as_str()
                .filter(|s| digest(s))
                .ok_or(Error::new(
                    "INVALID_RESOURCE",
                    "已保存的目录身份无效；不会回退并覆盖资料",
                ))?;
            let folder = root.join(key);
            safe_dir_existing(&folder)?;
            verify_package(&folder, key)?;
            bound(&folder)?;
            folder
        } else {
            builtin.to_path_buf()
        };
        Catalog::load(&active)?;
        Ok(Self {
            base: hash(&fs::read(active.join("manifest.json"))?),
            active,
            root,
            activation,
        })
    }
    fn candidate(&self, package: &str) -> Result<PathBuf> {
        if !digest(package) {
            return Err(Error::new("INVALID_RESOURCE", "资料包身份无效"));
        }
        let path = self.root.join(package);
        safe_dir_existing(&path)?;
        verify_package(&path, package)?;
        Ok(path)
    }
    fn describe(
        &self,
        current: &Catalog,
        next: &Catalog,
        store: &Store,
        key: &str,
    ) -> Result<Value> {
        let missing: Vec<_> = store
            .referenced_printings()?
            .into_iter()
            .filter(|p| !next.by_id.contains_key(p))
            .collect();
        let added = next
            .by_id
            .keys()
            .filter(|p| !current.by_id.contains_key(*p))
            .count();
        let removed = current
            .by_id
            .keys()
            .filter(|p| !next.by_id.contains_key(*p))
            .count();
        let changed = current
            .cards
            .iter()
            .filter(|c| {
                next.by_id
                    .get(c["printingId"].as_str().unwrap())
                    .is_some_and(|i| next.cards[*i]["engineId"] != c["engineId"])
            })
            .count();
        Ok(
            json!({"package":key,"base":self.base,"catalogVersion":next.version,"displayCatalogVersion":next.display_version,"cards":next.cards.len(),"added":added,"removed":removed,"engineMappingsChanged":changed,"referencedMissing":missing,"canApply":missing.is_empty()}),
        )
    }
    pub fn status(&self, current: &Catalog, store: &Store) -> Result<Value> {
        let pending = if let Some(saved) = store.setting("displayCandidate")? {
            if saved.is_null() {
                Value::Null
            } else {
                let key = saved["package"]
                    .as_str()
                    .ok_or(Error::new("INVALID_RESOURCE", "待安装目录记录无效"))?;
                let next = bound(&self.candidate(key)?)?;
                let mut view = self.describe(current, &next, store, key)?;
                view["stale"] = json!(saved["base"] != self.base);
                view
            }
        } else {
            Value::Null
        };
        Ok(
            json!({"base":self.base,"catalogVersion":current.version,"displayCatalogVersion":current.display_version,"pending":pending}),
        )
    }
    pub fn stage(
        &self,
        bytes: &[u8],
        expected_sha: &str,
        current: &Catalog,
        store: &Store,
    ) -> Result<Value> {
        self.current(store)?;
        if bytes.len() > MAX_BUNDLE || !digest(expected_sha) || hash(bytes) != expected_sha {
            return Err(Error::new(
                "HASH_MISMATCH",
                "资料文件 SHA-256 不符或超限；未改动当前目录",
            ));
        }
        let bundle: Bundle = serde_json::from_slice(bytes)?;
        if bundle.schema != 1
            || bundle.kind != "ptcg-mobile-display"
            || bundle.catalog.len() > 64 * 1024 * 1024
            || bundle.artwork.len() > 16 * 1024 * 1024
            || bundle.manifest.len() > 8192
        {
            return Err(Error::new(
                "INVALID_RESOURCE",
                "请选择移动展示资料 JSON；不接受图片 ZIP 或规则包",
            ));
        }
        let path = self.root.join(expected_sha);
        if !path.exists() {
            let size: u64 = fs::read_dir(&self.root)?
                .filter_map(|e| e.ok())
                .filter(|e| e.file_type().is_ok_and(|t| t.is_dir()))
                .flat_map(|e| {
                    fs::read_dir(e.path())
                        .into_iter()
                        .flatten()
                        .filter_map(|e| e.ok())
                })
                .filter_map(|e| e.metadata().ok().map(|m| m.len()))
                .sum();
            if size + 2 * bytes.len() as u64 > 512 * 1024 * 1024 {
                return Err(Error::new(
                    "RESOURCE_STORAGE_FULL",
                    "展示目录存储达到 512 MiB 上限，请先整理应用资料",
                ));
            }
            let temp = self.root.join(format!(".stage-{}", id()?));
            safe_dir(&temp)?;
            let result = (|| -> Result<()> {
                for (name, content) in [
                    ("catalog.json", bundle.catalog.as_bytes()),
                    ("artwork.json", bundle.artwork.as_bytes()),
                    ("manifest.json", bundle.manifest.as_bytes()),
                    ("bundle.json", bytes),
                ] {
                    let mut options = OpenOptions::new();
                    options.write(true).create_new(true);
                    #[cfg(unix)]
                    {
                        use std::os::unix::fs::OpenOptionsExt;
                        options.mode(0o600);
                    }
                    let mut f = options.open(temp.join(name))?;
                    f.write_all(content)?;
                    f.sync_all()?;
                }
                bound(&temp)?;
                File::open(&temp)?.sync_all()?;
                fs::rename(&temp, &path)?;
                File::open(&self.root)?.sync_all()?;
                Ok(())
            })();
            if result.is_err() {
                let _ = fs::remove_dir_all(&temp);
            }
            result?;
        }
        let next = bound(&self.candidate(expected_sha)?)?;
        store.set_setting(
            "displayCandidate",
            &json!({"package":expected_sha,"base":self.base}),
        )?;
        self.describe(current, &next, store, expected_sha)
    }
    pub fn prepare_apply(
        &self,
        package: &str,
        base: &str,
        current: &Catalog,
        store: &Store,
    ) -> Result<(PathBuf, Catalog)> {
        self.current(store)?;
        let saved = store
            .setting("displayCandidate")?
            .ok_or(Error::new("PREVIEW_REQUIRED", "请先校验并预览资料更新"))?;
        if base != self.base || saved["base"] != base || saved["package"] != package {
            return Err(Error::new("PREVIEW_CHANGED", "当前目录已变化，请重新预览"));
        }
        let path = self.candidate(package)?;
        let next = bound(&path)?;
        let preview = self.describe(current, &next, store, package)?;
        if preview["canApply"] != true {
            return Err(Error::new(
                "REFERENCED_PRINTING_MISSING",
                "更新会移除收藏、卡组或冻结版本引用的 ID；请使用完整目录",
            ));
        }
        Ok((path, next))
    }
    pub fn did_apply(&mut self, path: PathBuf, base: String, package: String) {
        self.base = base;
        self.active = path;
        self.activation = Some(package);
    }
    pub fn activation(&self) -> Option<&str> {
        self.activation.as_deref()
    }
    fn current(&self, store: &Store) -> Result<()> {
        let saved = store.setting("displayActivation")?;
        if saved.as_ref().and_then(|v| v["package"].as_str()) != self.activation.as_deref() {
            return Err(Error::new(
                "PREVIEW_CHANGED",
                "目录已由另一实例更新，请重开 App 再预览",
            ));
        }
        Ok(())
    }
    pub fn prune(&self, store: &Store) -> Result<Value> {
        self.current(store)?;
        let pending = store.setting("displayCandidate")?;
        let candidate = pending.as_ref().and_then(|v| v["package"].as_str());
        let mut removed = 0;
        for row in fs::read_dir(&self.root)? {
            let row = row?;
            let name = row.file_name();
            let Some(name) = name.to_str() else { continue };
            if digest(name) && Some(name) != self.activation.as_deref() && Some(name) != candidate {
                safe_dir_existing(&row.path())?;
                fs::remove_dir_all(row.path())?;
                removed += 1;
            }
        }
        File::open(&self.root)?.sync_all()?;
        Ok(json!({"removed":removed}))
    }
}
fn verify_package(path: &Path, sha: &str) -> Result<()> {
    let raw = crate::catalog::read(path, "bundle.json", MAX_BUNDLE as u64)?;
    if hash(&raw) != sha {
        return Err(Error::new(
            "HASH_MISMATCH",
            "已保存资料包身份不符；未回退或覆盖资料",
        ));
    }
    let bundle: Bundle = serde_json::from_slice(&raw)?;
    for (name, content) in [
        ("catalog.json", bundle.catalog),
        ("artwork.json", bundle.artwork),
        ("manifest.json", bundle.manifest),
    ] {
        if hash(&crate::catalog::read(path, name, 64 * 1024 * 1024)?) != hash(content.as_bytes()) {
            return Err(Error::new("HASH_MISMATCH", "已保存资料文件与原包不同"));
        }
    }
    Ok(())
}
fn safe_dir_existing(path: &Path) -> Result<()> {
    let meta = fs::symlink_metadata(path)?;
    if !meta.is_dir() || meta.file_type().is_symlink() {
        return Err(Error::new("UNSAFE_STORAGE", "资料包路径无效"));
    }
    Ok(())
}
pub fn download(url: &str, sha: &str) -> Result<Vec<u8>> {
    if !digest(sha) || url.len() > 2048 || !url.is_ascii() || url.contains(['\\', '%']) {
        return Err(Error::new(
            "INVALID_RESOURCE_URL",
            "请输入公开 HTTPS 资料 URL 与 SHA-256",
        ));
    }
    let parsed =
        url::Url::parse(url).map_err(|_| Error::new("INVALID_RESOURCE_URL", "资料 URL 无效"))?;
    if parsed.scheme() != "https"
        || !parsed.username().is_empty()
        || parsed.password().is_some()
        || parsed.query().is_some()
        || parsed.fragment().is_some()
        || !matches!(parsed.host(),Some(url::Host::Domain(h))if h.contains('.')&&!h.ends_with(".local")&&!h.ends_with(".localhost"))
    {
        return Err(Error::new(
            "INVALID_RESOURCE_URL",
            "资料 URL 须为无凭据、无参数的公开 HTTPS 地址",
        ));
    }
    let client = reqwest::blocking::Client::builder()
        .use_preconfigured_tls(
            crate::tls::client("")
                .map_err(|_| Error::new("DOWNLOAD_FAILED", "无法初始化系统证书验证"))?,
        )
        .no_proxy()
        .redirect(reqwest::redirect::Policy::none())
        .connect_timeout(std::time::Duration::from_secs(8))
        .timeout(std::time::Duration::from_secs(60))
        .build()
        .map_err(|_| Error::new("DOWNLOAD_FAILED", "无法初始化资料连接"))?;
    let response = client
        .get(url)
        .header("Accept-Encoding", "identity")
        .send()
        .map_err(|_| Error::new("DOWNLOAD_FAILED", "资料下载失败；当前目录未改变"))?;
    if !response.status().is_success()
        || response
            .content_length()
            .is_some_and(|n| n > MAX_BUNDLE as u64)
        || response
            .headers()
            .get("Content-Encoding")
            .is_some_and(|h| h != "identity")
    {
        return Err(Error::new(
            "DOWNLOAD_FAILED",
            "资料响应失败、重定向或超限；当前目录未改变",
        ));
    }
    let mut bytes = Vec::new();
    response
        .take(MAX_BUNDLE as u64 + 1)
        .read_to_end(&mut bytes)?;
    if bytes.len() > MAX_BUNDLE || hash(&bytes) != sha {
        return Err(Error::new(
            "HASH_MISMATCH",
            "下载资料 SHA-256 不符；当前目录未改变",
        ));
    }
    Ok(bytes)
}
