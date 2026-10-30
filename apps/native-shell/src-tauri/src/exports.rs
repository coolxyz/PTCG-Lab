//! A download never grants arbitrary URLs or server-supplied filesystem paths.
pub fn export_name(url: &url::Url, origin: Option<&str>) -> Option<(&'static str, &'static str)> {
    if !url.username().is_empty() || url.password().is_some() || url.fragment().is_some()
        || !origin.is_some_and(|value| url.origin().ascii_serialization() == value) {
        return None;
    }
    match (url.path(), url.query()) {
        ("/api/collection/export", None) => Some(("ptcg-collection", "csv")),
        ("/api/card-image-gaps", None) => Some(("ptcg-missing-images", "csv")),
        (path, format) => {
            let id = path.strip_prefix("/api/decks/")?.strip_suffix("/export")?;
            if id.is_empty() || !id.bytes().all(|c| c.is_ascii_alphanumeric() || b"_-".contains(&c)) {
                return None;
            }
            match format {
                None => Some(("ptcg-deck", "csv")),
                Some("format=text") => Some(("ptcg-deck", "txt")),
                _ => None,
            }
        }
    }
}

#[cfg(test)]
mod tests {
    #[test]
    fn downloads_only_allow_owned_export_endpoints_and_fixed_filenames() {
        let origin = Some("http://127.0.0.1:12345");
        for (path, expected) in [
            ("/api/collection/export", ("ptcg-collection", "csv")),
            ("/api/card-image-gaps", ("ptcg-missing-images", "csv")),
            ("/api/decks/deck_123/export", ("ptcg-deck", "csv")),
            ("/api/decks/deck-123/export?format=text", ("ptcg-deck", "txt")),
        ] {
            assert_eq!(super::export_name(&format!("http://127.0.0.1:12345{path}").parse().unwrap(), origin), Some(expected));
        }
        for value in [
            "https://evil.example/api/collection/export", "http://127.0.0.1:12346/api/collection/export",
            "http://user:pass@127.0.0.1:12345/api/collection/export", "file:///etc/passwd",
            "http://127.0.0.1:12345/api/collection/export?filename=evil",
            "http://127.0.0.1:12345/api/decks/a%2Fb/export", "http://127.0.0.1:12345/api/decks//export",
            "http://127.0.0.1:12345/api/decks/a/export?format=html", "http://127.0.0.1:12345/api/cards",
        ] {
            assert_eq!(super::export_name(&value.parse().unwrap(), origin), None);
        }
        assert_eq!(super::export_name(&"http://127.0.0.1:12345/api/collection/export".parse().unwrap(), None), None);
    }
}
