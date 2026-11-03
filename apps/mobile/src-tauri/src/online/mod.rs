pub mod client;
pub mod error;
mod feed;
pub mod protocol;
pub mod transport;
#[cfg(test)]
mod update_tests;
pub mod vault;

use crate::{catalog::Entry, Core};
use client::Client;
use error::{Error, Result};
use serde::Deserialize;
use serde_json::{json, Value};
use std::{
    collections::BTreeMap,
    path::PathBuf,
    sync::{
        atomic::{AtomicBool, AtomicU64, AtomicUsize, Ordering},
        Arc, Mutex,
    },
};
use transport::{Http, Server};
use vault::{SystemKeys, Vault};

#[derive(Deserialize)]
#[serde(
    tag = "op",
    rename_all = "kebab-case",
    rename_all_fields = "camelCase",
    deny_unknown_fields
)]
pub enum Request {
    Profiles {},
    Configuration {},
    Configure {
        origin: String,
        ca_pem: String,
    },
    Call {
        profile: String,
        operation: String,
        body: Value,
    },
    Catalog {
        catalog_version: String,
        asset_version: String,
    },
    Lifecycle {
        active: bool,
    },
    Close {},
}
pub struct Owner {
    root: PathBuf,
    server: Option<Server>,
    client: Option<Arc<Mutex<Client>>>,
    feeds: BTreeMap<String, feed::Feed>,
    pub active: Arc<AtomicBool>,
    epoch: Arc<AtomicU64>,
    slots: Arc<AtomicUsize>,
    seen_epoch: u64,
}
impl Owner {
    pub fn catalog_update_allowed(&mut self, core: &Core) -> Result<()> {
        fn pending(state: &vault::State) -> bool {
            state.refresh_pending
                || state.guest_pending
                || !state.pending.is_empty()
                || !state.control_pending.is_empty()
                || state.lobby.values().any(|v| v.get("result").is_none())
        }
        if let Some(client) = &self.client {
            if pending(
                client
                    .try_lock()
                    .map_err(|_| Error::new("PROFILE_BUSY"))?
                    .state(),
            ) {
                return Err(Error::new("PENDING_COMMAND"));
            }
        } else if let Some(server) = self.server(core)? {
            if self.root.join(format!("{}.sealed", server.key())).exists() {
                use vault::Journal;
                let journal = Vault::open(&self.root, &server.key(), &SystemKeys)?;
                if pending(journal.state()) {
                    return Err(Error::new("PENDING_COMMAND"));
                }
            }
        }
        Ok(())
    }
    pub fn new(root: PathBuf, active: Arc<AtomicBool>, epoch: Arc<AtomicU64>) -> Self {
        Self {
            root,
            server: None,
            client: None,
            feeds: BTreeMap::new(),
            active,
            seen_epoch: epoch.load(Ordering::Acquire),
            epoch,
            slots: Arc::new(AtomicUsize::new(0)),
        }
    }
    fn server(&mut self, core: &Core) -> Result<Option<Server>> {
        if self.server.is_none() {
            if let Some(value) = core
                .store
                .lock()
                .map_err(|_| Error::new("STORAGE_FAILED"))?
                .setting("onlineServer")?
            {
                let server: Server = serde_json::from_value(value)?;
                server.validate()?;
                self.server = Some(server);
            }
        }
        Ok(self.server.clone())
    }
    pub fn handle(&mut self, core: &Core, request: Request) -> Result<Value> {
        match request {
            Request::Profiles {} => Ok(self
                .server(core)?
                .map(|s| vec![json!({"id":s.id(),"origin":s.origin})])
                .unwrap_or_default()
                .into()),
            Request::Configuration {} => Ok(self
                .server(core)?
                .map(|s| json!({"origin":s.origin,"caPem":s.ca_pem}))
                .unwrap_or(Value::Null)),
            Request::Configure { origin, ca_pem } => {
                let server = Server { origin, ca_pem };
                server.validate()?;
                if self.client.is_none() {
                    if let Some(old) = self.server(core)? {
                        if self.root.join(format!("{}.sealed", old.key())).exists() {
                            let journal =
                                Box::new(Vault::open(&self.root, &old.key(), &SystemKeys)?);
                            self.client = Some(Arc::new(Mutex::new(Client::new(
                                Arc::new(Http::new(old)?),
                                journal,
                            )?)));
                        }
                    }
                }
                if let Some(client) = &self.client {
                    let client = client.try_lock().map_err(|_| Error::new("PROFILE_BUSY"))?;
                    if !client.state().pending.is_empty()
                        || !client.state().control_pending.is_empty()
                        || client.state().refresh_pending
                        || client.state().guest_pending
                        || client
                            .state()
                            .lobby
                            .values()
                            .any(|v| v.get("result").is_none())
                    {
                        return Err(Error::new("PENDING_COMMAND"));
                    }
                }
                // Configuration never creates a guest or probes an untrusted peer.
                core.store
                    .lock()
                    .map_err(|_| Error::new("STORAGE_FAILED"))?
                    .set_setting("onlineServer", &serde_json::to_value(&server)?)?;
                self.stop();
                self.client = None;
                self.server = Some(server.clone());
                Ok(json!({"id":server.id(),"origin":server.origin}))
            }
            Request::Catalog {
                catalog_version,
                asset_version,
            } => core
                .online_catalog(&catalog_version, &asset_version)
                .map_err(Into::into),
            Request::Lifecycle { active } => {
                core.set_online_active(active);
                if !active {
                    self.stop();
                }
                Ok(json!({"active":active}))
            }
            Request::Close {} => {
                self.stop();
                if let Some(client) = &self.client {
                    if let Ok(mut client) = client.try_lock() {
                        client.clear_leases();
                    }
                }
                Ok(json!({"closed":true}))
            }
            Request::Call {
                profile,
                operation,
                body,
            } => {
                if !self.active.load(Ordering::Acquire) {
                    return Err(Error::new("APP_SUSPENDED"));
                }
                let server = self.server(core)?.ok_or(Error::new("UNKNOWN_PROFILE"))?;
                if profile != server.id() {
                    return Err(Error::new("UNKNOWN_PROFILE"));
                }
                if matches!(
                    operation.as_str(),
                    "product-decks" | "product-freeze" | "product-check"
                ) {
                    return product(core, &operation, &body);
                }
                if self.client.is_none() {
                    let transport = Arc::new(Http::new(server.clone())?);
                    let journal = Box::new(Vault::open(&self.root, &server.key(), &SystemKeys)?);
                    self.client = Some(Arc::new(Mutex::new(Client::new(transport, journal)?)));
                }
                let client = self.client.as_ref().unwrap().clone();
                let epoch = self.epoch.load(Ordering::Acquire);
                if self.seen_epoch != epoch {
                    client
                        .try_lock()
                        .map_err(|_| Error::new("PROFILE_BUSY"))?
                        .clear_leases();
                    self.seen_epoch = epoch;
                }

                if matches!(operation.as_str(), "watch" | "events" | "unwatch") {
                    fields(&body, &["matchId"], &[])?;
                    let mid = text(&body, "matchId")?;
                    protocol::segment(mid, false)?;
                    if operation == "unwatch" {
                        if let Some(feed) = self.feeds.remove(mid) {
                            feed.stop();
                        }
                        return Ok(json!({"watching":false}));
                    }
                    if operation == "watch" {
                        self.feeds.retain(|_, f| !f.finished());
                        if !self.feeds.contains_key(mid) {
                            if self.feeds.len() >= 4 {
                                return Err(Error::new("STREAM_LIMIT"));
                            }
                            self.feeds.insert(
                                mid.into(),
                                feed::Feed::start(
                                    client,
                                    mid.into(),
                                    self.active.clone(),
                                    self.epoch.clone(),
                                    self.slots.clone(),
                                )?,
                            );
                        }
                    }
                    return self
                        .feeds
                        .get(mid)
                        .ok_or(Error::new("NOT_WATCHING"))?
                        .status();
                }
                let mut client = client.try_lock().map_err(|_| Error::new("PROFILE_BUSY"))?;
                dispatch(core, &mut client, &operation, &body)
            }
        }
    }
    pub fn stop(&mut self) {
        for (_, feed) in std::mem::take(&mut self.feeds) {
            feed.stop();
        }
    }
}
impl Drop for Owner {
    fn drop(&mut self) {
        self.stop();
    }
}
pub fn fields(body: &Value, required: &[&str], optional: &[&str]) -> Result<()> {
    if !body.as_object().is_some_and(|m| {
        required.iter().all(|k| m.contains_key(*k))
            && m.keys()
                .all(|k| required.contains(&k.as_str()) || optional.contains(&k.as_str()))
    }) {
        return Err(Error::new("INVALID_REQUEST"));
    }
    Ok(())
}
pub fn text<'a>(body: &'a Value, key: &str) -> Result<&'a str> {
    body[key]
        .as_str()
        .filter(|s| !s.is_empty() && s.len() <= 128)
        .ok_or(Error::new("INVALID_REQUEST"))
}
fn number(body: &Value, key: &str) -> Result<u64> {
    body[key]
        .as_u64()
        .filter(|n| *n <= 9007199254740991)
        .ok_or(Error::new("INVALID_REQUEST"))
}
fn dispatch(core: &Core, client: &mut Client, operation: &str, body: &Value) -> Result<Value> {
    match operation {
        "status" => {
            fields(body, &[], &[])?;
            Ok(client.status())
        }
        "capabilities" => {
            fields(body, &[], &[])?;
            client.capabilities()
        }
        "guest" => {
            fields(body, &[], &["name", "deviceName"])?;
            client.guest(
                if body.get("name").is_some() {
                    text(body, "name")?
                } else {
                    "训练家"
                },
                if body.get("deviceName").is_some() {
                    text(body, "deviceName")?
                } else {
                    "PTCG Lab iOS"
                },
            )
        }
        "snapshot" | "recover" | "resolve-rejected" | "recover-control" => {
            fields(body, &["matchId"], &[])?;
            let mid = text(body, "matchId")?;
            protocol::segment(mid, false)?;
            match operation {
                "snapshot" => client.snapshot(mid),
                "recover" => Ok(json!({"receipt":client.recover(mid)?})),
                "resolve-rejected" => Ok(json!({"receipt":client.resolve(mid)?})),
                _ => {
                    let epoch = client
                        .state()
                        .control_pending
                        .get(mid)
                        .and_then(|v| v["expectedControllerEpoch"].as_u64())
                        .ok_or(Error::new("NO_PENDING_CONTROL"))?;
                    client.control(mid, epoch)
                }
            }
        }
        "control" => {
            fields(body, &["matchId", "expectedControllerEpoch"], &[])?;
            client.control(
                text(body, "matchId")?,
                number(body, "expectedControllerEpoch")?,
            )
        }
        "command" | "resign" => {
            fields(body, &["matchId", "lease", "command"], &[])?;
            client.submit(
                text(body, "matchId")?,
                text(body, "lease")?,
                body["command"].clone(),
                operation == "resign",
            )
        }
        "replay" => {
            fields(body, &["matchId"], &["cursor"])?;
            let cursor = if body.get("cursor").is_some() {
                Some(text(body, "cursor")?)
            } else {
                None
            };
            client.replay(text(body, "matchId")?, cursor)
        }
        "room" => {
            fields(body, &["roomId"], &[])?;
            let room = text(body, "roomId")?;
            protocol::segment(room, false)?;
            let value = client
                .request("GET", &format!("/rooms/{room}"), None, true, None, false)
                .and_then(|v| protocol::checked("RoomView", v))?;
            if value["roomId"] != room {
                return Err(Error::new("IDENTITY_MISMATCH"));
            }
            Ok(value)
        }
        "product-deck" => {
            fields(body, &["revisionId", "ruleReleaseId", "requestId"], &[])?;
            let revision = text(body, "revisionId")?;
            let rule = text(body, "ruleReleaseId")?;
            let request = text(body, "requestId")?;
            let saved = client.state().lobby.get(request).cloned();
            let entries = if let Some(saved) = saved {
                if saved["localRevisionId"] != revision || saved["body"]["ruleReleaseId"] != rule {
                    return Err(Error::new("REQUEST_REUSED"));
                }
                saved["body"]["entries"].clone()
            } else {
                let frozen = frozen(core, revision)?;
                if frozen["catalogVersion"]
                    != core
                        .catalog
                        .read()
                        .map_err(|_| Error::new("LOCAL_FAILED"))?
                        .version
                {
                    return Err(Error::new("CATALOG_MISMATCH"));
                }
                frozen["entries"].clone()
            };
            client.lobby("DeckSubmission","/decks",json!({"protocolVersion":"2.0","requestId":request,"ruleReleaseId":rule,"entries":entries}),"DeckAccepted",Some(revision))
        }
        "deck" => {
            fields(body, &["entries", "ruleReleaseId", "requestId"], &[])?;
            client.lobby("DeckSubmission","/decks",json!({"protocolVersion":"2.0","requestId":text(body,"requestId")?,"ruleReleaseId":text(body,"ruleReleaseId")?,"entries":body["entries"]}),"DeckAccepted",None)
        }
        "create-room" => {
            fields(body, &["deckRevision", "ruleReleaseId", "requestId"], &[])?;
            client.lobby("RoomCreate","/rooms",json!({"protocolVersion":"2.0","requestId":text(body,"requestId")?,"deckRevision":text(body,"deckRevision")?,"ruleReleaseId":text(body,"ruleReleaseId")?}),"RoomView",None)
        }
        "join-room" => {
            fields(body, &["inviteCode", "deckRevision", "requestId"], &[])?;
            client.lobby("RoomJoin","/rooms/join",json!({"protocolVersion":"2.0","requestId":text(body,"requestId")?,"inviteCode":text(body,"inviteCode")?,"deckRevision":text(body,"deckRevision")?}),"RoomView",None)
        }
        "ready" | "room-deck" | "leave-room" => {
            let extra = match operation {
                "ready" => "ready",
                "room-deck" => "deckRevision",
                _ => "",
            };
            let mut required = vec!["roomId", "expectedRoomVersion", "requestId"];
            if !extra.is_empty() {
                required.push(extra);
            }
            fields(body, &required, &[])?;
            let room = text(body, "roomId")?;
            protocol::segment(room, false)?;
            let (action, kind, response) = match operation {
                "ready" => ("ready", "RoomReady", "RoomView"),
                "room-deck" => ("deck", "RoomDeck", "RoomView"),
                _ => ("leave", "RoomLeave", "RoomLeft"),
            };
            let mut payload = json!({"protocolVersion":"2.0","requestId":text(body,"requestId")?,"expectedRoomVersion":number(body,"expectedRoomVersion")?});
            if !extra.is_empty() {
                payload[extra] = body[extra].clone();
            }
            client.lobby(
                kind,
                &format!("/rooms/{room}/{action}"),
                payload,
                response,
                None,
            )
        }
        _ => Err(Error::new("UNKNOWN_OPERATION")),
    }
}
fn frozen(core: &Core, revision: &str) -> Result<Value> {
    let store = core
        .store
        .lock()
        .map_err(|_| Error::new("STORAGE_FAILED"))?;
    for deck in store.decks()? {
        for snapshot in store.revisions(&deck.id)?["revisions"].as_array().unwrap() {
            if snapshot["id"] == revision {
                return Ok(snapshot.clone());
            }
        }
    }
    Err(Error::new("REVISION_NOT_FOUND"))
}
fn product(core: &Core, operation: &str, body: &Value) -> Result<Value> {
    match operation {
        "product-decks" => {
            fields(body, &[], &[])?;
            let store = core
                .store
                .lock()
                .map_err(|_| Error::new("STORAGE_FAILED"))?;
            let mut decks = Vec::new();
            for deck in store.decks()? {
                let rows = store.revisions(&deck.id)?;
                let count = rows["revisions"].as_array().unwrap().len();
                let revisions=rows["revisions"].as_array().unwrap().iter().enumerate().map(|(i,r)|json!({"id":r["id"],"number":count-i,"count":r["entries"].as_array().unwrap().iter().map(|e|e["quantity"].as_u64().unwrap()).sum::<u64>()})).collect::<Vec<_>>();
                decks.push(json!({"id":deck.id,"name":deck.name,"version":deck.version,"revisions":revisions}));
            }
            Ok(json!({"decks":decks}))
        }
        "product-freeze" => {
            fields(body, &["deckId", "expectedVersion"], &[])?;
            let catalog = core
                .catalog
                .read()
                .map_err(|_| Error::new("LOCAL_FAILED"))?;
            let value = core
                .store
                .lock()
                .map_err(|_| Error::new("STORAGE_FAILED"))?
                .freeze(
                    &catalog,
                    text(body, "deckId")?.into(),
                    number(body, "expectedVersion")?,
                )?;
            Ok(json!({"revisionId":value["id"]}))
        }
        _ => {
            fields(body, &["revisionId"], &[])?;
            let snapshot = frozen(core, text(body, "revisionId")?)?;
            let entries: Vec<Entry> = serde_json::from_value(snapshot["entries"].clone())?;
            let check = core
                .catalog
                .read()
                .map_err(|_| Error::new("LOCAL_FAILED"))?
                .structural(&entries)?;
            let issues = if check["structureReady"] == true {
                vec![]
            } else {
                vec![
                    json!({"code":"DECK_STRUCTURE","explanation":"须为 60 张并包含基础宝可梦；此处不执行对战规则","suggestedFix":"补全卡组后交由服务器校验当前准入规则","cardRefs":[]}),
                ]
            };
            Ok(json!({"total":check["count"],"issues":issues,"scope":"structure-only"}))
        }
    }
}
