//! Platform ownership and backend protocol; no WebView command authority.
#[cfg(feature = "desktop")]
pub mod acceptance;
pub mod backend;
pub mod exports;
pub mod platform;

#[cfg(test)]
mod tests {
    pub fn scratch(label: &str) -> std::path::PathBuf {
        let mut random = [0u8; 8];
        getrandom::fill(&mut random).unwrap();
        let suffix: String = random.iter().map(|b| format!("{b:02x}")).collect();
        let root = std::env::temp_dir()
            .canonicalize()
            .unwrap()
            .join(format!("ptcg-{label}-{suffix}"));
        #[cfg(target_os = "macos")]
        {
            use std::os::unix::fs::DirBuilderExt;
            std::fs::DirBuilder::new()
                .mode(0o700)
                .create(&root)
                .unwrap();
        }
        #[cfg(windows)]
        drop(crate::platform::security::PrivateDirectory::open(&root).unwrap());
        root
    }
}
