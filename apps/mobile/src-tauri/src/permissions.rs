pub fn local_navigation(url: &url::Url) -> bool {
    url.scheme() == "tauri"
        && url.host_str() == Some("localhost")
        && url.port().is_none()
        && url.username().is_empty()
        && url.password().is_none()
        && url.query().is_none()
        // Non-special schemes retain an empty root path. WKWebView may load
        // tauri://localhost (without the slash) on its first navigation.
        && ["", "/", "/index.html"].contains(&url.path())
}

#[cfg(test)]
mod tests {
    #[test]
    fn only_the_embedded_product_page_is_allowed() {
        for value in [
            "tauri://localhost",
            "tauri://localhost/",
            "tauri://localhost/index.html#decks",
        ] {
            assert!(super::local_navigation(&url::Url::parse(value).unwrap()));
        }
        for value in [
            "https://example.com/",
            "http://127.0.0.1:5193/",
            "tauri://user@localhost/",
            "tauri://localhost:123/",
            "tauri://evil.invalid/",
            "tauri://localhost/index.html?token=x",
            "tauri://localhost/another.html",
            "file:///private/tmp/index.html",
        ] {
            assert!(!super::local_navigation(&url::Url::parse(value).unwrap()));
        }
    }
}
