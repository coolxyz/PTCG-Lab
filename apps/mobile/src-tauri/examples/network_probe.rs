//! Disposable TLS/WSS contract probe against an explicitly supplied synthetic peer.
use ptcg_mobile_lib::{
    catalog::id,
    online::{
        client::Client,
        error::Result,
        protocol::checked,
        transport::{Http, Server},
        vault::{Keys, Vault},
    },
};
use serde_json::{json, Value};
use std::{path::PathBuf, sync::Arc};
use zeroize::Zeroizing;
struct TestKeys;
impl Keys for TestKeys {
    fn key(&self, _: &str, _: bool) -> Result<Zeroizing<[u8; 32]>> {
        Ok(Zeroizing::new([9; 32]))
    }
}
struct Scratch(PathBuf);
impl Drop for Scratch {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}
fn client(root: &PathBuf, server: &Server) -> Client {
    Client::new(
        Arc::new(Http::new(server.clone()).unwrap()),
        Box::new(Vault::open(root, &server.key(), &TestKeys).unwrap()),
    )
    .unwrap()
}
fn main() {
    let path = PathBuf::from(std::env::args_os().nth(1).expect("synthetic peer config"));
    let server: Server = serde_json::from_slice(&std::fs::read(path).unwrap()).unwrap();
    let mode = std::env::args().nth(2).unwrap_or("duel".into());
    let scratch = Scratch(
        std::env::temp_dir()
            .canonicalize()
            .unwrap()
            .join(format!("ptcg-network-probe-{}", id().unwrap())),
    );
    std::fs::create_dir(&scratch.0).unwrap();
    if mode != "duel" {
        let mut c = client(&scratch.0.join("negative"), &server);
        assert!(c.capabilities().is_err());
        println!("{{\"status\":\"passed\",\"negativeTransportCheck\":true}}");
        return;
    }
    let mut a = client(&scratch.0.join("a"), &server);
    let mut b = client(&scratch.0.join("b"), &server);
    let alpha = a.guest("甲", "合成 native 客户端").unwrap();
    let beta = b.guest("乙", "合成 native 客户端").unwrap();
    assert_ne!(alpha["userId"], beta["userId"]);
    let rule = a.capabilities().unwrap()["ruleReleaseIds"][0].clone();
    let deck = |request: String| json!({"protocolVersion":"2.0","requestId":request,"ruleReleaseId":rule,"entries":[{"printingId":"synthetic-print-1","quantity":60}]});
    let da = a
        .lobby(
            "DeckSubmission",
            "/decks",
            deck(id().unwrap()),
            "DeckAccepted",
            None,
        )
        .unwrap();
    let db = b
        .lobby(
            "DeckSubmission",
            "/decks",
            deck(id().unwrap()),
            "DeckAccepted",
            None,
        )
        .unwrap();
    let room=a.lobby("RoomCreate","/rooms",json!({"protocolVersion":"2.0","requestId":id().unwrap(),"ruleReleaseId":rule,"deckRevision":da["deckRevision"]}),"RoomView",None).unwrap();
    let joined=b.lobby("RoomJoin","/rooms/join",json!({"protocolVersion":"2.0","requestId":id().unwrap(),"inviteCode":room["inviteCode"],"deckRevision":db["deckRevision"]}),"RoomView",None).unwrap();
    let room_id = room["roomId"].as_str().unwrap();
    let ready = |version: &Value| json!({"protocolVersion":"2.0","requestId":id().unwrap(),"expectedRoomVersion":version,"ready":true});
    let ar = a
        .lobby(
            "RoomReady",
            &format!("/rooms/{room_id}/ready"),
            ready(&joined["roomVersion"]),
            "RoomView",
            None,
        )
        .unwrap();
    let br = b
        .lobby(
            "RoomReady",
            &format!("/rooms/{room_id}/ready"),
            ready(&ar["roomVersion"]),
            "RoomView",
            None,
        )
        .unwrap();
    let mid = br["matchId"].as_str().unwrap();
    let lease = a.control(mid, 0).unwrap()["lease"]
        .as_str()
        .unwrap()
        .to_owned();
    let initial = a.snapshot(mid).unwrap();
    let subscription = checked(
        "MatchSubscribe",
        json!({"protocolVersion":"2.0","matchId":mid,"streamCursor":initial["streamCursor"]}),
    )
    .unwrap();
    let token = a.access(None).unwrap();
    let mut stream = a.transport.stream(&token, &subscription).unwrap();
    let mut received = false;
    for _ in 0..10 {
        if let Some(event) = stream.read().unwrap() {
            if event["type"] == "snapshot" {
                a.accept_snapshot(mid, event["snapshot"].clone()).unwrap();
                received = true;
                break;
            }
        }
    }
    stream.close();
    assert!(received);
    let command = json!({"protocolVersion":"2.0","commandId":"native-original-command","expectedMatchSeq":initial["matchSeq"],"expectedStateVersion":initial["stateVersion"],"controllerEpoch":initial["controllerEpoch"],"decisionId":initial["decision"]["id"],"choice":{"optionId":initial["decision"]["options"][0]["id"]}});
    assert!(a.submit(mid, &lease, command, false).is_err());
    assert!(a.state().pending.contains_key(mid));
    drop(a);
    let mut a = client(&scratch.0.join("a"), &server);
    a.recover(mid).unwrap();
    let current = a.snapshot(mid).unwrap();
    let lease = a
        .control(mid, current["controllerEpoch"].as_u64().unwrap())
        .unwrap()["lease"]
        .as_str()
        .unwrap()
        .to_owned();
    let current = a.snapshot(mid).unwrap();
    a.submit(mid,&lease,json!({"protocolVersion":"2.0","commandId":"native-resign-command","expectedMatchSeq":current["matchSeq"],"expectedStateVersion":current["stateVersion"],"controllerEpoch":current["controllerEpoch"]}),true).unwrap();
    let ap = a.replay(mid, None).unwrap();
    let bp = b.replay(mid, None).unwrap();
    assert_eq!(ap["viewerSeat"], "PLAYER1");
    assert_eq!(bp["viewerSeat"], "PLAYER2");
    assert_ne!(
        ap["frames"][0]["observation"]["self"]["hand"],
        bp["frames"][0]["observation"]["self"]["hand"]
    );
    assert_eq!(a.status()["identity"], alpha);
    assert_eq!(b.status()["identity"], beta);
    println!(
        "{}",
        json!({"status":"passed","https":true,"wss":true,"twoNativeClients":true,"inviteAndReady":true,"unknownCommandRecoveredAfterReopen":true,"memberReplaySeparation":true,"actualRuleEngine":false,"iOSDeviceExecution":false})
    );
}
