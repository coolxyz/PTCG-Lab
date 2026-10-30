fn main() {
    #[cfg(feature = "desktop")]
    {
        let mut attributes = tauri_build::Attributes::new();
        if std::env::var("CARGO_CFG_TARGET_OS").as_deref() == Ok("windows") {
            println!("cargo:rerun-if-changed=icons/icon.ico");
            attributes = attributes.windows_attributes(
                tauri_build::WindowsAttributes::new().window_icon_path("icons/icon.ico")
                    .app_manifest(r#"<assembly xmlns="urn:schemas-microsoft-com:asm.v1" manifestVersion="1.0">
<dependency><dependentAssembly><assemblyIdentity type="win32" name="Microsoft.Windows.Common-Controls" version="6.0.0.0" processorArchitecture="*" publicKeyToken="6595b64144ccf1df" language="*"/></dependentAssembly></dependency>
<trustInfo xmlns="urn:schemas-microsoft-com:asm.v3"><security><requestedPrivileges><requestedExecutionLevel level="asInvoker" uiAccess="false"/></requestedPrivileges></security></trustInfo>
<application xmlns="urn:schemas-microsoft-com:asm.v3"><windowsSettings><longPathAware xmlns="http://schemas.microsoft.com/SMI/2016/WindowsSettings">true</longPathAware></windowsSettings></application>
</assembly>"#),
            );
        }
        tauri_build::try_build(attributes).expect("native build configuration failed");
    }
}
