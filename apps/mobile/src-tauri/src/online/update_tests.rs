use super::*;
use crate::{
    catalog::{hash, id},
    Request as Product,
};
use vault::{Journal, State};
struct Memory(State);
impl Journal for Memory {
    fn state(&self) -> &State {
        &self.0
    }
    fn save(&mut self, next: State) -> Result<()> {
        self.0 = next;
        Ok(())
    }
}
struct Offline;
impl transport::Transport for Offline {
    fn request(
        &self,
        _: &str,
        _: &str,
        _: Option<&Value>,
        _: Option<&str>,
        _: Option<&str>,
    ) -> Result<Value> {
        panic!("Metadata activation must not contact a battle server")
    }
    fn stream(&self, _: &str, _: &Value) -> Result<Box<dyn transport::Stream>> {
        panic!("Metadata activation must not create a stream")
    }
}
struct Scratch(std::path::PathBuf);
impl Drop for Scratch {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}
fn resources(path: &std::path::Path, version: &str) -> Vec<u8> {
    std::fs::create_dir_all(path).unwrap();
    let catalog=json!({"version":version,"displayCatalogVersion":hash(version.as_bytes()),"cards":[{"printingId":"P1","engineId":"E1","cnName":"合成卡牌","hasArtwork":false}]}).to_string();
    let artwork =
        json!({"repository":"https://github.com/example/Cards","commit":"a".repeat(40),"cards":{}})
            .to_string();
    let manifest=json!({"schema":1,"catalogVersion":version,"files":{"catalog.json":hash(catalog.as_bytes()),"artwork.json":hash(artwork.as_bytes())}}).to_string();
    for (name, text) in [
        ("catalog.json", &catalog),
        ("artwork.json", &artwork),
        ("manifest.json", &manifest),
    ] {
        std::fs::write(path.join(name), text).unwrap();
    }
    serde_json::to_vec(&json!({"schema":1,"kind":"ptcg-mobile-display","catalog":catalog,"artwork":artwork,"manifest":manifest})).unwrap()
}
#[test]
fn pending_network_operations_block_real_metadata_activation_without_mutating_the_journal() {
    let temp = Scratch(
        std::env::temp_dir()
            .canonicalize()
            .unwrap()
            .join(format!("ptcg-update-pending-{}", id().unwrap())),
    );
    std::fs::create_dir(&temp.0).unwrap();
    resources(&temp.0.join("builtin"), "v1");
    let core = Core::open(
        &temp.0.join("builtin"),
        &temp.0.join("data"),
        &temp.0.join("cache"),
    )
    .unwrap();
    let bytes = resources(&temp.0.join("next"), "v2");
    let preview = core.stage_catalog(&bytes, &hash(&bytes)).unwrap();
    let mut command = State::default();
    let corpus: Vec<Value> = serde_json::from_str(include_str!(
        "../../../../../tests/synthetic/protocol/v2.json"
    ))
    .unwrap();
    let body = corpus.into_iter().find(|r| r["kind"] == "Command").unwrap()["value"].clone();
    command.pending.insert(
        "a".repeat(32),
        json!({"body":body,"controller":"c".repeat(32),"action":"commands"}),
    );
    let mut control = State::default();
    control.controllers.insert("a".repeat(32), "c".repeat(32));
    control.control_pending.insert("a".repeat(32),json!({"protocolVersion":"2.0","requestId":"r".repeat(32),"controllerId":"c".repeat(32),"expectedControllerEpoch":0}));
    let mut lobby = State::default();
    lobby.lobby.insert(
        "original-request".into(),
        json!({"body":{"requestId":"original-request"}}),
    );
    for state in [
        State {
            guest_pending: true,
            ..State::default()
        },
        State {
            refresh_pending: true,
            ..State::default()
        },
        command,
        control,
        lobby,
    ] {
        let before = serde_json::to_value(&state).unwrap();
        let client = Arc::new(Mutex::new(
            Client::new(Arc::new(Offline), Box::new(Memory(state))).unwrap(),
        ));
        core.online.lock().unwrap().client = Some(client.clone());
        let result = core.request(Product::ApplyCatalog {
            package: preview["package"].as_str().unwrap().into(),
            base: preview["base"].as_str().unwrap().into(),
        });
        assert_eq!(result.unwrap_err().code, "UPDATE_BLOCKED_ONLINE");
        assert_eq!(core.catalog.read().unwrap().version, "v1");
        assert_eq!(
            serde_json::to_value(client.lock().unwrap().state()).unwrap(),
            before
        );
    }
    core.online.lock().unwrap().client = Some(Arc::new(Mutex::new(
        Client::new(Arc::new(Offline), Box::new(Memory(State::default()))).unwrap(),
    )));
    core.request(Product::ApplyCatalog {
        package: preview["package"].as_str().unwrap().into(),
        base: preview["base"].as_str().unwrap().into(),
    })
    .unwrap();
    assert_eq!(core.catalog.read().unwrap().version, "v2");
}
