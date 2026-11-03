fn main() {
    if std::env::var("CARGO_CFG_TARGET_OS").as_deref() == Ok("ios")
        && std::env::var_os("CARGO_FEATURE_NATIVE").is_some()
    {
        println!("cargo:rerun-if-changed=ios/Sources");
        println!("cargo:rerun-if-changed=ios/Package.swift");
        // Xcode exports the device/simulator SDKROOT to build phases. SwiftPM
        // compiles Package.swift for macOS first; inheriting the iOS SDK breaks
        // that host compilation. swift-rs selects the actual target SDK itself.
        // Only this build-script process and its Swift child are affected.
        let xcode_sdkroot = std::env::var_os("SDKROOT");
        std::env::remove_var("SDKROOT");
        swift_rs::SwiftLinker::new("11.0")
            .with_ios("16.0")
            .with_package("MobileDocuments", "ios")
            .link();
        if let Some(sdkroot) = xcode_sdkroot {
            std::env::set_var("SDKROOT", sdkroot);
        }
        println!("cargo:rustc-link-lib=framework=UIKit");
        println!("cargo:rustc-link-lib=framework=UniformTypeIdentifiers");
    }
    #[cfg(feature = "native")]
    tauri_build::try_build(tauri_build::Attributes::new().app_manifest(
        tauri_build::AppManifest::new().commands(&[
            "mobile_request",
            "mobile_artwork",
            "mobile_online",
            "mobile_files",
        ]),
    ))
    .expect("mobile build configuration failed");
}
