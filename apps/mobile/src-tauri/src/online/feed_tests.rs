//! Exercise reconnect and retirement ordering with a deterministic, private protocol peer.
use super::*;
use crate::online::{
    transport::{Stream, Transport},
    vault::{Journal, State},
};
use std::{collections::VecDeque, sync::Condvar};

const MID: &str = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
fn snapshot() -> Value {
    let rows: Vec<Value> = serde_json::from_str(include_str!(
        "../../../../../tests/synthetic/protocol/v2.json"
    ))
    .unwrap();
    let mut v = rows
        .into_iter()
        .find(|r| r["kind"] == "MatchSnapshot")
        .unwrap()["value"]
        .clone();
    v["matchId"] = MID.into();
    v["streamCursor"] = "0".repeat(32).into();
    v
}
struct Memory(State);
impl Journal for Memory {
    fn state(&self) -> &State {
        &self.0
    }
    fn save(&mut self, state: State) -> Result<()> {
        self.0 = state;
        Ok(())
    }
}
struct Peer {
    attempts: Mutex<Vec<Value>>,
    reads: Mutex<VecDeque<Result<Option<Value>>>>,
    active: Arc<AtomicBool>,
    gate: Option<Arc<(Mutex<(bool, bool)>, Condvar)>>,
    closed: AtomicBool,
}
struct Reader(Arc<Peer>);
impl Stream for Reader {
    fn read(&mut self) -> Result<Option<Value>> {
        if let Some(gate) = &self.0.gate {
            let (lock, wake) = &**gate;
            let mut flags = lock.lock().unwrap();
            flags.0 = true;
            wake.notify_all();
            while !flags.1 {
                flags = wake.wait(flags).unwrap();
            }
        }
        match self.0.reads.lock().unwrap().pop_front() {
            Some(v) => v,
            None => {
                self.0.active.store(false, Ordering::Release);
                Ok(None)
            }
        }
    }
    fn close(&mut self) {
        self.0.closed.store(true, Ordering::Release);
    }
}
// An Arc wrapper lets the stream share the same synthetic peer without production hooks.
impl Transport for Arc<Peer> {
    fn request(
        &self,
        method: &str,
        path: &str,
        _: Option<&Value>,
        _: Option<&str>,
        _: Option<&str>,
    ) -> Result<Value> {
        assert_eq!(method, "GET");
        assert_eq!(path, format!("/matches/{MID}"));
        Ok(snapshot())
    }
    fn stream(&self, _: &str, subscription: &Value) -> Result<Box<dyn Stream>> {
        self.attempts.lock().unwrap().push(subscription.clone());
        Ok(Box::new(Reader(self.clone())))
    }
}
fn peer(
    reads: Vec<Result<Option<Value>>>,
    gate: Option<Arc<(Mutex<(bool, bool)>, Condvar)>>,
) -> Arc<Peer> {
    Arc::new(Peer {
        attempts: Mutex::new(vec![]),
        reads: Mutex::new(reads.into()),
        active: Arc::new(AtomicBool::new(true)),
        gate,
        closed: AtomicBool::new(false),
    })
}
#[test]
fn reconnect_uses_only_a_persisted_cursor_and_resync_removes_it() {
    let mut next = snapshot();
    next["matchSeq"] = (next["matchSeq"].as_u64().unwrap() + 1).into();
    next["streamCursor"] = "1".repeat(32).into();
    let api = peer(
        vec![
            Ok(Some(
                json!({"protocolVersion":"2.0","type":"update","snapshot":next}),
            )),
            Err(Error::network()),
            Ok(Some(
                json!({"protocolVersion":"2.0","code":"RESYNC_REQUIRED","retryable":false}),
            )),
        ],
        None,
    );
    let c = make_client(api.clone());
    let result = Mutex::new(json!({"revision":0,"event":null}));
    pump(
        c.clone(),
        MID,
        &AtomicBool::new(false),
        &api.active,
        &AtomicU64::new(0),
        0,
        &result,
    )
    .unwrap();
    let attempts = api.attempts.lock().unwrap();
    assert_eq!(attempts.len(), 3);
    assert_eq!(attempts[0]["streamCursor"], "0".repeat(32));
    assert_eq!(attempts[1]["streamCursor"], "1".repeat(32));
    assert!(attempts[2]["streamCursor"].is_null());
    assert_eq!(
        c.lock().unwrap().state().snapshots[MID]["streamCursor"],
        "1".repeat(32)
    );
    assert_eq!(result.lock().unwrap()["revision"], 2);
}
fn make_client(api: Arc<Peer>) -> Arc<Mutex<Client>> {
    let tokens = json!({"protocolVersion":"2.0","serverId":"test","userId":"1".repeat(32),"deviceId":"2".repeat(32),"sessionId":"3".repeat(32),"tokenType":"Bearer","accessToken":format!("atk_{}","a".repeat(43)),"refreshToken":format!("rtk_{}","r".repeat(43)),"accessExpiresAt":"2099-01-01T00:00:00Z","refreshExpiresAt":"2099-02-01T00:00:00Z"});
    Arc::new(Mutex::new(
        Client::new(
            Arc::new(api),
            Box::new(Memory(State {
                credentials: Some(tokens),
                ..State::default()
            })),
        )
        .unwrap(),
    ))
}
#[test]
fn quick_background_resume_retires_old_frames_and_releases_worker_slot() {
    let gate = Arc::new((Mutex::new((false, false)), Condvar::new()));
    let mut next = snapshot();
    next["matchSeq"] = (next["matchSeq"].as_u64().unwrap() + 1).into();
    next["streamCursor"] = "1".repeat(32).into();
    let api = peer(
        vec![Ok(Some(
            json!({"protocolVersion":"2.0","type":"update","snapshot":next}),
        ))],
        Some(gate.clone()),
    );
    let c = make_client(api.clone());
    let epoch = Arc::new(AtomicU64::new(0));
    let slots = Arc::new(AtomicUsize::new(0));
    let feed = Feed::start(
        c.clone(),
        MID.into(),
        api.active.clone(),
        epoch.clone(),
        slots.clone(),
    )
    .unwrap();
    let (lock, wake) = &*gate;
    let flags = lock.lock().unwrap();
    let (mut flags, timeout) = wake
        .wait_timeout_while(flags, Duration::from_secs(5), |f| !f.0)
        .unwrap();
    assert!(!timeout.timed_out());
    api.active.store(false, Ordering::Release);
    epoch.fetch_add(1, Ordering::AcqRel);
    api.active.store(true, Ordering::Release);
    assert!(feed.finished());
    flags.1 = true;
    wake.notify_all();
    drop(flags);
    let deadline = Instant::now() + Duration::from_secs(5);
    while slots.load(Ordering::Acquire) != 0 && Instant::now() < deadline {
        std::thread::sleep(Duration::from_millis(5));
    }
    assert_eq!(slots.load(Ordering::Acquire), 0);
    assert!(api.closed.load(Ordering::Acquire));
    assert_eq!(
        c.lock().unwrap().state().snapshots[MID]["streamCursor"],
        "0".repeat(32)
    );
    assert_eq!(feed.status().unwrap()["revision"], 1);
    slots.store(4, Ordering::Release);
    assert!(
        matches!(Feed::start(c,MID.into(),api.active.clone(),epoch,slots.clone()),Err(e) if e.code=="STREAM_LIMIT")
    );
    assert_eq!(slots.load(Ordering::Acquire), 4);
}
