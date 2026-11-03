use ptcg_mobile_lib::online::{
    client::Client,
    error::{Error, Result},
    protocol,
    transport::{Stream, Transport},
    vault::{Journal, State},
};
use serde_json::{json, Value};
use std::{
    collections::BTreeMap,
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc, Mutex,
    },
};
const MID: &str = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";

fn wire(kind: &str) -> Value {
    let rows: Vec<Value> =
        serde_json::from_str(include_str!("../../../../tests/synthetic/protocol/v2.json")).unwrap();
    rows.into_iter().find(|r| r["kind"] == kind).unwrap()["value"].clone()
}
fn tokens() -> Value {
    json!({"protocolVersion":"2.0","serverId":"test","userId":"1".repeat(32),"deviceId":"2".repeat(32),"sessionId":"3".repeat(32),"tokenType":"Bearer","accessToken":format!("atk_{}","a".repeat(43)),"refreshToken":format!("rtk_{}","r".repeat(43)),"accessExpiresAt":"2099-01-01T00:00:00Z","refreshExpiresAt":"2099-02-01T00:00:00Z"})
}
fn view() -> Value {
    let mut v = wire("MatchSnapshot");
    v["matchId"] = MID.into();
    v["streamCursor"] = "0".repeat(32).into();
    v["controllerEpoch"] = 0.into();
    v
}
#[derive(Clone)]
struct Memory {
    state: Arc<Mutex<State>>,
    fail: Arc<AtomicBool>,
}
impl Memory {
    fn new(auth: bool) -> Self {
        Self {
            state: Arc::new(Mutex::new(State {
                credentials: if auth { Some(tokens()) } else { None },
                ..State::default()
            })),
            fail: Arc::new(AtomicBool::new(false)),
        }
    }
}
struct JournalOwner {
    mirror: State,
    memory: Memory,
}
impl JournalOwner {
    fn open(memory: Memory) -> Self {
        let mirror = memory.state.lock().unwrap().clone();
        Self { mirror, memory }
    }
}
impl Journal for JournalOwner {
    fn state(&self) -> &State {
        &self.mirror
    }
    fn save(&mut self, next: State) -> Result<()> {
        if self.memory.fail.swap(false, Ordering::AcqRel) {
            return Err(Error::new("JOURNAL_UNAVAILABLE"));
        }
        *self.memory.state.lock().unwrap() = next.clone();
        self.mirror = next;
        Ok(())
    }
}
#[derive(Clone)]
struct Call {
    method: String,
    path: String,
    body: Option<Value>,
    controller: Option<String>,
}
struct ApiState {
    view: Value,
    calls: Vec<Call>,
    receipts: BTreeMap<String, Value>,
    controls: BTreeMap<String, Value>,
    drop_command: bool,
    drop_before: bool,
    drop_control: bool,
    drop_refresh: bool,
    drop_guest: bool,
    wrong_refresh: bool,
    wrong_receipt: bool,
    wrong_control: bool,
    fail_ack: Option<Arc<AtomicBool>>,
}
struct Api(Mutex<ApiState>);
impl Api {
    fn new() -> Arc<Self> {
        Arc::new(Self(Mutex::new(ApiState {
            view: view(),
            calls: vec![],
            receipts: BTreeMap::new(),
            controls: BTreeMap::new(),
            drop_command: false,
            drop_before: false,
            drop_control: false,
            drop_refresh: false,
            drop_guest: false,
            wrong_refresh: false,
            wrong_receipt: false,
            wrong_control: false,
            fail_ack: None,
        })))
    }
    fn writes(&self, suffix: &str) -> Vec<Call> {
        self.0
            .lock()
            .unwrap()
            .calls
            .iter()
            .filter(|r| r.method == "POST" && r.path.ends_with(suffix))
            .cloned()
            .collect()
    }
}
impl Transport for Api {
    fn request(
        &self,
        method: &str,
        path: &str,
        body: Option<&Value>,
        _: Option<&str>,
        controller: Option<&str>,
    ) -> Result<Value> {
        let mut a = self.0.lock().unwrap();
        a.calls.push(Call {
            method: method.into(),
            path: path.into(),
            body: body.cloned(),
            controller: controller.map(String::from),
        });
        let body = body.cloned().unwrap_or(Value::Null);
        if path == "/capabilities" {
            return Ok(wire("Capabilities"));
        }
        if path == "/sessions" {
            return if a.drop_guest {
                Err(Error::network())
            } else {
                Ok(tokens())
            };
        }
        if path == "/sessions/refresh" {
            if a.drop_refresh {
                return Err(Error::network());
            }
            let mut t = tokens();
            t["accessToken"] = format!("atk_{}", "b".repeat(43)).into();
            if a.wrong_refresh {
                t["userId"] = "f".repeat(32).into();
            }
            return Ok(t);
        }
        if path.ends_with("/control") {
            if a.wrong_control {
                let mut other = a.view.clone();
                other["matchId"] = "f".repeat(32).into();
                return Ok(other);
            }
            let request = body["requestId"].as_str().unwrap();
            if !a.controls.contains_key(request) {
                a.view["controllerEpoch"] =
                    (body["expectedControllerEpoch"].as_u64().unwrap() + 1).into();
                let v = a.view.clone();
                a.controls.insert(request.into(), v);
            }
            return if a.drop_control {
                Err(Error::network())
            } else {
                Ok(a.controls[request].clone())
            };
        }
        if path.contains("/receipts/") {
            let id = path.rsplit('/').next().unwrap();
            let mut receipt = a.receipts.get(id).cloned().ok_or(Error {
                code: "RECEIPT_NOT_FOUND".into(),
                status: 404,
                retryable: false,
            })?;
            if a.wrong_receipt {
                receipt["commandId"] = "another-command".into();
            }
            return Ok(receipt);
        }
        if path.ends_with("/commands") || path.ends_with("/resign") {
            if a.drop_before {
                a.drop_before = false;
                return Err(Error::network());
            }
            let id = body["commandId"].as_str().unwrap();
            if !a.receipts.contains_key(id) {
                a.view["matchSeq"] = (a.view["matchSeq"].as_u64().unwrap() + 1).into();
                let receipt = json!({"protocolVersion":"2.0","matchId":MID,"commandId":id,"accepted":true,"matchSeq":a.view["matchSeq"],"stateVersion":a.view["stateVersion"],"streamCursor":"1".repeat(32)});
                a.receipts.insert(id.into(), receipt);
            }
            if let Some(flag) = a.fail_ack.take() {
                flag.store(true, Ordering::Release);
            }
            if a.drop_command {
                a.drop_command = false;
                return Err(Error::network());
            }
            return Ok(a.receipts[id].clone());
        }
        if path.ends_with("/frames") {
            let mut page = wire("ReplayPage");
            page["matchId"] = MID.into();
            return Ok(page);
        }
        if path == format!("/matches/{MID}") {
            return Ok(a.view.clone());
        }
        if path == "/decks" {
            return Ok(
                json!({"protocolVersion":"2.0","deckRevision":"b".repeat(32),"ruleReleaseId":body["ruleReleaseId"],"formatAsOf":"2026-10-31"}),
            );
        }
        Err(Error::new("UNEXPECTED_TEST_REQUEST"))
    }
    fn stream(&self, _: &str, _: &Value) -> Result<Box<dyn Stream>> {
        Err(Error::network())
    }
}
fn owner(api: Arc<Api>, memory: &Memory) -> Client {
    Client::new(api, Box::new(JournalOwner::open(memory.clone()))).unwrap()
}
#[test]
fn a_control_response_for_another_match_keeps_the_durable_request() {
    let api = Api::new();
    api.0.lock().unwrap().wrong_control = true;
    let memory = Memory::new(true);
    let mut client = owner(api.clone(), &memory);
    assert_eq!(
        client.control(MID, 0).unwrap_err().code,
        "IDENTITY_MISMATCH"
    );
    let original = client.state().control_pending[MID].clone();
    drop(client);
    api.0.lock().unwrap().wrong_control = false;
    let mut client = owner(api.clone(), &memory);
    client.control(MID, 0).unwrap();
    assert!(client.state().control_pending.is_empty());
    let calls = api.writes("/control");
    assert_eq!(calls[0].body, Some(original.clone()));
    assert_eq!(calls[1].body, Some(original));
}
fn lease(client: &mut Client) -> String {
    client.control(MID, 0).unwrap()["lease"]
        .as_str()
        .unwrap()
        .into()
}
fn command() -> Value {
    wire("Command")
}

#[test]
fn unknown_command_reopens_and_reads_receipt_without_another_write() {
    let api = Api::new();
    let memory = Memory::new(true);
    let mut client = owner(api.clone(), &memory);
    let lease = lease(&mut client);
    api.0.lock().unwrap().drop_command = true;
    assert!(client.submit(MID, &lease, command(), false).is_err());
    assert!(client.state().pending.contains_key(MID));
    drop(client);
    let mut reopened = owner(api.clone(), &memory);
    assert!(reopened.recover(MID).unwrap().is_some());
    assert!(reopened.state().pending.is_empty());
    assert_eq!(api.writes("/commands").len(), 1);
}
#[test]
fn absent_receipt_retries_the_original_body_and_controller() {
    let api = Api::new();
    let memory = Memory::new(true);
    let mut client = owner(api.clone(), &memory);
    let lease = lease(&mut client);
    api.0.lock().unwrap().drop_before = true;
    assert!(client.submit(MID, &lease, command(), false).is_err());
    drop(client);
    let mut reopened = owner(api.clone(), &memory);
    reopened.recover(MID).unwrap();
    let writes = api.writes("/commands");
    assert_eq!(writes.len(), 2);
    assert_eq!(writes[0].body, writes[1].body);
    assert_eq!(writes[0].controller, writes[1].controller);
}
#[test]
fn unknown_operation_blocks_a_different_command_and_a_new_control() {
    let api = Api::new();
    let memory = Memory::new(true);
    let mut client = owner(api.clone(), &memory);
    let lease = lease(&mut client);
    api.0.lock().unwrap().drop_before = true;
    client.submit(MID, &lease, command(), false).unwrap_err();
    let mut different = command();
    different["commandId"] = "different-id".into();
    assert_eq!(
        client
            .submit(MID, &lease, different, false)
            .unwrap_err()
            .code,
        "PENDING_COMMAND"
    );
    assert_eq!(client.control(MID, 1).unwrap_err().code, "PENDING_COMMAND");
}
#[test]
fn persistence_failure_prevents_sending_the_command() {
    let api = Api::new();
    let memory = Memory::new(true);
    let mut client = owner(api.clone(), &memory);
    let lease = lease(&mut client);
    memory.fail.store(true, Ordering::Release);
    assert_eq!(
        client
            .submit(MID, &lease, command(), false)
            .unwrap_err()
            .code,
        "JOURNAL_UNAVAILABLE"
    );
    assert!(api.writes("/commands").is_empty());
}
#[test]
fn failed_ack_persistence_keeps_the_original_pending_record() {
    let api = Api::new();
    let memory = Memory::new(true);
    let mut client = owner(api.clone(), &memory);
    let lease = lease(&mut client);
    api.0.lock().unwrap().fail_ack = Some(memory.fail.clone());
    assert_eq!(
        client
            .submit(MID, &lease, command(), false)
            .unwrap_err()
            .code,
        "JOURNAL_UNAVAILABLE"
    );
    drop(client);
    let mut reopened = owner(api.clone(), &memory);
    assert!(reopened.state().pending.contains_key(MID));
    reopened.recover(MID).unwrap();
    assert_eq!(api.writes("/commands").len(), 1);
}
#[test]
fn mismatched_receipt_cannot_clear_a_pending_operation() {
    let api = Api::new();
    let memory = Memory::new(true);
    let mut client = owner(api.clone(), &memory);
    let lease = lease(&mut client);
    api.0.lock().unwrap().drop_command = true;
    client.submit(MID, &lease, command(), false).unwrap_err();
    api.0.lock().unwrap().wrong_receipt = true;
    assert_eq!(client.recover(MID).unwrap_err().code, "INVALID_RESPONSE");
    assert!(client.state().pending.contains_key(MID));
}
#[test]
fn unknown_control_reuses_its_durable_request_and_controller_after_reopen() {
    let api = Api::new();
    let memory = Memory::new(true);
    api.0.lock().unwrap().drop_control = true;
    let mut client = owner(api.clone(), &memory);
    client.control(MID, 0).unwrap_err();
    drop(client);
    api.0.lock().unwrap().drop_control = false;
    let mut reopened = owner(api.clone(), &memory);
    reopened.control(MID, 0).unwrap();
    let writes = api.writes("/control");
    assert_eq!(writes.len(), 4);
    assert!(writes.iter().all(|r| r.body == writes[0].body));
    assert!(reopened.state().control_pending.is_empty());
}
#[test]
fn refresh_unknown_is_never_reused_or_replaced_with_a_guest() {
    let api = Api::new();
    let memory = Memory::new(true);
    memory.state.lock().unwrap().credentials.as_mut().unwrap()["accessExpiresAt"] =
        "2020-01-01T00:00:00Z".into();
    api.0.lock().unwrap().drop_refresh = true;
    let mut client = owner(api.clone(), &memory);
    assert_eq!(
        client.access(None).unwrap_err().code,
        "REAUTHENTICATION_REQUIRED"
    );
    drop(client);
    let mut reopened = owner(api.clone(), &memory);
    assert_eq!(
        reopened.access(None).unwrap_err().code,
        "REAUTHENTICATION_REQUIRED"
    );
    assert!(reopened.guest("new", "ios").is_err());
    assert_eq!(api.writes("/sessions/refresh").len(), 1);
    assert!(api.writes("/sessions").is_empty());
}
#[test]
fn rotated_tokens_must_preserve_the_entire_identity() {
    let api = Api::new();
    let memory = Memory::new(true);
    api.0.lock().unwrap().wrong_refresh = true;
    let mut client = owner(api, &memory);
    assert_eq!(
        client
            .access(Some(tokens()["accessToken"].as_str().unwrap()))
            .unwrap_err()
            .code,
        "IDENTITY_MISMATCH"
    );
    assert!(client.state().refresh_pending);
    assert_eq!(
        client.state().credentials.as_ref().unwrap()["userId"],
        "1".repeat(32)
    );
}
#[test]
fn lost_guest_response_does_not_create_another_guest() {
    let api = Api::new();
    let memory = Memory::new(false);
    api.0.lock().unwrap().drop_guest = true;
    let mut client = owner(api.clone(), &memory);
    client.guest("测试", "ios").unwrap_err();
    drop(client);
    let mut reopened = owner(api.clone(), &memory);
    assert_eq!(
        reopened.guest("测试", "ios").unwrap_err().code,
        "IDENTITY_CREATION_UNKNOWN"
    );
    assert_eq!(api.writes("/sessions").len(), 1);
}
#[test]
fn private_snapshots_are_member_bound_and_monotonic() {
    let api = Api::new();
    let memory = Memory::new(true);
    let mut client = owner(api, &memory);
    client.accept_snapshot(MID, view()).unwrap();
    let mut wrong = view();
    wrong["viewerSeat"] = "PLAYER2".into();
    wrong["decision"] = Value::Null;
    assert_eq!(
        client.accept_snapshot(MID, wrong).unwrap_err().code,
        "IDENTITY_MISMATCH"
    );
    let mut old = view();
    old["matchSeq"] = 2.into();
    assert_eq!(
        client.accept_snapshot(MID, old).unwrap_err().code,
        "STALE_STREAM"
    );
    let mut foreign = view();
    foreign["matchId"] = "b".repeat(32).into();
    assert_eq!(
        client.accept_snapshot(MID, foreign).unwrap_err().code,
        "IDENTITY_MISMATCH"
    );
}
#[test]
fn GUI_status_never_contains_tokens_controller_ids_or_private_hands() {
    let api = Api::new();
    let memory = Memory::new(true);
    let mut client = owner(api, &memory);
    let _ = lease(&mut client);
    let status = client.status().to_string();
    for secret in [
        "atk_",
        "rtk_",
        "controllers",
        "handCount",
        "accessExpiresAt",
    ] {
        assert!(!status.contains(secret));
    }
    assert!(status.contains("userId"));
}
#[test]
fn schema_decoder_rejects_ambiguity_nonintegers_and_hidden_information() {
    assert!(protocol::unpack(br#"{"protocolVersion":"2.0","protocolVersion":"1.0"}"#).is_err());
    assert!(protocol::unpack(b"{\"matchSeq\":1.0}").is_err());
    let mut v = view();
    v["observation"]["opponent"]["hand"] = json!([{"cardId":"secret","name":"hidden"}]);
    assert!(protocol::checked("MatchSnapshot", v).is_err());
    let mut v = view();
    v["observation"]["self"]["handCount"] = 2.into();
    assert!(protocol::checked("MatchSnapshot", v).is_err());
    let rows: Vec<Value> =
        serde_json::from_str(include_str!("../../../../tests/synthetic/protocol/v2.json")).unwrap();
    for row in rows {
        protocol::checked(row["kind"].as_str().unwrap(), row["value"].clone()).unwrap();
    }
}
#[test]
fn a_lobby_request_is_persisted_and_reused_without_resubmission() {
    let api = Api::new();
    let memory = Memory::new(true);
    let mut client = owner(api.clone(), &memory);
    let body = wire("DeckSubmission");
    let first = client
        .lobby(
            "DeckSubmission",
            "/decks",
            body.clone(),
            "DeckAccepted",
            Some("local-revision"),
        )
        .unwrap();
    drop(client);
    let mut reopened = owner(api.clone(), &memory);
    let second = reopened
        .lobby(
            "DeckSubmission",
            "/decks",
            body,
            "DeckAccepted",
            Some("local-revision"),
        )
        .unwrap();
    assert_eq!(first, second);
    assert_eq!(api.writes("/decks").len(), 1);
}
