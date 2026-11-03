use serde::Serialize;

#[derive(Clone, Debug, Serialize)]
pub struct Error {
    pub code: String,
    pub status: u16,
    pub retryable: bool,
}
pub type Result<T> = std::result::Result<T, Error>;
impl Error {
    pub fn new(code: impl Into<String>) -> Self {
        Self {
            code: code.into(),
            status: 0,
            retryable: false,
        }
    }
    pub fn network() -> Self {
        Self {
            code: "NETWORK_UNAVAILABLE".into(),
            status: 0,
            retryable: true,
        }
    }
    pub fn invalid() -> Self {
        Self::new("INVALID_RESPONSE")
    }
}
impl From<crate::error::Error> for Error {
    fn from(error: crate::error::Error) -> Self {
        Self::new(error.code)
    }
}
impl From<std::io::Error> for Error {
    fn from(_: std::io::Error) -> Self {
        Self::new("JOURNAL_UNAVAILABLE")
    }
}
impl From<serde_json::Error> for Error {
    fn from(_: serde_json::Error) -> Self {
        Self::invalid()
    }
}
