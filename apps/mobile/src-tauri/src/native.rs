use crate::{
    artwork::ImageData,
    error::{Error, Result},
    Core, Request,
};
use serde_json::Value;
use std::sync::Arc;
use tauri::{Manager, WebviewWindow};

struct Host(Result<Arc<Core>>);
fn authorize(window: &WebviewWindow) -> Result<()> {
    let url = window
        .url()
        .map_err(|_| Error::new("LOCAL_AUTH_REQUIRED", "无法确认本机窗口来源"))?;
    if window.label() != "main" || !crate::permissions::local_navigation(&url) {
        return Err(Error::new(
            "LOCAL_AUTH_REQUIRED",
            "仅允许随包移动界面访问本机能力",
        ));
    }
    Ok(())
}
#[tauri::command]
async fn mobile_request(
    window: WebviewWindow,
    request: Value,
    host: tauri::State<'_, Host>,
) -> Result<Value> {
    authorize(&window)?;
    if serde_json::to_vec(&request)?.len() > 2 * 1024 * 1024 {
        return Err(Error::new("REQUEST_TOO_LARGE", "本机操作内容过大"));
    }
    let request: Request = serde_json::from_value(request)?;
    let core = host.0.clone()?;
    tauri::async_runtime::spawn_blocking(move || core.request(request))
        .await
        .map_err(|_| Error::new("LOCAL_FAILED", "本机操作中断，请重新打开页面"))?
}
#[tauri::command]
async fn mobile_artwork(
    window: WebviewWindow,
    printing_id: String,
    host: tauri::State<'_, Host>,
) -> Result<ImageData> {
    authorize(&window)?;
    crate::protection::available()?;
    if printing_id.len() > 256 {
        return Err(Error::new("INVALID_REQUEST", "卡牌编号过长"));
    }
    let core = host.0.clone()?;
    tauri::async_runtime::spawn_blocking(move || {
        core.artwork
            .lock()
            .map_err(|_| Error::new("LOCAL_FAILED", "图片缓存暂不可用"))?
            .get(&printing_id)
    })
    .await
    .map_err(|_| Error::new("LOCAL_FAILED", "卡图读取中断"))?
}

#[tauri::command]
async fn mobile_online(
    window: WebviewWindow,
    request: Value,
    host: tauri::State<'_, Host>,
) -> crate::online::error::Result<Value> {
    authorize(&window)?;
    crate::protection::available()?;
    if serde_json::to_vec(&request)?.len() > 256 * 1024 {
        return Err(crate::online::error::Error::new("REQUEST_TOO_LARGE"));
    }
    let request: crate::online::Request = serde_json::from_value(request)?;
    let core = host.0.clone()?;
    if matches!(request, crate::online::Request::Close {}) {
        core.online_epoch
            .fetch_add(1, std::sync::atomic::Ordering::AcqRel);
        if let Ok(mut owner) = core.online.try_lock() {
            owner.stop();
        }
        return Ok(serde_json::json!({"closed":true}));
    }
    if let crate::online::Request::Lifecycle { active } = request {
        core.set_online_active(active);
        if !active {
            if let Ok(mut owner) = core.online.try_lock() {
                owner.stop();
            }
        }
        return Ok(serde_json::json!({"active":active}));
    }
    tauri::async_runtime::spawn_blocking(move || {
        core.online
            .try_lock()
            .map_err(|_| crate::online::error::Error::new("PROFILE_BUSY"))?
            .handle(&core, request)
    })
    .await
    .map_err(|_| crate::online::error::Error::new("LOCAL_FAILED"))?
}
#[tauri::command]
async fn mobile_files(
    window: WebviewWindow,
    request: Value,
    host: tauri::State<'_, Host>,
) -> Result<Value> {
    authorize(&window)?;
    crate::protection::available()?;
    if serde_json::to_vec(&request)?.len() > 16384 {
        return Err(Error::new("REQUEST_TOO_LARGE", "文件请求过大"));
    }
    let request: crate::files::Request = serde_json::from_value(request)?;
    let core = host.0.clone()?;
    use crate::files::{system, text, Request as F};
    use serde_json::json;
    if let F::DownloadCatalog { url, sha256 } = request {
        return tauri::async_runtime::spawn_blocking(move || {
            let bytes = crate::resources::download(&url, &sha256)?;
            core.stage_catalog(&bytes, &sha256)
        })
        .await
        .map_err(|_| Error::new("DOWNLOAD_FAILED", "资料下载中断；当前目录未改变"))?;
    }
    let (kind, payload) = match &request {
        F::PickDeck {} => ("deck", json!({"op":"pick","kind":"deck"})),
        F::PickCollection {} => ("collection", json!({"op":"pick","kind":"collection"})),
        F::PickCatalog { .. } => ("catalog", json!({"op":"pick","kind":"catalog"})),
        F::ShareDeck { id, format } => {
            let value = core.request(Request::ExportDeck {
                id: id.clone(),
                format: *format,
            })?;
            let (text, filename) = if let Some(text) = value["text"].as_str() {
                (
                    text.to_owned(),
                    value["filename"].as_str().unwrap().to_owned(),
                )
            } else {
                (
                    serde_json::to_string_pretty(&value)?,
                    "ptcg-deck.json".into(),
                )
            };
            (
                "share",
                json!({"op":"share","kind":"deck","text":text,"filename":filename}),
            )
        }
        F::ShareCollection {} => {
            let value = core.request(Request::ExportCollection {})?;
            (
                "share",
                json!({"op":"share","kind":"collection","text":value["text"]}),
            )
        }
        _ => unreachable!(),
    };
    if let F::PickCatalog { sha256 } = &request {
        if !crate::resources::digest(sha256) {
            return Err(Error::new("HASH_REQUIRED", "请先填写资料文件的 SHA-256"));
        }
    }
    let Some(bytes) = system(payload).await? else {
        return Ok(json!({"cancelled":true}));
    };
    match request {
        F::PickCatalog { sha256 } => {
            tauri::async_runtime::spawn_blocking(move || core.stage_catalog(&bytes, &sha256))
                .await
                .map_err(|_| Error::new("FILE_UNAVAILABLE", "资料校验中断"))?
        }
        _ if kind == "share" => Ok(json!({"shared":true})),
        _ => {
            Ok(json!({"text":text(&bytes,if kind=="deck"{crate::decks::MAX_TEXT}else{1024*1024})?}))
        }
    }
}
#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .register_asynchronous_uri_scheme_protocol(
            "mobile-artwork",
            |context, request, responder| {
                let core = context.app_handle().state::<Host>().0.clone();
                tauri::async_runtime::spawn_blocking(move || {
                    let result = (|| -> Result<crate::artwork::ImageData> {
                        crate::protection::available()?;
                        if request.method() != "GET" || request.uri().host() != Some("localhost") {
                            return Err(Error::new("IMAGE_MISSING", "卡图路径无效"));
                        }
                        let path = request.uri().path().trim_start_matches('/');
                        let (blob, printing) = path
                            .split_once('/')
                            .ok_or(Error::new("IMAGE_MISSING", "卡图路径无效"))?;
                        let printing = percent_encoding::percent_decode_str(printing)
                            .decode_utf8()
                            .map_err(|_| Error::new("IMAGE_MISSING", "卡牌编号无效"))?;
                        let core = core?;
                        let mut images = core
                            .artwork
                            .lock()
                            .map_err(|_| Error::new("LOCAL_FAILED", "图片缓存暂不可用"))?;
                        images.referenced(blob, &printing)
                    })();
                    let response = match result {
                        Ok(data) => tauri::http::Response::builder()
                            .header("Access-Control-Allow-Origin", "tauri://localhost")
                            .header("Content-Type", data.mime)
                            .header("Cache-Control", "private, max-age=31536000, immutable")
                            .body(data.bytes)
                            .unwrap(),
                        Err(error) => {
                            let mut builder = tauri::http::Response::builder()
                                .status(if error.retry_after_seconds.is_some() {
                                    503
                                } else {
                                    404
                                })
                                .header("Access-Control-Allow-Origin", "tauri://localhost")
                                .header("Content-Type", "application/json")
                                .header("Cache-Control", "no-store");
                            if let Some(delay) = error.retry_after_seconds {
                                builder = builder.header("Retry-After", delay.to_string());
                            }
                            builder.body(serde_json::to_vec(&error).unwrap()).unwrap()
                        }
                    };
                    responder.respond(response);
                });
            },
        )
        .setup(|app| {
            // Display metadata is validated before opening the user database.
            let initialized = (|| -> Result<Arc<Core>> {
                let resources = app
                    .path()
                    .resource_dir()
                    .map_err(|_| Error::new("RESOURCE_MISSING", "找不到随包展示资源"))?
                    .join("catalog");
                let data = app
                    .path()
                    .app_data_dir()
                    .map_err(|_| Error::new("STORAGE_FAILED", "无法定位应用数据目录"))?;
                let cache = app
                    .path()
                    .app_cache_dir()
                    .map_err(|_| Error::new("STORAGE_FAILED", "无法定位应用缓存目录"))?
                    .join("artwork");
                let core = Arc::new(Core::open(&resources, &data, &cache)?);
                #[cfg(target_os = "ios")]
                crate::protection::observe(&core);
                Ok(core)
            })();
            app.manage(Host(initialized));
            let window = tauri::WebviewWindowBuilder::new(
                app,
                "main",
                tauri::WebviewUrl::App("index.html".into()),
            )
            .title("PTCG Lab");
            // iOS owns the screen/window geometry. A desktop-sized initial
            // frame clips the WebView on phones and wastes the iPad canvas.
            #[cfg(not(target_os = "ios"))]
            let window = window.inner_size(430.0, 900.0);
            window
                .on_navigation(crate::permissions::local_navigation)
                .build()?;
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            mobile_request,
            mobile_artwork,
            mobile_online,
            mobile_files
        ])
        .build(tauri::generate_context!())
        .expect("mobile runtime unavailable")
        .run(|app, event| {
            let suspended = matches!(
                event,
                tauri::RunEvent::WindowEvent {
                    event: tauri::WindowEvent::Suspended,
                    ..
                } | tauri::RunEvent::Exit
            );
            let resumed = matches!(
                event,
                tauri::RunEvent::Resumed
                    | tauri::RunEvent::WindowEvent {
                        event: tauri::WindowEvent::Resumed,
                        ..
                    }
            );
            if suspended || resumed {
                if let Ok(core) = &app.state::<Host>().0 {
                    core.set_online_active(resumed);
                    // All writes are already committed before replying. Checkpoint is
                    // supplementary; suspension never depends on JavaScript running.
                    if let Ok(store) = core.store.try_lock() {
                        let _ = store.checkpoint();
                    }
                }
            }
        });
}
