//! Host-side real-metadata probe, using disposable local data; not iOS acceptance.
use ptcg_mobile_lib::{
    catalog::{id, Entry},
    Core, Request,
};
use serde_json::json;
use std::path::PathBuf;

struct Scratch(PathBuf);
impl Drop for Scratch {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

fn main() {
    let resources = PathBuf::from(
        std::env::args_os()
            .nth(1)
            .expect("Provide prepared resources directory"),
    );
    let scratch =
        Scratch(std::env::temp_dir().join(format!("ptcg-mobile-probe-{}", id().unwrap())));
    std::fs::create_dir(&scratch.0).unwrap();
    let data = scratch.0.join("data");
    let cache = scratch.0.join("cache");
    let core = Core::open(&resources, &data, &cache).unwrap();
    let catalog = core.catalog.read().unwrap();
    let count = catalog.cards.len();
    assert!(count > 0);
    let display = core
        .online_catalog(catalog.display_version.as_deref().unwrap(), "unavailable")
        .unwrap();
    let display_count = display["cards"].as_array().unwrap().len();
    assert!(display_count > 0);
    assert!(display["cards"]
        .as_array()
        .unwrap()
        .iter()
        .all(|card| card["image"]["url"].is_null()
            || card["image"]["url"]
                .as_str()
                .unwrap()
                .starts_with("mobile-artwork://localhost/")));
    assert!(core
        .online_catalog("different-identity", "unavailable")
        .unwrap()["missing"]
        .as_bool()
        .unwrap());
    assert!(std::fs::read_dir(data.join("online"))
        .unwrap()
        .next()
        .is_none()); // No Keychain access or journal creation for display browsing.
    let printing = catalog.cards[0]["printingId"].as_str().unwrap().to_owned();
    drop(catalog);
    let deck = core
        .request(Request::SaveDeck {
            id: None,
            name: "移动离线探针".into(),
            entries: vec![Entry {
                printing_id: printing.clone(),
                quantity: 2,
            }],
            expected_version: None,
        })
        .unwrap();
    let deck_id = deck["id"].as_str().unwrap().to_owned();
    core.request(Request::SetHolding {
        printing_id: printing,
        quantity: 3,
        wishlist: true,
        expected_version: 0,
    })
    .unwrap();
    let frozen = core
        .request(Request::FreezeDeck {
            id: deck_id.clone(),
            expected_version: 1,
        })
        .unwrap();
    let update_tested = if let Some(bundle) = std::env::args_os().nth(2) {
        let bytes = std::fs::read(bundle).unwrap();
        let staged = core
            .stage_catalog(&bytes, &ptcg_mobile_lib::catalog::hash(&bytes))
            .unwrap();
        core.request(Request::ApplyCatalog {
            package: staged["package"].as_str().unwrap().into(),
            base: staged["base"].as_str().unwrap().into(),
        })
        .unwrap();
        true
    } else {
        false
    };
    drop(core);
    let reopened = Core::open(&resources, &data, &cache).unwrap();
    assert_eq!(reopened.request(Request::Decks {}).unwrap()[0], deck);
    assert_eq!(
        reopened.request(Request::Collection {}).unwrap()["entries"][0]["quantity"],
        3
    );
    assert_eq!(
        reopened.request(Request::Revisions { deck_id }).unwrap()["revisions"][0]["contentHash"],
        frozen["contentHash"]
    );
    println!(
        "{}",
        json!({"status":"passed","displayCards":count,"onlineDisplayEffects":display_count,"onlineDisplayIdentityChecked":true,"sqliteReopen":true,"frozenContentVerified":true,"displayUpdateInstalledAndReopened":update_tested,
        "networkRequests":0,"userProfileAccessed":false,"ruleExecution":false,"iOSDeviceExecution":false})
    );
}
