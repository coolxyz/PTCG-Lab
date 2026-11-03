use serde::Serialize;

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Error {
    pub code: &'static str,
    pub message: &'static str,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub retry_after_seconds: Option<u64>,
}
pub type Result<T> = std::result::Result<T, Error>;

impl Error {
    pub fn new(code: &'static str, message: &'static str) -> Self {
        Self {
            code,
            message,
            retry_after_seconds: None,
        }
    }
    pub fn busy(seconds: u64) -> Self {
        Self {
            code: "IMAGE_SOURCE_BUSY",
            message: "图源繁忙，已暂停新的图片下载",
            retry_after_seconds: Some(seconds),
        }
    }
    pub fn retry(seconds: u64) -> Self {
        Self {
            code: "IMAGE_RETRY",
            message: "图源暂时不可用，请稍后重试",
            retry_after_seconds: Some(seconds),
        }
    }
}
impl From<rusqlite::Error> for Error {
    fn from(_: rusqlite::Error) -> Self {
        Self::new(
            "STORAGE_FAILED",
            "本机存储失败；原数据已保留，请检查可用空间后重试",
        )
    }
}
impl From<std::io::Error> for Error {
    fn from(_: std::io::Error) -> Self {
        Self::new(
            "FILE_UNAVAILABLE",
            "本机文件暂不可读取或保存；不会重建或清空用户资料",
        )
    }
}
impl From<serde_json::Error> for Error {
    fn from(_: serde_json::Error) -> Self {
        Self::new("INVALID_DATA", "资料格式无效")
    }
}
