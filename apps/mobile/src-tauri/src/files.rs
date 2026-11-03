//! Only product document selection/sharing; never expose filesystem paths or identity files.
use crate::error::{Error, Result};
use serde::Deserialize;
#[cfg(feature = "native")]
use serde_json::Value;
#[derive(Deserialize)]
#[serde(
    tag = "op",
    rename_all = "kebab-case",
    rename_all_fields = "camelCase",
    deny_unknown_fields
)]
pub enum Request {
    PickDeck {},
    PickCollection {},
    PickCatalog {
        sha256: String,
    },
    DownloadCatalog {
        url: String,
        sha256: String,
    },
    ShareDeck {
        id: String,
        format: Option<crate::decks::Format>,
    },
    ShareCollection {},
}
pub fn text(bytes: &[u8], limit: usize) -> Result<String> {
    if bytes.len() > limit {
        return Err(Error::new("IMPORT_TOO_LARGE", "所选文件超限；未导入资料"));
    }
    let text = std::str::from_utf8(bytes)
        .map_err(|_| Error::new("INVALID_TEXT", "请选择 UTF-8 JSON、文本或 CSV 文件"))?;
    if text.contains('\0') {
        return Err(Error::new("INVALID_TEXT", "文件包含无效的空字符"));
    }
    Ok(text.into())
}
#[cfg(all(feature = "native", target_os = "ios"))]
pub async fn system(payload: Value) -> Result<Option<Vec<u8>>> {
    use std::ffi::{c_char, c_void, CString};
    type Reply = tauri::async_runtime::Sender<Result<Option<Vec<u8>>>>;
    // The Swift coordinator owns one request until its exactly-once completion.
    extern "C" {
        fn ptcg_mobile_file_request(
            input: *const c_char,
            context: *mut c_void,
            callback: extern "C" fn(*mut c_void, i32, *const u8, usize),
        );
    }
    extern "C" fn complete(context: *mut c_void, status: i32, bytes: *const u8, len: usize) {
        let sender = unsafe { Box::from_raw(context as *mut Reply) };
        let result = if status == 0 {
            Ok(None)
        } else if status == 1
            && len <= crate::resources::MAX_BUNDLE
            && (!bytes.is_null() || len == 0)
        {
            Ok(Some(if len == 0 {
                vec![]
            } else {
                unsafe { std::slice::from_raw_parts(bytes, len) }.to_vec()
            }))
        } else {
            Err(Error::new(
                if status == 3 {
                    "FILE_BUSY"
                } else {
                    "FILE_UNAVAILABLE"
                },
                "系统文件操作未完成；请重试，原数据未改变",
            ))
        };
        let _ = sender.try_send(result);
    }
    let raw = CString::new(payload.to_string())
        .map_err(|_| Error::new("INVALID_DATA", "文件请求无效"))?;
    let (sender, receiver) = tauri::async_runtime::channel(1);
    // The mpsc sender completion does not await and cannot retain an untrusted path.
    let context = Box::into_raw(Box::new(sender)) as *mut c_void;
    unsafe { ptcg_mobile_file_request(raw.as_ptr(), context, complete) };
    let mut receiver = receiver;
    receiver
        .recv()
        .await
        .ok_or(Error::new("FILE_UNAVAILABLE", "文件请求中断"))?
}
#[cfg(all(feature = "native", not(target_os = "ios")))]
pub async fn system(_: Value) -> Result<Option<Vec<u8>>> {
    Err(Error::new(
        "IOS_REQUIRED",
        "系统文件选择与分享仅在 iOS App 中提供",
    ))
}
