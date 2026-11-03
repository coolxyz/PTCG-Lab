//! Opt-in host-native real-rule probe. Never an iOS UI or Keychain result.
use ptcg_mobile_lib::{
    catalog::{id, Entry},
    online::{
        client::Client,
        error::{Error, Result},
        protocol::checked,
        transport::{Http, Server, Stream, Transport},
        vault::{Keys, Vault},
    },
};
use serde::Deserialize;
use serde_json::{json, Value};
use std::{
    path::Path,
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc,
    },
    thread::sleep,
    time::{Duration, Instant},
};
use zeroize::Zeroizing;

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Config {
    acceptance: bool,
    server: Server,
    rule_release_id: String,
    entries: Vec<Entry>,
}
struct TestKeys(Zeroizing<[u8; 32]>);
impl Keys for TestKeys {
    fn key(&self, _: &str, _: bool) -> Result<Zeroizing<[u8; 32]>> {
        Ok(Zeroizing::new(*self.0))
    }
}
struct LostResponse {
    inner: Http,
    armed: Arc<AtomicBool>,
}
impl Transport for LostResponse {
    fn request(
        &self,
        method: &str,
        path: &str,
        body: Option<&Value>,
        token: Option<&str>,
        controller: Option<&str>,
    ) -> Result<Value> {
        let response = self.inner.request(method, path, body, token, controller)?;
        if method == "POST"
            && path.ends_with("/commands")
            && self.armed.swap(false, Ordering::SeqCst)
        {
            return Err(Error::network());
        }
        Ok(response)
    }
    fn stream(&self, token: &str, subscription: &Value) -> Result<Box<dyn Stream>> {
        self.inner.stream(token, subscription)
    }
}
fn open(root: &Path, server: &Server, keys: &TestKeys, armed: &Arc<AtomicBool>) -> Client {
    Client::new(
        Arc::new(LostResponse {
            inner: Http::new(server.clone()).unwrap(),
            armed: armed.clone(),
        }),
        Box::new(Vault::open(root, &server.key(), keys).unwrap()),
    )
    .unwrap()
}
fn private(view: &Value, i: usize) {
    assert_eq!(view["viewerSeat"], format!("PLAYER{}", i + 1));
    assert!(view["observation"]["opponent"]["hand"].is_null());
    for key in ["seed", "recovery", "stateDigest"] {
        assert!(view.get(key).is_none());
    }
}
fn replay_frame(view: &Value) -> Value {
    let mut frame = view.as_object().unwrap().clone();
    for key in [
        "protocolVersion",
        "matchId",
        "ruleReleaseId",
        "viewerSeat",
        "controllerEpoch",
        "streamCursor",
        "decision",
    ] {
        frame.remove(key);
    }
    frame.insert("frameSeq".into(), view["matchSeq"].clone());
    Value::Object(frame)
}
fn stream(c: &mut Client, mid: &str, cursor: Value) {
    let body = checked(
        "MatchSubscribe",
        json!({"protocolVersion":"2.0","matchId":mid,"streamCursor":cursor}),
    )
    .unwrap();
    let token = c.access(None).unwrap();
    let mut ws = c.transport.stream(&token, &body).unwrap();
    let mut seen = false;
    for _ in 0..12 {
        if let Some(event) = ws.read().unwrap() {
            if let Some(view) = event.get("snapshot") {
                private(view, 0);
                seen = true;
                break;
            }
        }
    }
    ws.close();
    assert!(seen);
}
fn choice(view: &Value) -> Value {
    let d = &view["decision"];
    if d["kind"] == "selection" {
        let refs: Vec<_> = d["candidates"]
            .as_array()
            .unwrap()
            .iter()
            .take(d["min"].as_u64().unwrap() as usize)
            .map(|c| c["ref"].clone())
            .collect();
        json!({"selectedRefs":refs})
    } else {
        let options = d["options"].as_array().unwrap();
        let option = options
            .iter()
            .find(|o| o["actionType"] == "PassTurn")
            .unwrap_or(&options[0]);
        json!({"optionId":option["id"]})
    }
}
fn main() {
    let args: Vec<_> = std::env::args_os().skip(1).collect();
    assert_eq!(args.len(), 2, "Use CONFIG NEW_PRIVATE_OUTPUT");
    let config: Config = serde_json::from_slice(&std::fs::read(&args[0]).unwrap()).unwrap();
    let url = url::Url::parse(&config.server.origin).unwrap();
    assert!(
        config.acceptance
            && url.scheme() == "https"
            && url.host_str() == Some("127.0.0.1")
            && url.port().is_some_and(|p| p >= 20000)
    );
    assert!(!config.server.ca_pem.is_empty());
    config.server.validate().unwrap();
    assert_eq!(
        config
            .entries
            .iter()
            .map(|e| e.quantity as usize)
            .sum::<usize>(),
        60
    );
    let output = Path::new(&args[1]);
    assert!(
        output.is_absolute() && !output.exists() && !output.ancestors().any(|p| p.is_symlink())
    );
    std::fs::create_dir(output).unwrap();
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(output, std::fs::Permissions::from_mode(0o700)).unwrap();
    }
    // Random test-only key never touches the user's system Keychain.
    let mut key = Zeroizing::new([0; 32]);
    getrandom::fill(key.as_mut()).unwrap();
    let keys = TestKeys(key);
    let roots = [output.join("a"), output.join("b")];
    let armed = Arc::new(AtomicBool::new(false));
    let mut clients: Vec<_> = (0..2)
        .map(|i| open(&roots[i], &config.server, &keys, &armed))
        .collect();
    let mut identities = Vec::new();
    let mut revisions = Vec::new();
    for (i, c) in clients.iter_mut().enumerate() {
        assert!(c.capabilities().unwrap()["ruleReleaseIds"]
            .as_array()
            .unwrap()
            .contains(&json!(config.rule_release_id)));
        identities.push(
            c.guest(&format!("P8-F {}", i + 1), "host-native acceptance")
                .unwrap(),
        );
        let deck=c.lobby("DeckSubmission","/decks",json!({"protocolVersion":"2.0","requestId":id().unwrap(),"ruleReleaseId":config.rule_release_id,"entries":config.entries}),"DeckAccepted",None).unwrap();
        revisions.push(deck["deckRevision"].clone());
    }
    let room=clients[0].lobby("RoomCreate","/rooms",json!({"protocolVersion":"2.0","requestId":id().unwrap(),"ruleReleaseId":config.rule_release_id,"deckRevision":revisions[0]}),"RoomView",None).unwrap();
    let mut room=clients[1].lobby("RoomJoin","/rooms/join",json!({"protocolVersion":"2.0","requestId":id().unwrap(),"inviteCode":room["inviteCode"],"deckRevision":revisions[1]}),"RoomView",None).unwrap();
    let path = format!("/rooms/{}/ready", room["roomId"].as_str().unwrap());
    for c in &mut clients {
        room=c.lobby("RoomReady",&path,json!({"protocolVersion":"2.0","requestId":id().unwrap(),"expectedRoomVersion":room["roomVersion"],"ready":true}),"RoomView",None).unwrap();
    }
    let mid = room["matchId"].as_str().unwrap().to_owned();
    let until = Instant::now() + Duration::from_secs(40);
    while clients[0].snapshot(&mid).is_err() {
        assert!(Instant::now() < until);
        sleep(Duration::from_millis(200));
    }
    let mut leases: Vec<_> = clients
        .iter_mut()
        .map(|c| {
            c.control(&mid, 0).unwrap()["lease"]
                .as_str()
                .unwrap()
                .to_owned()
        })
        .collect();
    let initial = clients[0].snapshot(&mid).unwrap();
    stream(&mut clients[0], &mid, Value::Null);
    let mut counts = [0usize; 2];
    let mut reason = String::new();
    let mut last_seq = 0;
    let mut history: [Vec<Value>; 2] = [Vec::new(), Vec::new()];
    for step in 0..220 {
        // Respect normal peer admission limits; this is a functional probe, not load.
        sleep(Duration::from_secs(1));
        let views = [
            clients[0].snapshot(&mid).unwrap(),
            clients[1].snapshot(&mid).unwrap(),
        ];
        for i in 0..2 {
            private(&views[i], i);
            history[i].push(replay_frame(&views[i]));
        }
        assert_eq!(views[0]["matchSeq"], views[1]["matchSeq"]);
        if views[0]["done"] == true {
            assert_eq!(views[0]["winner"], views[1]["winner"]);
            reason = views[0]["result"]["reason"].as_str().unwrap().into();
            last_seq = views[0]["matchSeq"].as_u64().unwrap();
            break;
        }
        if step == 16 {
            stream(&mut clients[0], &mid, initial["streamCursor"].clone());
        }
        let actor = (0..2).find(|&i| !views[i]["decision"].is_null()).unwrap();
        let v = &views[actor];
        let command = json!({"protocolVersion":"2.0","commandId":id().unwrap(),"expectedMatchSeq":v["matchSeq"],"expectedStateVersion":v["stateVersion"],"controllerEpoch":v["controllerEpoch"],"decisionId":v["decision"]["id"],"choice":choice(v)});
        if step == 8 {
            armed.store(true, Ordering::SeqCst);
            assert!(clients[actor]
                .submit(&mid, &leases[actor], command.clone(), false)
                .is_err());
            assert_eq!(clients[actor].state().pending[&mid]["body"], command);
            drop(clients.remove(actor));
            clients.insert(actor, open(&roots[actor], &config.server, &keys, &armed));
            assert_eq!(clients[actor].status()["identity"], identities[actor]);
            let receipt = clients[actor].recover(&mid).unwrap().unwrap();
            assert_eq!(receipt["commandId"], command["commandId"]);
            assert_eq!(
                receipt["matchSeq"],
                command["expectedMatchSeq"].as_u64().unwrap() + 1
            );
            assert!(!clients[actor].state().pending.contains_key(&mid));
            let v = clients[actor].snapshot(&mid).unwrap();
            leases[actor] = clients[actor]
                .control(&mid, v["controllerEpoch"].as_u64().unwrap())
                .unwrap()["lease"]
                .as_str()
                .unwrap()
                .to_owned();
        } else {
            clients[actor]
                .submit(&mid, &leases[actor], command, false)
                .unwrap();
        }
        counts[actor] += 1;
        if step % 25 == 0 {
            eprintln!(
                "Accepted {} real-rule commands",
                counts.iter().sum::<usize>()
            );
        }
    }
    assert!(
        !reason.is_empty()
            && !["unknown", "resigned", "technical_abort", "truncated"].contains(&reason.as_str())
    );
    assert!(counts.iter().all(|&n| n > 0));
    let mut frame_counts = [0usize; 2];
    for i in 0..2 {
        let mut cursor = None;
        let mut last = 0;
        loop {
            let page = clients[i].replay(&mid, cursor.as_deref()).unwrap();
            assert_eq!(page["viewerSeat"], format!("PLAYER{}", i + 1));
            for frame in page["frames"].as_array().unwrap() {
                // Replay identity belongs to its page; frames intentionally omit
                // viewerSeat and all actionable/control fields. Compare with
                // that member's complete live projection, not a weakened view.
                assert_eq!(frame, &history[i][frame_counts[i]]);
                last = frame["matchSeq"].as_u64().unwrap();
                frame_counts[i] += 1;
            }
            cursor = page["nextCursor"].as_str().map(str::to_owned);
            if cursor.is_none() {
                break;
            }
        }
        assert_eq!(last, last_seq);
        assert_eq!(frame_counts[i], history[i].len());
    }
    let report = json!({"status":"passed","actualRuleEngine":true,"transport":"HTTPS/WSS","commandsBySeat":counts,"lastMatchSeq":last_seq,"naturalReason":reason,"privateReplayFrames":frame_counts,"encryptedVaultReopenedWithinProcess":true,"committedCommandResponseLostAndRecoveredByOriginalId":true,"wssReconnected":true,"iOSExecution":false,"realProcessTermination":false,"guiExecution":false,"keychainTested":false});
    std::fs::write(
        output.join("REPORT.json"),
        serde_json::to_vec_pretty(&report).unwrap(),
    )
    .unwrap();
    println!("{report}");
}
