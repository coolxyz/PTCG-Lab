use super::{
    client::Client,
    error::{Error, Result},
    protocol::{checked, segment},
};
use serde_json::{json, Value};
use std::{
    sync::{
        atomic::{AtomicBool, AtomicU64, AtomicUsize, Ordering},
        Arc, Mutex,
    },
    time::{Duration, Instant},
};

#[cfg(test)]
#[path = "feed_tests.rs"]
mod tests;

pub struct Feed {
    stop: Arc<AtomicBool>,
    done: Arc<AtomicBool>,
    result: Arc<Mutex<Value>>,
    epoch: Arc<AtomicU64>,
    generation: u64,
}
impl Feed {
    pub fn start(
        client: Arc<Mutex<Client>>,
        mid: String,
        active: Arc<AtomicBool>,
        epoch: Arc<AtomicU64>,
        slots: Arc<AtomicUsize>,
    ) -> Result<Self> {
        slots
            .fetch_update(Ordering::AcqRel, Ordering::Acquire, |n| {
                (n < 4).then_some(n + 1)
            })
            .map_err(|_| Error::new("STREAM_LIMIT"))?;
        let generation = epoch.load(Ordering::Acquire);
        let feed = Self {
            epoch: epoch.clone(),
            generation,
            stop: Arc::new(AtomicBool::new(false)),
            done: Arc::new(AtomicBool::new(false)),
            result: Arc::new(Mutex::new(
                json!({"revision":0,"event":null,"error":null,"done":false}),
            )),
        };
        let stop = feed.stop.clone();
        let done = feed.done.clone();
        let result = feed.result.clone();
        std::thread::spawn(move || {
            struct Slot(Arc<AtomicUsize>);
            impl Drop for Slot {
                fn drop(&mut self) {
                    self.0.fetch_sub(1, Ordering::AcqRel);
                }
            }
            let _slot = Slot(slots);
            let outcome = pump(client, &mid, &stop, &active, &epoch, generation, &result);
            if let Ok(mut status) = result.lock() {
                status["done"] = true.into();
                if let Err(error) = outcome {
                    status["error"] = json!({"code":error.code});
                }
            }
            done.store(true, Ordering::Release);
        });
        Ok(feed)
    }
    pub fn finished(&self) -> bool {
        self.done.load(Ordering::Acquire) || self.epoch.load(Ordering::Acquire) != self.generation
    }
    pub fn stop(&self) {
        self.stop.store(true, Ordering::Release);
    }
    pub fn status(&self) -> Result<Value> {
        Ok(self
            .result
            .lock()
            .map_err(|_| Error::new("STREAM_UNAVAILABLE"))?
            .clone())
    }
}
impl Drop for Feed {
    fn drop(&mut self) {
        self.stop();
    }
}
fn emit(result: &Mutex<Value>, kind: &str, view: Value) -> Result<()> {
    let mut result = result
        .lock()
        .map_err(|_| Error::new("STREAM_UNAVAILABLE"))?;
    result["revision"] = (result["revision"].as_u64().unwrap() + 1).into();
    result["event"] = json!({"type":kind,"snapshot":view});
    Ok(())
}
fn pump(
    client: Arc<Mutex<Client>>,
    mid: &str,
    stop: &AtomicBool,
    active: &AtomicBool,
    epoch: &AtomicU64,
    generation: u64,
    result: &Mutex<Value>,
) -> Result<()> {
    segment(mid, false)?;
    if stop.load(Ordering::Acquire)
        || !active.load(Ordering::Acquire)
        || epoch.load(Ordering::Acquire) != generation
    {
        return Ok(());
    }
    let view = client
        .lock()
        .map_err(|_| Error::new("PROFILE_BUSY"))?
        .snapshot(mid)?;
    let mut cursor = view["streamCursor"].clone();
    emit(result, "snapshot", view)?;
    let mut failures = 0;
    while !stop.load(Ordering::Acquire)
        && active.load(Ordering::Acquire)
        && epoch.load(Ordering::Acquire) == generation
    {
        let (token, transport) = {
            let mut c = client.lock().map_err(|_| Error::new("PROFILE_BUSY"))?;
            (c.access(None)?, c.transport.clone())
        };
        let subscription = checked(
            "MatchSubscribe",
            json!({"protocolVersion":"2.0","matchId":mid,"streamCursor":cursor}),
        )?;
        let connected = transport.stream(&token, &subscription);
        let error = match connected {
            Ok(mut stream) => {
                let mut idle = Instant::now();
                let error = loop {
                    if stop.load(Ordering::Acquire)
                        || !active.load(Ordering::Acquire)
                        || epoch.load(Ordering::Acquire) != generation
                    {
                        stream.close();
                        return Ok(());
                    }
                    match stream.read() {
                        Ok(None) => {
                            if idle.elapsed() > Duration::from_secs(40) {
                                break Error::network();
                            }
                        }
                        Ok(Some(event)) => {
                            if stop.load(Ordering::Acquire)
                                || !active.load(Ordering::Acquire)
                                || epoch.load(Ordering::Acquire) != generation
                            {
                                stream.close();
                                return Ok(());
                            }
                            idle = Instant::now();
                            if event.get("code").is_some() {
                                match checked("ProtocolError", event) {
                                    Ok(e) => {
                                        break Error {
                                            code: e["code"].as_str().unwrap().into(),
                                            status: 0,
                                            retryable: e["retryable"].as_bool().unwrap(),
                                        }
                                    }
                                    Err(e) => break e,
                                }
                            }
                            if event["protocolVersion"] != "2.0" {
                                break Error::invalid();
                            }
                            if event["type"] == "heartbeat" {
                                if event["streamCursor"] != cursor {
                                    break Error::invalid();
                                }
                                continue;
                            }
                            if !["snapshot", "update", "control"]
                                .contains(&event["type"].as_str().unwrap_or(""))
                                || event.as_object().is_none_or(|m| m.len() != 3)
                            {
                                break Error::invalid();
                            }
                            let mut c = client.lock().map_err(|_| Error::new("PROFILE_BUSY"))?;
                            match c.accept_snapshot(mid, event["snapshot"].clone()) {
                                Ok(view) => {
                                    cursor = view["streamCursor"].clone();
                                    failures = 0;
                                    emit(result, event["type"].as_str().unwrap(), view)?;
                                }
                                Err(error) => break error,
                            }
                        }
                        Err(error) => break error,
                    }
                };
                stream.close();
                error
            }
            Err(error) => error,
        };
        if matches!(error.code.as_str(), "RESYNC_REQUIRED" | "STALE_STREAM") {
            cursor = Value::Null;
        } else if error.code == "UNAUTHORIZED" || error.status == 401 {
            client
                .lock()
                .map_err(|_| Error::new("PROFILE_BUSY"))?
                .access(Some(&token))?;
        } else if error.status == 403 {
            client
                .lock()
                .map_err(|_| Error::new("PROFILE_BUSY"))?
                .request("GET", "/sessions/current", None, true, None, false)
                .and_then(|v| checked("Identity", v))?;
        } else if !error.retryable {
            return Err(error);
        }
        failures += 1;
        if failures >= 3 {
            return Err(Error::new("STREAM_UNAVAILABLE"));
        }
        for _ in 0..failures * 3 {
            if stop.load(Ordering::Acquire)
                || !active.load(Ordering::Acquire)
                || epoch.load(Ordering::Acquire) != generation
            {
                return Ok(());
            }
            std::thread::sleep(Duration::from_millis(100));
        }
    }
    Ok(())
}
