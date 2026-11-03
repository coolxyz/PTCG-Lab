//! OS data protection is independent of JavaScript lifecycle notifications.
use crate::error::{Error, Result};
use std::path::Path;
#[cfg(all(target_os = "ios", feature = "native"))]
extern "C" {
    fn ptcg_mobile_data_available() -> i32;
    fn ptcg_mobile_protect(path: *const std::ffi::c_char) -> i32;
    fn ptcg_mobile_protect_file(path: *const std::ffi::c_char) -> i32;
}
pub fn secure_file(path: &Path) -> Result<()> {
    #[cfg(all(target_os = "ios", feature = "native"))]
    {
        let raw = std::ffi::CString::new(
            path.to_str()
                .ok_or(Error::new("UNSAFE_STORAGE", "应用文件路径无效"))?,
        )
        .map_err(|_| Error::new("UNSAFE_STORAGE", "应用文件路径无效"))?;
        if unsafe { ptcg_mobile_protect_file(raw.as_ptr()) } != 1 {
            return Err(Error::new(
                "DATA_PROTECTION_FAILED",
                "无法保护应用文件；原数据未替换",
            ));
        }
    }
    #[cfg(not(all(target_os = "ios", feature = "native")))]
    let _ = path;
    Ok(())
}
pub fn available() -> Result<()> {
    #[cfg(all(target_os = "ios", feature = "native"))]
    if unsafe { ptcg_mobile_data_available() } != 1 {
        return Err(Error::new(
            "DEVICE_LOCKED",
            "设备数据已锁定，请解锁后重试；原资料已保留",
        ));
    }
    Ok(())
}
pub fn prepare(root: &Path) -> Result<()> {
    available()?;
    std::fs::create_dir_all(root)?;
    for path in [root.to_path_buf(), root.join("online")] {
        if path.exists() && std::fs::symlink_metadata(&path)?.file_type().is_symlink() {
            return Err(Error::new("UNSAFE_STORAGE", "应用资料目录不能是符号链接"));
        }
        std::fs::create_dir_all(&path)?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o700))?;
        }
    }
    #[cfg(all(target_os = "ios", feature = "native"))]
    {
        let path = std::ffi::CString::new(
            root.to_str()
                .ok_or(Error::new("UNSAFE_STORAGE", "应用资料路径无效"))?,
        )
        .map_err(|_| Error::new("UNSAFE_STORAGE", "应用资料路径无效"))?;
        if unsafe { ptcg_mobile_protect(path.as_ptr()) } != 1 {
            return Err(Error::new(
                "DATA_PROTECTION_FAILED",
                "无法启用应用数据保护；未清理原资料",
            ));
        }
    }
    Ok(())
}
#[cfg(all(target_os = "ios", feature = "native"))]
static CORE: std::sync::OnceLock<std::sync::Weak<crate::Core>> = std::sync::OnceLock::new();
#[cfg(all(target_os = "ios", feature = "native"))]
pub fn observe(core: &std::sync::Arc<crate::Core>) {
    let _ = CORE.set(std::sync::Arc::downgrade(core));
}
#[cfg(all(target_os = "ios", feature = "native"))]
#[no_mangle]
pub extern "C" fn ptcg_mobile_protected_data_changed(active: i32) {
    if let Some(core) = CORE.get().and_then(std::sync::Weak::upgrade) {
        core.set_online_active(active == 1);
    }
}
