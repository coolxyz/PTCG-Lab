use crate::{
    catalog::{id, read},
    error::{Error, Result},
};
use serde::{Deserialize, Serialize};
use sha1::{Digest, Sha1};
use std::{
    collections::BTreeMap,
    fs::{self, OpenOptions},
    io::{Read, Write},
    path::{Path, PathBuf},
    time::{Duration, Instant},
};
use url::Url;

const MAX: u64 = 8 * 1024 * 1024;
#[derive(Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Face {
    path: String,
    blob: String,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Index {
    repository: String,
    commit: String,
    cards: BTreeMap<String, Face>,
}
#[derive(Clone, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Source {
    pub source: String,
    pub base_url: String,
    pub capacity_mi_b: u32,
}
impl Default for Source {
    fn default() -> Self {
        Self {
            source: "github-raw".into(),
            base_url: String::new(),
            capacity_mi_b: 512,
        }
    }
}
impl Source {
    pub fn validate(&self) -> Result<()> {
        if !(64..=2048).contains(&self.capacity_mi_b)
            || !["github-raw", "cdn"].contains(&self.source.as_str())
        {
            return Err(Error::new(
                "INVALID_IMAGE_SOURCE",
                "请选择在线图源和 64 至 2048 MiB 缓存",
            ));
        }
        if self.source == "cdn" || !self.base_url.is_empty() {
            let url = Url::parse(&self.base_url)
                .map_err(|_| Error::new("INVALID_IMAGE_SOURCE", "请输入完整的 HTTPS 图源地址"))?;
            if url.scheme() != "https"
                || !url.username().is_empty()
                || url.password().is_some()
                || url.port().is_some_and(|p| p != 443)
                || url.query().is_some()
                || url.fragment().is_some()
                || self.base_url.contains(['%', '\\'])
                || self.base_url.len() > 1024
                || !self.base_url.is_ascii()
                || !matches!(url.host(),Some(url::Host::Domain(host)) if host.contains('.') && !host.ends_with(".local") && !host.ends_with(".localhost"))
            {
                return Err(Error::new(
                    "INVALID_IMAGE_SOURCE",
                    "静态图源须为无凭据、无参数的 HTTPS 域名",
                ));
            }
        }
        Ok(())
    }
}
#[derive(Serialize)]
pub struct ImageData {
    pub bytes: Vec<u8>,
    pub mime: String,
}
pub trait Download: Send {
    fn get(&mut self, url: &str) -> Result<Vec<u8>>;
}
pub struct HttpDownload {
    client: reqwest::blocking::Client,
}
impl HttpDownload {
    pub fn new() -> Result<Self> {
        Ok(Self {
            client: reqwest::blocking::Client::builder()
                .use_preconfigured_tls(
                    crate::tls::client("")
                        .map_err(|_| Error::new("NETWORK_UNAVAILABLE", "无法初始化系统证书验证"))?,
                )
                .redirect(reqwest::redirect::Policy::none())
                .timeout(Duration::from_secs(20))
                .connect_timeout(Duration::from_secs(8))
                .no_proxy()
                .build()
                .map_err(|_| Error::new("NETWORK_UNAVAILABLE", "无法初始化原生图片客户端"))?,
        })
    }
}
impl Download for HttpDownload {
    fn get(&mut self, url: &str) -> Result<Vec<u8>> {
        let response = self.client.get(url).send().map_err(|_| Error::retry(2))?;
        if response.status().as_u16() == 429 || response.status().is_server_error() {
            let delay = response
                .headers()
                .get("Retry-After")
                .and_then(|v| v.to_str().ok())
                .and_then(|s| s.parse::<u64>().ok())
                .unwrap_or(5)
                .clamp(1, 86400);
            return Err(
                if response.status().as_u16() == 429
                    || response.headers().contains_key("Retry-After")
                {
                    Error::busy(delay)
                } else {
                    Error::retry(delay)
                },
            );
        }
        if !response.status().is_success() {
            return Err(Error::new(
                "IMAGE_MISSING",
                "图源未提供此图片；可在设置中检查 CDN 索引或切换来源",
            ));
        }
        if response.content_length().is_some_and(|n| n > MAX) {
            return Err(Error::new("IMAGE_TOO_LARGE", "卡图超过 8 MiB，已拒绝读取"));
        }
        let mut bytes = Vec::new();
        response
            .take(MAX + 1)
            .read_to_end(&mut bytes)
            .map_err(|_| Error::retry(2))?;
        Ok(bytes)
    }
}

pub fn validate_resources(resources: &Path) -> Result<std::collections::BTreeSet<String>> {
    Ok(index(resources)?.cards.into_keys().collect())
}
fn index(resources: &Path) -> Result<Index> {
    let index: Index = serde_json::from_slice(&read(resources, "artwork.json", 16 * 1024 * 1024)?)?;
    let url = Url::parse(&index.repository)
        .map_err(|_| Error::new("INVALID_RESOURCE", "卡图来源身份无效"))?;
    if url.scheme() != "https"
        || url.host_str() != Some("github.com")
        || url.path_segments().map(|v| v.count()) != Some(2)
        || !url.username().is_empty()
        || url.password().is_some()
        || url.query().is_some()
        || url.fragment().is_some()
        || url.port().is_some()
        || url.as_str().trim_end_matches('/') != index.repository
        || !hex(&index.commit, 40)
        || index.cards.len() > 100000
    {
        return Err(Error::new("INVALID_RESOURCE", "卡图来源身份无效"));
    }
    for face in index.cards.values() {
        if !face.path.starts_with("img/")
            || face
                .path
                .split('/')
                .any(|p| p.is_empty() || p == "." || p == "..")
            || face.path.contains(['\\', '%', '?', '#', ':'])
            || !hex(&face.blob, 40)
        {
            return Err(Error::new("INVALID_RESOURCE", "卡图路径或内容身份无效"));
        }
    }
    Ok(index)
}

pub struct Artwork {
    root: PathBuf,
    repository: String,
    commit: String,
    cards: BTreeMap<String, Face>,
    pub source: Source,
    download: Box<dyn Download>,
    cooldown: Option<(Instant, Error)>,
    failures: BTreeMap<String, (Instant, Error)>,
}
impl Artwork {
    pub fn reference(&self, printing: &str) -> Option<String> {
        self.cards.get(printing).map(|face| {
            format!(
                "mobile-artwork://localhost/{}/{}",
                face.blob,
                percent_encoding::utf8_percent_encode(printing, percent_encoding::NON_ALPHANUMERIC)
            )
        })
    }
    pub fn referenced(&mut self, blob: &str, printing: &str) -> Result<ImageData> {
        if self.cards.get(printing).is_none_or(|f| f.blob != blob) {
            return Err(Error::new("IMAGE_MISSING", "卡图内容身份不存在"));
        }
        self.get(printing)
    }
    pub fn load(
        resources: &Path,
        root: PathBuf,
        source: Source,
        download: Box<dyn Download>,
    ) -> Result<Self> {
        source.validate()?;
        let index = index(resources)?;
        fs::create_dir_all(&root)?;
        if fs::symlink_metadata(&root)?.file_type().is_symlink() {
            return Err(Error::new("UNSAFE_STORAGE", "图片缓存不能位于链接目录"));
        }
        Ok(Self {
            root,
            repository: index.repository,
            commit: index.commit,
            cards: index.cards,
            source,
            download,
            cooldown: None,
            failures: BTreeMap::new(),
        })
    }
    pub fn configure(&mut self, source: Source) -> Result<()> {
        source.validate()?;
        self.source = source;
        self.failures.clear();
        self.cooldown = None;
        self.prune(0)?;
        Ok(())
    }
    pub fn get(&mut self, printing: &str) -> Result<ImageData> {
        // iOS may reclaim caches while keeping application data intact.
        fs::create_dir_all(&self.root)?;
        if fs::symlink_metadata(&self.root)?.file_type().is_symlink() {
            return Err(Error::new("UNSAFE_STORAGE", "图片缓存目录无效"));
        }
        let face = self
            .cards
            .get(printing)
            .cloned()
            .ok_or(Error::new("IMAGE_MISSING", "该印刷版本没有经过确认的卡图"))?;
        let path = self.root.join(format!("{}.image", face.blob));
        if path.is_file() && !path.is_symlink() {
            if let Ok(bytes) = read(&self.root, &format!("{}.image", face.blob), MAX) {
                if let Ok(mime) = validate(&bytes, &face.blob) {
                    // Refresh LRU without changing or re-encoding the image bytes.
                    if let Ok(file) = OpenOptions::new().write(true).open(&path) {
                        let _ = file.set_modified(std::time::SystemTime::now());
                    }
                    return Ok(ImageData { bytes, mime });
                }
            }
        }
        if let Some((until, error)) = &self.cooldown {
            if *until > Instant::now() {
                return Err(error.clone());
            }
        }
        if let Some((until, error)) = self.failures.get(&face.blob) {
            if *until > Instant::now() {
                return Err(error.clone());
            }
        }
        let base = if self.source.source == "github-raw" {
            "https://raw.githubusercontent.com"
        } else {
            self.source.base_url.trim_end_matches('/')
        };
        let mut url =
            Url::parse(base).map_err(|_| Error::new("INVALID_IMAGE_SOURCE", "卡图来源无效"))?;
        {
            let mut segments = url
                .path_segments_mut()
                .map_err(|_| Error::new("INVALID_IMAGE_SOURCE", "卡图来源路径无效"))?;
            segments.pop_if_empty();
            for part in self
                .repository
                .trim_start_matches("https://github.com/")
                .split('/')
                .chain(std::iter::once(self.commit.as_str()))
                .chain(face.path.split('/'))
            {
                segments.push(part);
            }
        }
        let bytes = match self.download.get(url.as_str()) {
            Ok(bytes) => bytes,
            Err(error) => {
                if error.code == "IMAGE_SOURCE_BUSY" {
                    self.cooldown = Some((
                        Instant::now()
                            + Duration::from_secs(error.retry_after_seconds.unwrap_or(5)),
                        error.clone(),
                    ));
                }
                if self.failures.len() >= 256 {
                    self.failures.clear();
                }
                self.failures.insert(
                    face.blob.clone(),
                    (
                        Instant::now()
                            + Duration::from_secs(error.retry_after_seconds.unwrap_or(60)),
                        error.clone(),
                    ),
                );
                return Err(error);
            }
        };
        let mime = validate(&bytes, &face.blob)?;
        self.prune(bytes.len() as u64)?;
        let temporary = self.root.join(format!("{}.tmp", id()?));
        let result = (|| -> Result<()> {
            let mut stream = OpenOptions::new()
                .write(true)
                .create_new(true)
                .open(&temporary)?;
            stream.write_all(&bytes)?;
            stream.sync_all()?;
            fs::rename(&temporary, &path)?;
            Ok(())
        })();
        let _ = fs::remove_file(temporary);
        result?;
        self.failures.remove(&face.blob);
        Ok(ImageData { bytes, mime })
    }
    pub fn cached_bytes(&self) -> Result<u64> {
        Ok(self.files()?.iter().map(|r| r.2).sum())
    }
    fn files(&self) -> Result<Vec<(std::time::SystemTime, PathBuf, u64)>> {
        let mut files = Vec::new();
        for entry in fs::read_dir(&self.root)? {
            let path = entry?.path();
            let info = fs::symlink_metadata(&path)?;
            if info.is_file()
                && !info.file_type().is_symlink()
                && path
                    .file_stem()
                    .and_then(|s| s.to_str())
                    .is_some_and(|s| hex(s, 40))
                && path.extension().is_some_and(|s| s == "image")
            {
                files.push((info.modified()?, path, info.len()));
            }
        }
        files.sort_by_key(|r| r.0);
        Ok(files)
    }
    fn prune(&self, incoming: u64) -> Result<()> {
        let files = self.files()?;
        let mut total = files.iter().map(|r| r.2).sum::<u64>() + incoming;
        let limit = self.source.capacity_mi_b as u64 * 1024 * 1024;
        for (_, path, size) in files {
            if total <= limit {
                break;
            }
            fs::remove_file(path)?;
            total = total.saturating_sub(size);
        }
        Ok(())
    }
}
fn hex(s: &str, len: usize) -> bool {
    s.len() == len
        && s.bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}
pub fn validate(bytes: &[u8], expected: &str) -> Result<String> {
    if bytes.is_empty() || bytes.len() as u64 > MAX {
        return Err(Error::new("INVALID_IMAGE", "卡图为空或超过大小限制"));
    }
    let mut digest = Sha1::new();
    digest.update(format!("blob {}\0", bytes.len()).as_bytes());
    digest.update(bytes);
    if format!("{:x}", digest.finalize()) != expected {
        return Err(Error::new(
            "IMAGE_HASH_MISMATCH",
            "卡图与固定索引不一致；未显示或缓存",
        ));
    }
    let mut reader = image::ImageReader::new(std::io::Cursor::new(bytes))
        .with_guessed_format()
        .map_err(|_| Error::new("INVALID_IMAGE", "卡图格式无效"))?;
    let mime = match reader.format() {
        Some(image::ImageFormat::Png) => "image/png",
        Some(image::ImageFormat::Jpeg) => "image/jpeg",
        Some(image::ImageFormat::WebP) => "image/webp",
        _ => return Err(Error::new("INVALID_IMAGE", "只接受 PNG、JPEG 或 WebP 卡图")),
    };
    let mut limits = image::Limits::default();
    limits.max_image_width = Some(8192);
    limits.max_image_height = Some(8192);
    limits.max_alloc = Some(64 * 1024 * 1024);
    reader.limits(limits);
    reader
        .decode()
        .map_err(|_| Error::new("INVALID_IMAGE", "卡图无法完整解码或像素数过大"))?;
    Ok(mime.into())
}
