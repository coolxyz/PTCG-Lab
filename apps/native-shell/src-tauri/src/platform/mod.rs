//! OS-specific ownership. A platform failure never falls back to an unsafe host.
#[cfg(target_os = "macos")]
#[path = "macos_instance.rs"]
pub mod instance;
#[cfg(windows)]
#[path = "windows_instance.rs"]
pub mod instance;
#[cfg(target_os = "macos")]
#[path = "macos_process.rs"]
pub mod process;
#[cfg(windows)]
#[path = "windows_process.rs"]
pub mod process;
#[cfg(windows)]
pub mod security;

pub fn payload_platform() -> &'static str {
    if cfg!(all(target_os = "macos", target_arch = "aarch64")) {
        "macos-arm64"
    } else if cfg!(all(windows, target_arch = "x86_64")) {
        "windows-x64"
    } else {
        "unsupported"
    }
}

pub fn reparse(info: &std::fs::Metadata) -> bool {
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        info.file_attributes() & 0x400 != 0
    }
    #[cfg(not(windows))]
    {
        info.file_type().is_symlink()
    }
}

/// Show only fixed setup guidance, never OS errors, paths or configuration.
pub fn startup_error() {
    #[cfg(windows)]
    unsafe {
        use windows_sys::Win32::UI::WindowsAndMessaging::{MessageBoxW, MB_ICONERROR, MB_OK};
        if let (Ok(text), Ok(title)) = (security::wide("无法启动本机窗口。请确认已安装 Microsoft Edge WebView2 Runtime，并使用本机 NTFS 上当前用户的私有配置目录。"), security::wide("PTCG Lab — 启动失败")) {
            MessageBoxW(std::ptr::null_mut(), text.as_ptr(), title.as_ptr(), MB_OK | MB_ICONERROR);
        }
    }
    #[cfg(not(windows))]
    eprintln!("Native shell initialization failed");
}
