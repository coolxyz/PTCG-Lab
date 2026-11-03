use ptcg_mobile_lib::{
    catalog::{hash, id, Catalog, Entry},
    collection::{self, Holding},
    store::Store,
    Core, Request,
};
use rusqlite::Connection;
use serde_json::{json, Value};
use std::{
    fs,
    path::{Path, PathBuf},
};
struct Scratch(PathBuf);
impl Scratch {
    fn new() -> Self {
        let root = std::env::temp_dir()
            .canonicalize()
            .unwrap()
            .join(format!("ptcg-e3-{}", id().unwrap()));
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
    Catalog::from_value(json!({"version":"v1","cards":[{"printingId":"P1","engineId":"E1","cnName":"测试牌"},{"printingId":"P2","engineId":"E2","cnName":"测试能量"}]})).unwrap()
}
fn row(pid: &str, q: u32, notes: &str) -> Holding {
    Holding {
        printing_id: pid.into(),
        quantity: q,
        condition: "良好".into(),
        notes: notes.into(),
        wishlist: false,
    }
}
fn csv() -> &'static str {
    "printingId,quantity,condition,notes,wishlist\r\nP1,2,良好,导入备注,true\r\nP2,1,全新,,false\r\n"
}
#[test]
fn schema_one_upgrades_in_one_transaction_and_preserves_decks_revisions_and_holdings() {
    let t = Scratch::new();
    let file = t.0.join("mobile.sqlite");
    let db = Connection::open(&file).unwrap();
    db.execute_batch("CREATE TABLE decks(id TEXT PRIMARY KEY,name TEXT,entries TEXT,version INTEGER);CREATE TABLE revisions(id TEXT PRIMARY KEY,deck_id TEXT,catalog_version TEXT,entries TEXT,content_hash TEXT);CREATE TABLE holdings(printing_id TEXT PRIMARY KEY,quantity INTEGER,wishlist INTEGER);CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT);INSERT INTO settings VALUES('collectionVersion','7');INSERT INTO holdings VALUES('P1',3,1);INSERT INTO decks VALUES('deck','旧草稿','[{\"printingId\":\"P1\",\"quantity\":2}]',4);PRAGMA user_version=1;").unwrap();
    let entries = json!([{"printingId":"P1","quantity":2}]);
    let digest = hash(
        &serde_json::to_vec(&json!({"catalogVersion":"original-identity","entries":entries}))
            .unwrap(),
    );
    db.execute(
        "INSERT INTO revisions VALUES('frozen','deck','original-identity',?1,?2)",
        rusqlite::params![entries.to_string(), digest],
    )
    .unwrap();
    drop(db);
    let store = Store::open(&t.0).unwrap();
    let value = store.collection().unwrap();
    assert_eq!(value["version"], 7);
    assert_eq!(value["entries"][0]["quantity"], 3);
    assert_eq!(value["entries"][0]["condition"], "未标注");
    assert_eq!(value["entries"][0]["notes"], "");
    assert_eq!(store.deck("deck").unwrap().version, 4);
    assert_eq!(
        store.revisions("deck").unwrap()["revisions"][0]["contentHash"],
        digest
    );
    drop(store);
    assert_eq!(Store::open(&t.0).unwrap().collection().unwrap(), value);
}
#[test]
fn failed_migration_keeps_the_original_schema_and_rows() {
    let t = Scratch::new();
    let db = Connection::open(t.0.join("mobile.sqlite")).unwrap();
    db.execute_batch("CREATE TABLE decks(id TEXT,name TEXT,entries TEXT,version INTEGER);CREATE TABLE revisions(id TEXT,deck_id TEXT,catalog_version TEXT,entries TEXT,content_hash TEXT);CREATE TABLE settings(key TEXT,value TEXT);CREATE TABLE holdings(printing_id TEXT,quantity INTEGER,wishlist INTEGER);INSERT INTO holdings VALUES('P1',10000,0);PRAGMA user_version=1;").unwrap();
    drop(db);
    assert!(Store::open(&t.0).is_err());
    let db = Connection::open(t.0.join("mobile.sqlite")).unwrap();
    assert_eq!(
        db.query_row("PRAGMA user_version", [], |r| r.get::<_, u64>(0))
            .unwrap(),
        1
    );
    assert_eq!(
        db.query_row("SELECT quantity FROM holdings", [], |r| r.get::<_, u64>(0))
            .unwrap(),
        10000
    );
}
#[test]
fn collection_changes_are_atomic_and_versioned_by_the_whole_batch() {
    let t = Scratch::new();
    let mut store = Store::open(&t.0).unwrap();
    store
        .set_collection(&catalog(), vec![row("P1", 2, "备注")], 0)
        .unwrap();
    let before = store.collection().unwrap();
    assert!(store
        .set_collection(
            &catalog(),
            vec![row("P1", 9, "改变"), row("absent", 1, "")],
            1
        )
        .is_err());
    assert_eq!(store.collection().unwrap(), before);
    assert!(store
        .set_collection(&catalog(), vec![row("P1", 9, "")], 0)
        .is_err());
    assert_eq!(store.collection().unwrap(), before);
    store.holding(&catalog(), "P1".into(), 1, false, 1).unwrap();
    let now = store.collection().unwrap();
    assert_eq!(now["entries"].as_array().unwrap().len(), 2);
    assert_eq!(
        now["entries"]
            .as_array()
            .unwrap()
            .iter()
            .filter_map(|e| e["quantity"].as_u64())
            .sum::<u64>(),
        3
    );
}
#[test]
fn import_is_idempotent_and_undo_preserves_edits_after_import() {
    let t = Scratch::new();
    let mut store = Store::open(&t.0).unwrap();
    store
        .set_collection(&catalog(), vec![row("P1", 3, "原备注")], 0)
        .unwrap();
    let preview = collection::preview(csv(), &catalog()).unwrap();
    let batch = store
        .import_collection(
            &catalog(),
            csv(),
            1,
            preview["previewHash"].as_str().unwrap(),
        )
        .unwrap();
    let before = store.collection().unwrap();
    assert!(store
        .import_collection(
            &catalog(),
            csv(),
            0,
            preview["previewHash"].as_str().unwrap()
        )
        .unwrap()["duplicate"]
        .as_bool()
        .unwrap());
    assert_eq!(store.collection().unwrap(), before);
    store
        .set_collection(&catalog(), vec![row("P1", 6, "后续备注")], 2)
        .unwrap();
    store
        .undo_collection(batch["id"].as_str().unwrap(), 3)
        .unwrap();
    let current = store.collection().unwrap();
    let entries = current["entries"].as_array().unwrap();
    let first = entries.iter().find(|e| e["printingId"] == "P1").unwrap();
    assert_eq!(first["quantity"], 4);
    assert_eq!(first["notes"], "后续备注");
    assert_eq!(first["wishlist"], false);
    store
        .undo_collection(batch["id"].as_str().unwrap(), 0)
        .unwrap();
    assert_eq!(store.collection().unwrap(), current);
    assert!(store
        .import_collection(
            &catalog(),
            csv(),
            4,
            preview["previewHash"].as_str().unwrap()
        )
        .is_err());
}
#[test]
fn undo_conflicts_roll_back_every_row_and_the_version() {
    let t = Scratch::new();
    let mut store = Store::open(&t.0).unwrap();
    let preview = collection::preview(csv(), &catalog()).unwrap();
    let batch = store
        .import_collection(
            &catalog(),
            csv(),
            0,
            preview["previewHash"].as_str().unwrap(),
        )
        .unwrap();
    store
        .set_collection(
            &catalog(),
            vec![Holding {
                condition: "全新".into(),
                ..row("P2", 0, "")
            }],
            1,
        )
        .unwrap();
    let before = store.collection().unwrap();
    assert_eq!(
        store
            .undo_collection(batch["id"].as_str().unwrap(), 2)
            .unwrap_err()
            .code,
        "UNDO_CONFLICT"
    );
    assert_eq!(store.collection().unwrap(), before);
    assert_eq!(store.collection_batches().unwrap()[0]["status"], "applied");
}
#[test]
fn desktop_csv_roundtrips_chinese_quotes_newlines_and_formula_prefixes() {
    let t = Scratch::new();
    let mut store = Store::open(&t.0).unwrap();
    let expected = vec![
        row("P1", 4, "=SUM(1,2)\n\"中文备注\""),
        row("P2", 1, "'原始单引号"),
    ];
    store
        .set_collection(&catalog(), expected.clone(), 0)
        .unwrap();
    let exported = store.export_collection().unwrap();
    assert!(exported.starts_with('\u{feff}'));
    assert_eq!(collection::parse(&exported, &catalog()).unwrap(), expected);
    for invalid in [
        "printingId,quantity,condition,notes,wishlist\nP1,2,未知,,false",
        "printingId,quantity,condition,notes,wishlist\nP1,NaN,良好,,false",
        "printingId,quantity,condition,notes,wishlist\nmissing,2,良好,,false",
        "printingId,quantity,condition,notes,wishlist\nP1,2,良好,,maybe",
        "printingId,quantity,condition,notes,notes\nP1,2,良好,,false",
    ] {
        assert!(collection::parse(invalid, &catalog()).is_err());
    }
}
fn metadata(path: &Path, version: &str, ids: &[&str]) -> Vec<u8> {
    fs::create_dir_all(path).unwrap();
    let cards:Vec<_>=ids.iter().map(|id|json!({"printingId":id,"engineId":format!("E{id}"),"cnName":"合成展示资料","hasArtwork":false})).collect();
    let card =
        json!({"version":version,"displayCatalogVersion":hash(version.as_bytes()),"cards":cards})
            .to_string();
    let artwork =
        json!({"repository":"https://github.com/example/Cards","commit":"a".repeat(40),"cards":{}})
            .to_string();
    let manifest=json!({"schema":1,"catalogVersion":version,"files":{"catalog.json":hash(card.as_bytes()),"artwork.json":hash(artwork.as_bytes())}}).to_string();
    for (name, content) in [
        ("catalog.json", &card),
        ("artwork.json", &artwork),
        ("manifest.json", &manifest),
    ] {
        fs::write(path.join(name), content).unwrap();
    }
    serde_json::to_vec(&json!({"schema":1,"kind":"ptcg-mobile-display","catalog":card,"artwork":artwork,"manifest":manifest})).unwrap()
}
fn core(t: &Scratch) -> Core {
    Core::open(&t.0.join("builtin"), &t.0.join("data"), &t.0.join("cache")).unwrap()
}
fn apply(core: &Core, preview: &Value) -> ptcg_mobile_lib::error::Result<Value> {
    core.request(Request::ApplyCatalog {
        package: preview["package"].as_str().unwrap().into(),
        base: preview["base"].as_str().unwrap().into(),
    })
}
#[test]
fn staged_directory_reopens_then_applies_without_rewriting_user_or_frozen_identity() {
    let t = Scratch::new();
    metadata(&t.0.join("builtin"), "v1", &["P1"]);
    let c = core(&t);
    let deck = c
        .request(Request::SaveDeck {
            id: None,
            name: "保留草稿".into(),
            entries: vec![Entry {
                printing_id: "P1".into(),
                quantity: 1,
            }],
            expected_version: None,
        })
        .unwrap();
    let frozen = c
        .request(Request::FreezeDeck {
            id: deck["id"].as_str().unwrap().into(),
            expected_version: 1,
        })
        .unwrap();
    let next = metadata(&t.0.join("next"), "v2", &["P1", "P2"]);
    let p = c.stage_catalog(&next, &hash(&next)).unwrap();
    assert_eq!(c.catalog.read().unwrap().version, "v1");
    assert_eq!(p["added"], 1);
    drop(c);
    let c = core(&t);
    assert_eq!(
        c.request(Request::CatalogStatus {}).unwrap()["pending"]["package"],
        p["package"]
    );
    apply(&c, &p).unwrap();
    assert_eq!(c.catalog.read().unwrap().version, "v2");
    drop(c);
    let c = core(&t);
    assert_eq!(c.catalog.read().unwrap().version, "v2");
    let store = c.store.lock().unwrap();
    assert_eq!(
        store.deck(deck["id"].as_str().unwrap()).unwrap().name,
        "保留草稿"
    );
    let revision = store.revisions(deck["id"].as_str().unwrap()).unwrap();
    assert_eq!(
        revision["revisions"][0]["contentHash"],
        frozen["contentHash"]
    );
    assert_eq!(revision["revisions"][0]["catalogVersion"], "v1");
}
#[test]
fn hash_failure_or_tampered_stage_never_changes_active_directory() {
    let t = Scratch::new();
    metadata(&t.0.join("builtin"), "v1", &["P1"]);
    let c = core(&t);
    let next = metadata(&t.0.join("next"), "v2", &["P1", "P2"]);
    assert_eq!(
        c.stage_catalog(&next, &"0".repeat(64)).unwrap_err().code,
        "HASH_MISMATCH"
    );
    let p = c.stage_catalog(&next, &hash(&next)).unwrap();
    fs::write(
        t.0.join("data/display-updates")
            .join(p["package"].as_str().unwrap())
            .join("catalog.json"),
        "{}",
    )
    .unwrap();
    assert!(apply(&c, &p).is_err());
    assert_eq!(c.catalog.read().unwrap().version, "v1");
    assert!(c
        .store
        .lock()
        .unwrap()
        .setting("displayActivation")
        .unwrap()
        .is_none());
}
#[test]
fn removing_a_referenced_printing_is_blocked_at_apply_even_if_added_after_preview() {
    let t = Scratch::new();
    metadata(&t.0.join("builtin"), "v1", &["P1", "P2"]);
    let c = core(&t);
    let next = metadata(&t.0.join("next"), "v2", &["P2"]);
    let p = c.stage_catalog(&next, &hash(&next)).unwrap();
    assert_eq!(p["canApply"], true);
    c.request(Request::SetHolding {
        printing_id: "P1".into(),
        quantity: 1,
        wishlist: false,
        expected_version: 0,
    })
    .unwrap();
    assert_eq!(
        apply(&c, &p).unwrap_err().code,
        "REFERENCED_PRINTING_MISSING"
    );
    assert_eq!(c.catalog.read().unwrap().version, "v1");
}
#[test]
fn failed_activation_commit_keeps_memory_and_restart_on_the_old_directory() {
    let t = Scratch::new();
    metadata(&t.0.join("builtin"), "v1", &["P1"]);
    let c = core(&t);
    let next = metadata(&t.0.join("next"), "v2", &["P1", "P2"]);
    let p = c.stage_catalog(&next, &hash(&next)).unwrap();
    let db = Connection::open(t.0.join("data/mobile.sqlite")).unwrap();
    db.execute_batch("CREATE TRIGGER fail_activation BEFORE INSERT ON settings WHEN NEW.key='displayActivation' BEGIN SELECT RAISE(ABORT,'simulated disk failure');END;").unwrap();
    drop(db);
    assert!(apply(&c, &p).is_err());
    assert_eq!(c.catalog.read().unwrap().version, "v1");
    drop(c);
    assert_eq!(core(&t).catalog.read().unwrap().version, "v1");
}
#[test]
fn corrupted_active_release_is_preserved_and_never_silently_replaced() {
    let t = Scratch::new();
    metadata(&t.0.join("builtin"), "v1", &["P1"]);
    let c = core(&t);
    let next = metadata(&t.0.join("next"), "v2", &["P1", "P2"]);
    let p = c.stage_catalog(&next, &hash(&next)).unwrap();
    apply(&c, &p).unwrap();
    drop(c);
    let path =
        t.0.join("data/display-updates")
            .join(p["package"].as_str().unwrap())
            .join("bundle.json");
    fs::write(&path, "changed").unwrap();
    assert!(Core::open(&t.0.join("builtin"), &t.0.join("data"), &t.0.join("cache")).is_err());
    assert_eq!(fs::read_to_string(path).unwrap(), "changed");
}
#[test]
fn file_contract_rejects_paths_headers_and_invalid_utf8() {
    use ptcg_mobile_lib::files::{text, Request as F};
    for value in [
        json!({"op":"pick-deck","path":"/private/identity"}),
        json!({"op":"share-collection","headers":{}}),
        json!({"op":"share-file","path":"any"}),
    ] {
        assert!(serde_json::from_value::<F>(value).is_err());
    }
    assert!(text(&[255], 100).is_err());
    assert!(text(b"a\0b", 100).is_err());
    assert!(text(b"toolarge", 2).is_err());
}

#[test]
fn stale_owner_cannot_overwrite_an_activation_committed_by_another_owner() {
    let t = Scratch::new();
    metadata(&t.0.join("builtin"), "v1", &["P1"]);
    let old = core(&t);
    let other = core(&t);
    let next = metadata(&t.0.join("next"), "v2", &["P1", "P2"]);
    let p = other.stage_catalog(&next, &hash(&next)).unwrap();
    apply(&other, &p).unwrap();
    let later = metadata(&t.0.join("later"), "v3", &["P1", "P2", "P3"]);
    assert_eq!(
        old.stage_catalog(&later, &hash(&later)).unwrap_err().code,
        "PREVIEW_CHANGED"
    );
    drop(old);
    drop(other);
    assert_eq!(core(&t).catalog.read().unwrap().version, "v2");
}
#[test]
fn pruning_unused_metadata_keeps_active_candidate_and_user_records() {
    let t = Scratch::new();
    metadata(&t.0.join("builtin"), "v1", &["P1"]);
    let c = core(&t);
    let next = metadata(&t.0.join("next"), "v2", &["P1", "P2"]);
    let old = c.stage_catalog(&next, &hash(&next)).unwrap();
    apply(&c, &old).unwrap();
    let later = metadata(&t.0.join("later"), "v3", &["P1", "P2", "P3"]);
    let current = c.stage_catalog(&later, &hash(&later)).unwrap();
    apply(&c, &current).unwrap();
    let future = metadata(&t.0.join("future"), "v4", &["P1", "P2", "P3", "P4"]);
    let pending = c.stage_catalog(&future, &hash(&future)).unwrap();
    c.request(Request::SetHolding {
        printing_id: "P1".into(),
        quantity: 7,
        wishlist: true,
        expected_version: 0,
    })
    .unwrap();
    let before = c.request(Request::Collection {}).unwrap();
    let result = c.request(Request::PruneCatalogUpdates {}).unwrap();
    assert_eq!(result["removed"], 1);
    let root = t.0.join("data/display-updates");
    assert!(!root.join(old["package"].as_str().unwrap()).exists());
    assert!(root.join(current["package"].as_str().unwrap()).exists());
    assert!(root.join(pending["package"].as_str().unwrap()).exists());
    assert_eq!(c.request(Request::Collection {}).unwrap(), before);
    drop(c);
    assert_eq!(core(&t).catalog.read().unwrap().version, "v3");
}
#[test]
fn an_unversioned_existing_database_is_never_repurposed() {
    let t = Scratch::new();
    let file = t.0.join("mobile.sqlite");
    let db = Connection::open(&file).unwrap();
    db.execute_batch("CREATE TABLE sentinel(value TEXT);INSERT INTO sentinel VALUES('keep');")
        .unwrap();
    drop(db);
    assert_eq!(Store::open(&t.0).err().unwrap().code, "INVALID_STORAGE");
    let db = Connection::open(&file).unwrap();
    assert_eq!(
        db.query_row("SELECT value FROM sentinel", [], |r| r.get::<_, String>(0))
            .unwrap(),
        "keep"
    );
    assert_eq!(
        db.query_row("PRAGMA user_version", [], |r| r.get::<_, u32>(0))
            .unwrap(),
        0
    );
}
#[test]
fn a_valid_checksum_cannot_install_malformed_display_fields() {
    let t = Scratch::new();
    metadata(&t.0.join("builtin"), "v1", &["P1"]);
    let c = core(&t);
    let next = metadata(&t.0.join("next"), "v2", &["P1", "P2"]);
    let mut bundle: Value = serde_json::from_slice(&next).unwrap();
    let mut catalog: Value = serde_json::from_str(bundle["catalog"].as_str().unwrap()).unwrap();
    catalog["cards"][0]["chineseDetails"] = json!({"sections":"not an array"});
    let raw = catalog.to_string();
    let mut manifest: Value = serde_json::from_str(bundle["manifest"].as_str().unwrap()).unwrap();
    manifest["files"]["catalog.json"] = hash(raw.as_bytes()).into();
    bundle["catalog"] = raw.into();
    bundle["manifest"] = manifest.to_string().into();
    let bytes = serde_json::to_vec(&bundle).unwrap();
    assert_eq!(
        c.stage_catalog(&bytes, &hash(&bytes)).unwrap_err().code,
        "INVALID_RESOURCE"
    );
    assert_eq!(c.catalog.read().unwrap().version, "v1");
    assert!(c
        .store
        .lock()
        .unwrap()
        .setting("displayCandidate")
        .unwrap()
        .is_none());
}
