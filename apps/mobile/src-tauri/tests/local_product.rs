use ptcg_mobile_lib::{
    artwork::{Artwork, Download, Source},
    catalog::{hash, id, Catalog, Entry},
    error::{Error, Result},
    import_deck,
    store::Store,
    Request,
};
use serde_json::json;
use sha1::{Digest, Sha1};
use std::{
    fs,
    path::PathBuf,
    sync::{Arc, Mutex},
};

struct Scratch(PathBuf);
impl Scratch {
    fn new() -> Self {
        let root = std::env::temp_dir().join(format!("ptcg-ios-{}", id().unwrap()));
        fs::create_dir(&root).unwrap();
        Self(root)
    }
}
impl Drop for Scratch {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}
fn catalog() -> Catalog {
    Catalog::from_value(json!({"version":"fixture-1","cards":[
    {"printingId":"CN:TEST:001","engineId":"TEST1","cnName":"测试牌","isBasicPokemon":true},
    {"printingId":"CN:TEST:002","engineId":"TEST2","cnName":"测试能量","isBasicPokemon":false}]}))
    .unwrap()
}
fn entry(q: u32) -> Entry {
    Entry {
        printing_id: "CN:TEST:001".into(),
        quantity: q,
    }
}

#[test]
fn drafts_reopen_and_stale_writers_cannot_overwrite() {
    let temp = Scratch::new();
    let catalog = catalog();
    let mut first = Store::open(&temp.0).unwrap();
    let deck = first
        .save(
            &catalog,
            None,
            "中文 空格 卡组".into(),
            vec![entry(3)],
            None,
        )
        .unwrap();
    let mut second = Store::open(&temp.0).unwrap();
    let newer = first
        .save(
            &catalog,
            Some(deck.id.clone()),
            "更新名称".into(),
            vec![entry(4)],
            Some(1),
        )
        .unwrap();
    assert_eq!(
        second
            .save(
                &catalog,
                Some(deck.id.clone()),
                "过期覆盖".into(),
                vec![entry(1)],
                Some(1)
            )
            .err()
            .unwrap()
            .code,
        "VERSION_CONFLICT"
    );
    drop(first);
    drop(second);
    let reopened = Store::open(&temp.0).unwrap();
    let saved = reopened.deck(&deck.id).unwrap();
    assert_eq!(saved.entries, newer.entries);
    assert_eq!(saved.version, 2);
    assert_eq!(saved.name, "更新名称");
}

#[test]
fn invalid_entries_and_collection_conflicts_do_not_partially_commit() {
    let temp = Scratch::new();
    let mut store = Store::open(&temp.0).unwrap();
    let catalog = catalog();
    assert!(store
        .save(
            &catalog,
            None,
            "坏牌".into(),
            vec![Entry {
                printing_id: "wrong-id".into(),
                quantity: 1
            }],
            None
        )
        .is_err());
    assert!(store.decks().unwrap().is_empty());
    assert!(store
        .holding(&catalog, "CN:TEST:001".into(), 2, true, 0)
        .is_ok());
    assert_eq!(
        store
            .holding(&catalog, "CN:TEST:002".into(), 1, false, 0)
            .err()
            .unwrap()
            .code,
        "VERSION_CONFLICT"
    );
    assert_eq!(
        store.collection().unwrap(),
        json!({"version":1,"entries":[{"printingId":"CN:TEST:001","quantity":2,"wishlist":true,"condition":"未标注","notes":""}]})
    );
}

#[test]
fn frozen_versions_remain_immutable_after_draft_edit_and_delete() {
    let temp = Scratch::new();
    let mut store = Store::open(&temp.0).unwrap();
    let catalog = catalog();
    let deck = store
        .save(&catalog, None, "冻结测试".into(), vec![entry(60)], None)
        .unwrap();
    let frozen = store.freeze(&catalog, deck.id.clone(), 1).unwrap();
    assert_eq!(frozen["validation"]["scope"], "structure-only");
    assert_eq!(frozen["validation"]["structureReady"], true);
    store
        .save(
            &catalog,
            Some(deck.id.clone()),
            "修改草稿".into(),
            vec![entry(2)],
            Some(1),
        )
        .unwrap();
    store.delete(deck.id, 2).unwrap();
    drop(store);
    let db = rusqlite::Connection::open(temp.0.join("mobile.sqlite")).unwrap();
    let saved: String = db
        .query_row(
            "SELECT entries FROM revisions WHERE id=?1",
            [frozen["id"].as_str().unwrap()],
            |r| r.get(0),
        )
        .unwrap();
    assert_eq!(
        serde_json::from_str::<Vec<Entry>>(&saved).unwrap(),
        vec![entry(60)]
    );
}

#[test]
fn newer_or_corrupt_database_is_never_reset() {
    let temp = Scratch::new();
    let file = temp.0.join("mobile.sqlite");
    let db = rusqlite::Connection::open(&file).unwrap();
    db.execute_batch("CREATE TABLE sentinel(value TEXT); INSERT INTO sentinel VALUES('keep'); PRAGMA user_version=3;").unwrap();
    drop(db);
    let before = fs::read(&file).unwrap();
    assert_eq!(Store::open(&temp.0).err().unwrap().code, "NEWER_DATABASE");
    assert_eq!(before, fs::read(&file).unwrap());
    fs::write(&file, b"corrupt user bytes").unwrap();
    assert!(Store::open(&temp.0).is_err());
    assert_eq!(fs::read(file).unwrap(), b"corrupt user bytes");
}

#[test]
fn desktop_text_and_mobile_json_keep_printing_ids_without_rule_migration() {
    let (_, entries) = import_deck("2 CN:TEST:001\n1 CN:TEST:002", "fixture-1").unwrap();
    assert_eq!(entries[0], entry(2));
    let body = json!({"schema":1,"catalogVersion":"fixture-1","name":"导入","entries":[entry(2)]})
        .to_string();
    assert_eq!(import_deck(&body, "fixture-1").unwrap().1, vec![entry(2)]);
    assert_eq!(
        import_deck(&body, "different-version").err().unwrap().code,
        "CATALOG_MISMATCH"
    );
    assert!(import_deck(
        "{\"name\":\"x\",\"entries\":[],\"credentials\":\"no\"}",
        "fixture-1"
    )
    .is_err());
    assert_eq!(
        catalog().normalize(&[entry(1), entry(2)]).unwrap(),
        vec![entry(3)]
    );
}

#[test]
fn desktop_csv_and_text_round_trip_through_mobile_exports() {
    use ptcg_mobile_lib::decks::{export, Format};
    // Exact bytes produced by desktop export_csv / text endpoints (BOM + CRLF).
    for text in [
        "\u{feff}printingId,quantity\r\nCN:TEST:001,2\r\nCN:TEST:002,1\r\n",
        "2 CN:TEST:001\n1 CN:TEST:002",
        "\u{feff}quantity,printingId\r\n\"2\",\"CN:TEST:001\"\r\n1,CN:TEST:002\r\n",
    ] {
        let temp = Scratch::new();
        let mut store = Store::open(&temp.0).unwrap();
        let (name, entries) = import_deck(text, "fixture-1").unwrap();
        let deck = store.save(&catalog(), None, name, entries, None).unwrap();
        assert_eq!(
            deck.entries,
            vec![
                entry(2),
                Entry {
                    printing_id: "CN:TEST:002".into(),
                    quantity: 1
                }
            ]
        );
        for format in [Format::Csv, Format::Text, Format::Json] {
            let value = export(&deck, "fixture-1", format).unwrap();
            let text = value["text"]
                .as_str()
                .map(str::to_owned)
                .unwrap_or_else(|| value.to_string());
            assert_eq!(import_deck(&text, "fixture-1").unwrap().1, deck.entries);
        }
        assert!(export(&deck, "fixture-1", Format::Csv).unwrap()["text"]
            .as_str()
            .unwrap()
            .starts_with("\u{feff}printingId,quantity\r\n"));
    }
}

#[test]
fn malformed_or_unknown_decks_never_partially_replace_existing_data() {
    let temp = Scratch::new();
    let mut store = Store::open(&temp.0).unwrap();
    let catalog = catalog();
    let deck = store
        .save(&catalog, None, "保留原卡组".into(), vec![entry(2)], None)
        .unwrap();
    let mut cases = vec![
        "printingId,quantity\nCN:TEST:001,2\nunknown,1".to_owned(),
        "printingId,quantity\nCN:TEST:001,2\nCN:TEST:002,no".into(),
        "printingId,quantity,quantity\nCN:TEST:001,2,3".into(),
        "printingId,quantity\nCN:TEST:001,2,extra".into(),
        "printingId,quantity\nCN:TEST:001".into(),
        "name,quantity\n测试牌,2".into(),
        "printingId,quantity\nCN:TEST:001,0".into(),
        "printingId,quantity\nCN:TEST:001,61".into(),
        "printingId,quantity\nCN:TEST:001,-1".into(),
        "# nothing\n".into(),
        "printingId,quantity\r\n".into(),
        "1 CN:TEST:001\0".into(),
        "x".repeat(128 * 1024 + 1),
    ];
    cases.push(format!(
        "printingId,quantity\n{}",
        "CN:TEST:001,1\n".repeat(501)
    ));
    for text in cases {
        let imported = import_deck(&text, "fixture-1")
            .and_then(|(name, entries)| store.save(&catalog, None, name, entries, None));
        assert!(imported.is_err(), "unexpected import: {text:.80}");
        assert_eq!(store.decks().unwrap().len(), 1);
        assert_eq!(store.deck(&deck.id).unwrap().entries, vec![entry(2)]);
    }
}

#[test]
fn stale_deletion_keeps_new_draft_and_immutable_snapshots() {
    let temp = Scratch::new();
    let mut first = Store::open(&temp.0).unwrap();
    let catalog = catalog();
    let deck = first
        .save(&catalog, None, "原卡组".into(), vec![entry(2)], None)
        .unwrap();
    let frozen = first.freeze(&catalog, deck.id.clone(), 1).unwrap();
    let mut second = Store::open(&temp.0).unwrap();
    second
        .save(
            &catalog,
            Some(deck.id.clone()),
            "新版".into(),
            vec![entry(3)],
            Some(1),
        )
        .unwrap();
    assert_eq!(
        first.delete(deck.id.clone(), 1).unwrap_err().code,
        "VERSION_CONFLICT"
    );
    assert_eq!(first.deck(&deck.id).unwrap().name, "新版");
    first.delete(deck.id.clone(), 2).unwrap();
    drop(first);
    drop(second);
    let reopened = Store::open(&temp.0).unwrap();
    assert!(reopened.decks().unwrap().is_empty());
    assert_eq!(
        reopened.revisions(&deck.id).unwrap()["revisions"][0]["contentHash"],
        frozen["contentHash"]
    );
}

#[test]
fn command_boundary_rejects_execution_paths_and_unknown_fields() {
    for request in [
        json!({"op":"practice"}),
        json!({"op":"capabilities","path":"outside"}),
        json!({"op":"card","printingId":"x","url":"https://example.com"}),
        json!({"op":"save-deck","id":null,"name":"x","entries":[{"printingId":"CN:TEST:001","quantity":true}],"expectedVersion":null}),
    ] {
        assert!(serde_json::from_value::<Request>(request).is_err());
    }
}

type Calls = Arc<Mutex<Vec<String>>>;
struct Fake {
    bytes: Vec<u8>,
    calls: Calls,
}
impl Download for Fake {
    fn get(&mut self, url: &str) -> Result<Vec<u8>> {
        self.calls.lock().unwrap().push(url.into());
        Ok(self.bytes.clone())
    }
}
fn png() -> Vec<u8> {
    let mut bytes = std::io::Cursor::new(Vec::new());
    image::DynamicImage::new_rgb8(6, 8)
        .write_to(&mut bytes, image::ImageFormat::Png)
        .unwrap();
    bytes.into_inner()
}
fn blob(bytes: &[u8]) -> String {
    let mut h = Sha1::new();
    h.update(format!("blob {}\0", bytes.len()));
    h.update(bytes);
    format!("{:x}", h.finalize())
}
fn resource(root: &PathBuf, bytes: &[u8]) {
    fs::write(root.join("artwork.json"),json!({"repository":"https://github.com/example/Cards","commit":"a".repeat(40),"cards":{"CN:TEST:001":{"path":"img/1/test.png","blob":blob(bytes)}}}).to_string()).unwrap();
}

#[test]
fn fixed_raw_image_is_verified_cached_and_available_offline_after_reopen() {
    let temp = Scratch::new();
    let bytes = png();
    resource(&temp.0, &bytes);
    let calls = Calls::default();
    let mut images = Artwork::load(
        &temp.0,
        temp.0.join("cache"),
        Source::default(),
        Box::new(Fake {
            bytes: bytes.clone(),
            calls: calls.clone(),
        }),
    )
    .unwrap();
    assert_eq!(images.get("CN:TEST:001").unwrap().bytes, bytes);
    assert_eq!(
        calls.lock().unwrap().as_slice(),
        [format!(
            "https://raw.githubusercontent.com/example/Cards/{}/img/1/test.png",
            "a".repeat(40)
        )]
    );
    drop(images);
    let mut offline = Artwork::load(
        &temp.0,
        temp.0.join("cache"),
        Source::default(),
        Box::new(Fake {
            bytes: Vec::new(),
            calls: calls.clone(),
        }),
    )
    .unwrap();
    assert_eq!(offline.get("CN:TEST:001").unwrap().bytes, bytes);
    assert_eq!(calls.lock().unwrap().len(), 1);
    assert_eq!(offline.get("unknown").err().unwrap().code, "IMAGE_MISSING");
}

#[test]
fn wrong_blob_and_truncated_raster_never_enter_cache() {
    let temp = Scratch::new();
    let bytes = png();
    resource(&temp.0, &bytes);
    let calls = Calls::default();
    let mut images = Artwork::load(
        &temp.0,
        temp.0.join("cache"),
        Source::default(),
        Box::new(Fake {
            bytes: b"Git LFS pointer".to_vec(),
            calls,
        }),
    )
    .unwrap();
    assert_eq!(
        images.get("CN:TEST:001").err().unwrap().code,
        "IMAGE_HASH_MISMATCH"
    );
    assert_eq!(images.cached_bytes().unwrap(), 0);
    let broken = &bytes[..bytes.len() / 2];
    assert_eq!(
        ptcg_mobile_lib::artwork::validate(broken, &blob(broken))
            .err()
            .unwrap()
            .code,
        "INVALID_IMAGE"
    );
}

#[test]
fn tampered_display_catalogue_fails_before_opening_user_data() {
    let temp = Scratch::new();
    let raw = json!({"version":"v1","cards":[{"printingId":"P1","cnName":"测试牌"}]}).to_string();
    let artwork =
        json!({"repository":"https://github.com/example/Cards","commit":"a".repeat(40),"cards":{}})
            .to_string();
    fs::write(temp.0.join("catalog.json"), &raw).unwrap();
    fs::write(temp.0.join("artwork.json"), &artwork).unwrap();
    fs::write(temp.0.join("manifest.json"),json!({"schema":1,"catalogVersion":"v1","files":{"catalog.json":hash(raw.as_bytes()),"artwork.json":hash(artwork.as_bytes())}}).to_string()).unwrap();
    assert!(Catalog::load(&temp.0).is_ok());
    fs::write(temp.0.join("catalog.json"), "changed").unwrap();
    assert_eq!(
        Catalog::load(&temp.0).err().unwrap().code,
        "INVALID_RESOURCE"
    );
}

#[test]
fn insecure_or_credentialed_image_sources_are_rejected() {
    for base in [
        "http://images.example.com",
        "https://user:password@images.example.com",
        "https://127.0.0.1",
        "https://images.example.com/a?token=x",
        "https://images.example.com/#x",
    ] {
        assert!(Source {
            source: "cdn".into(),
            base_url: base.into(),
            capacity_mi_b: 512
        }
        .validate()
        .is_err());
    }
    assert!(Source {
        source: "local-git".into(),
        base_url: String::new(),
        capacity_mi_b: 512
    }
    .validate()
    .is_err());
}

struct Sequence {
    results: std::collections::VecDeque<Result<Vec<u8>>>,
    calls: Calls,
}
impl Download for Sequence {
    fn get(&mut self, url: &str) -> Result<Vec<u8>> {
        self.calls.lock().unwrap().push(url.into());
        self.results.pop_front().expect("Unexpected image request")
    }
}
fn second_face(root: &PathBuf) -> Vec<u8> {
    let mut picture = image::RgbImage::new(6, 8);
    picture.put_pixel(0, 0, image::Rgb([100, 25, 180]));
    let mut buffer = std::io::Cursor::new(Vec::new());
    image::DynamicImage::ImageRgb8(picture)
        .write_to(&mut buffer, image::ImageFormat::Png)
        .unwrap();
    let bytes = buffer.into_inner();
    let path = root.join("artwork.json");
    let mut index: serde_json::Value = serde_json::from_slice(&fs::read(&path).unwrap()).unwrap();
    index["cards"]["CN:TEST:002"] = json!({"path":"img/1/two.png","blob":blob(&bytes)});
    index["cards"]["CN:TEST:003"] = json!({"path":"img/1/three.png","blob":"c".repeat(40)});
    fs::write(path, index.to_string()).unwrap();
    bytes
}

#[test]
fn a_network_failure_does_not_pause_unrelated_images() {
    let temp = Scratch::new();
    resource(&temp.0, &png());
    let bytes = second_face(&temp.0);
    let calls = Calls::default();
    let mut images = Artwork::load(
        &temp.0,
        temp.0.join("cache"),
        Source::default(),
        Box::new(Sequence {
            results: [Err(Error::retry(2)), Ok(bytes.clone())].into(),
            calls: calls.clone(),
        }),
    )
    .unwrap();
    assert_eq!(images.get("CN:TEST:001").err().unwrap().code, "IMAGE_RETRY");
    assert_eq!(images.get("CN:TEST:002").unwrap().bytes, bytes);
    assert_eq!(calls.lock().unwrap().len(), 2);
}

#[test]
fn explicit_source_throttling_stops_new_downloads_but_preserves_cached_images() {
    let temp = Scratch::new();
    resource(&temp.0, &png());
    let bytes = second_face(&temp.0);
    let calls = Calls::default();
    let mut images = Artwork::load(
        &temp.0,
        temp.0.join("cache"),
        Source::default(),
        Box::new(Sequence {
            results: [Ok(bytes.clone()), Err(Error::busy(60))].into(),
            calls: calls.clone(),
        }),
    )
    .unwrap();
    images.get("CN:TEST:002").unwrap();
    assert_eq!(
        images.get("CN:TEST:001").err().unwrap().code,
        "IMAGE_SOURCE_BUSY"
    );
    assert_eq!(
        images.get("CN:TEST:003").err().unwrap().code,
        "IMAGE_SOURCE_BUSY"
    );
    assert_eq!(images.get("CN:TEST:002").unwrap().bytes, bytes);
    assert_eq!(calls.lock().unwrap().len(), 2);
}

#[test]
fn permanent_image_errors_have_no_automatic_retry_metadata() {
    let value = serde_json::to_value(Error::new("IMAGE_MISSING", "missing")).unwrap();
    assert!(value.get("retryAfterSeconds").is_none());
}

#[test]
fn frozen_content_tampering_is_reported_without_rewriting_the_identity() {
    let temp = Scratch::new();
    let mut store = Store::open(&temp.0).unwrap();
    let catalog = catalog();
    let deck = store
        .save(&catalog, None, "保存版本".into(), vec![entry(2)], None)
        .unwrap();
    let frozen = store.freeze(&catalog, deck.id.clone(), 1).unwrap();
    assert_eq!(
        store.revisions(&deck.id).unwrap()["revisions"][0]["id"],
        frozen["id"]
    );
    drop(store);
    let db = rusqlite::Connection::open(temp.0.join("mobile.sqlite")).unwrap();
    db.execute("UPDATE revisions SET content_hash='tampered'", [])
        .unwrap();
    drop(db);
    let reopened = Store::open(&temp.0).unwrap();
    assert_eq!(
        reopened.revisions(&deck.id).err().unwrap().code,
        "INVALID_REVISION"
    );
    assert_eq!(reopened.deck(&deck.id).unwrap().entries, vec![entry(2)]);
}
