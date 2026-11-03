use super::{
    error::{Error, Result},
    protocol::{checked, segment},
    transport::Transport,
    vault::{Journal, State},
};
use serde_json::{json, Value};
use std::{collections::BTreeMap, sync::Arc};

pub struct Client {
    pub transport: Arc<dyn Transport>,
    pub journal: Box<dyn Journal>,
    leases: BTreeMap<String, String>,
}
impl Client {
    pub fn new(transport: Arc<dyn Transport>, journal: Box<dyn Journal>) -> Result<Self> {
        if let Some(tokens) = &journal.state().credentials {
            checked("Tokens", tokens.clone())?;
        }
        for (mid, pending) in &journal.state().pending {
            segment(mid, false)?;
            if !matches!(pending["action"].as_str(), Some("commands" | "resign"))
                || pending.as_object().is_none_or(|m| m.len() != 3)
            {
                return Err(Error::invalid());
            }
            segment(
                pending["controller"].as_str().ok_or_else(Error::invalid)?,
                false,
            )?;
            checked(
                if pending["action"] == "resign" {
                    "MatchResign"
                } else {
                    "Command"
                },
                pending["body"].clone(),
            )?;
            segment(pending["body"]["commandId"].as_str().unwrap(), true)?;
        }
        for (mid, controller) in &journal.state().controllers {
            segment(mid, false)?;
            segment(controller, false)?;
        }
        for (mid, pending) in &journal.state().control_pending {
            segment(mid, false)?;
            checked("MatchControl", pending.clone())?;
            if journal.state().controllers.get(mid).map(String::as_str)
                != pending["controllerId"].as_str()
            {
                return Err(Error::new("IDENTITY_MISMATCH"));
            }
        }
        Ok(Self {
            transport,
            journal,
            leases: BTreeMap::new(),
        })
    }
    pub fn state(&self) -> &State {
        self.journal.state()
    }
    pub fn identity(tokens: &Value) -> Value {
        let mut identity = serde_json::Map::new();
        for key in [
            "protocolVersion",
            "serverId",
            "userId",
            "deviceId",
            "sessionId",
        ] {
            identity.insert(key.into(), tokens[key].clone());
        }
        identity.into()
    }
    pub fn status(&self) -> Value {
        let s = self.state();
        json!({"identity":s.credentials.as_ref().map(Self::identity),"reauthenticationRequired":s.refresh_pending||s.guest_pending,
            "pendingMatches":s.pending.keys().collect::<Vec<_>>(),"recentMatches":s.history.values().rev().collect::<Vec<_>>(),
            "recovery":{"commands":s.pending.iter().map(|(mid,p)|json!({"matchId":mid,"commandId":p["body"]["commandId"],"action":p["action"]})).collect::<Vec<_>>(),
                        "controls":s.control_pending.iter().map(|(mid,p)|json!({"matchId":mid,"expectedControllerEpoch":p["expectedControllerEpoch"]})).collect::<Vec<_>>()},
            "productPending":s.lobby.values().filter(|p|p.get("result").is_none()&&p.get("localRevisionId").is_some()).map(|p|json!({"revisionId":p["localRevisionId"],"ruleReleaseId":p["body"]["ruleReleaseId"],"requestId":p["body"]["requestId"]})).collect::<Vec<_>>()})
    }
    pub fn capabilities(&mut self) -> Result<Value> {
        let value = self.request("GET", "/capabilities", None, false, None, false)?;
        let value = checked("Capabilities", value)?;
        if !value["supportedProtocolVersions"]
            .as_array()
            .unwrap()
            .iter()
            .any(|v| v == "2.0")
        {
            return Err(Error::new("UNSUPPORTED_PROTOCOL"));
        }
        Ok(value)
    }
    pub fn guest(&mut self, name: &str, device: &str) -> Result<Value> {
        if self.state().credentials.is_some() {
            return Err(Error::new("IDENTITY_ALREADY_EXISTS"));
        }
        if self.state().guest_pending || self.state().refresh_pending {
            return Err(Error::new("IDENTITY_CREATION_UNKNOWN"));
        }
        self.capabilities()?;
        let body = checked(
            "Create",
            json!({"protocolVersion":"2.0","name":name,"deviceName":device}),
        )?;
        let mut next = self.state().clone();
        next.guest_pending = true;
        self.journal.save(next)?;
        let result = self
            .transport
            .request("POST", "/sessions", Some(&body), None, None);
        let tokens = match result.and_then(|v| checked("Tokens", v)) {
            Ok(value) => value,
            Err(error) => {
                if matches!(error.status, 400 | 422) && !error.retryable {
                    let mut next = self.state().clone();
                    next.guest_pending = false;
                    self.journal.save(next)?;
                }
                return Err(error);
            }
        };
        let identity = Self::identity(&tokens);
        let mut next = self.state().clone();
        next.credentials = Some(tokens);
        next.guest_pending = false;
        self.journal.save(next)?;
        Ok(identity)
    }
    pub fn access(&mut self, rejected: Option<&str>) -> Result<String> {
        let state = self.state();
        if state.refresh_pending {
            return Err(Error::new("REAUTHENTICATION_REQUIRED"));
        }
        let current = state
            .credentials
            .clone()
            .ok_or(Error::new("REAUTHENTICATION_REQUIRED"))?;
        checked("Tokens", current.clone())?;
        let token = current["accessToken"].as_str().unwrap();
        let expiration = time::OffsetDateTime::parse(
            current["accessExpiresAt"].as_str().unwrap(),
            &time::format_description::well_known::Rfc3339,
        )
        .map_err(|_| Error::invalid())?;
        if (rejected.is_none()
            && expiration.unix_timestamp() > time::OffsetDateTime::now_utc().unix_timestamp() + 20)
            || rejected.is_some_and(|old| old != token)
        {
            return Ok(token.into());
        }
        let mut next = state.clone();
        next.refresh_pending = true;
        self.journal.save(next)?;
        let body = json!({"protocolVersion":"2.0","refreshToken":current["refreshToken"]});
        let tokens = self
            .transport
            .request("POST", "/sessions/refresh", Some(&body), None, None)
            .and_then(|v| checked("Tokens", v))
            .map_err(|_| Error::new("REAUTHENTICATION_REQUIRED"))?;
        if Self::identity(&tokens) != Self::identity(&current) {
            return Err(Error::new("IDENTITY_MISMATCH"));
        }
        let token = tokens["accessToken"].as_str().unwrap().to_owned();
        let mut next = self.state().clone();
        next.credentials = Some(tokens);
        next.refresh_pending = false;
        self.journal.save(next)?;
        Ok(token)
    }
    pub fn request(
        &mut self,
        method: &str,
        path: &str,
        body: Option<&Value>,
        auth: bool,
        controller: Option<&str>,
        retry: bool,
    ) -> Result<Value> {
        let mut token = if auth { Some(self.access(None)?) } else { None };
        let mut rotated = false;
        let attempts = if method == "GET" || retry { 3 } else { 1 };
        for attempt in 0..attempts {
            let mut result =
                self.transport
                    .request(method, path, body, token.as_deref(), controller);
            if result.as_ref().is_err_and(|e| e.status == 401) && auth && !rotated {
                token = Some(self.access(token.as_deref())?);
                rotated = true;
                result = self
                    .transport
                    .request(method, path, body, token.as_deref(), controller);
            }
            match result {
                Ok(value) => return Ok(value),
                Err(error) => {
                    if !error.retryable || error.status == 429 || attempt + 1 == attempts {
                        return Err(error);
                    }
                    std::thread::sleep(std::time::Duration::from_millis(50 * (1 << attempt)));
                }
            }
        }
        unreachable!()
    }
    pub fn lobby(
        &mut self,
        kind: &str,
        path: &str,
        body: Value,
        response: &str,
        local_revision: Option<&str>,
    ) -> Result<Value> {
        let body = checked(kind, body)?;
        let request = body["requestId"]
            .as_str()
            .ok_or_else(Error::invalid)?
            .to_owned();
        if kind == "DeckSubmission" || kind == "RoomCreate" {
            let caps = self.capabilities()?;
            if !caps["ruleReleaseIds"]
                .as_array()
                .unwrap()
                .contains(&body["ruleReleaseId"])
            {
                return Err(Error::new("RULE_RELEASE_UNAVAILABLE"));
            }
        }
        self.access(None)?;
        if let Some(saved) = self.state().lobby.get(&request) {
            if saved["kind"] != kind
                || saved["path"] != path
                || saved["body"] != body
                || saved.get("localRevisionId").and_then(Value::as_str) != local_revision
            {
                return Err(Error::new("REQUEST_REUSED"));
            }
            if let Some(result) = saved.get("result") {
                return checked(response, result.clone());
            }
        } else {
            let mut next = self.state().clone();
            if next.lobby.len() >= 128 {
                let removable = next
                    .lobby
                    .iter()
                    .find(|(_, v)| v.get("result").is_some())
                    .map(|(k, _)| k.clone());
                if let Some(key) = removable {
                    next.lobby.remove(&key);
                } else {
                    return Err(Error::new("STORAGE_CAPACITY"));
                }
            }
            let mut pending = json!({"kind":kind,"path":path,"body":body});
            if let Some(id) = local_revision {
                pending["localRevisionId"] = id.into();
            }
            next.lobby.insert(request.clone(), pending);
            self.journal.save(next)?;
        }
        let result = self
            .request("POST", path, Some(&body), true, None, true)
            .and_then(|v| checked(response, v));
        let result = match result {
            Ok(value) => value,
            Err(error) => {
                if matches!(error.status, 400 | 403 | 409 | 422) && !error.retryable {
                    let mut next = self.state().clone();
                    next.lobby.remove(&request);
                    self.journal.save(next)?;
                }
                return Err(error);
            }
        };
        if (kind == "DeckSubmission" || kind == "RoomCreate")
            && result["ruleReleaseId"] != body["ruleReleaseId"]
        {
            return Err(Error::new("IDENTITY_MISMATCH"));
        }
        let mut next = self.state().clone();
        next.lobby.get_mut(&request).unwrap()["result"] = result.clone();
        self.journal.save(next)?;
        Ok(result)
    }
    pub fn accept_snapshot(&mut self, mid: &str, value: Value) -> Result<Value> {
        let view = checked("MatchSnapshot", value)?;
        if view["matchId"] != mid {
            return Err(Error::new("IDENTITY_MISMATCH"));
        }
        if let Some(old) = self.state().snapshots.get(mid) {
            if old["viewerSeat"] != view["viewerSeat"]
                || old["ruleReleaseId"] != view["ruleReleaseId"]
            {
                return Err(Error::new("IDENTITY_MISMATCH"));
            }
            if view["matchSeq"].as_u64() < old["matchSeq"].as_u64()
                || view["controllerEpoch"].as_u64() < old["controllerEpoch"].as_u64()
            {
                return Err(Error::new("STALE_STREAM"));
            }
        }
        let mut next = self.state().clone();
        next.snapshots.insert(mid.into(), view.clone());
        let mut summary = serde_json::Map::new();
        for key in ["matchId", "viewerSeat", "done", "winner", "matchSeq"] {
            summary.insert(key.into(), view[key].clone());
        }
        next.history.insert(mid.into(), summary.into());
        while next.snapshots.len() > 8 {
            let key = next
                .snapshots
                .keys()
                .find(|k| k.as_str() != mid)
                .unwrap()
                .clone();
            next.snapshots.remove(&key);
        }
        if next.history.len() > 10000 {
            return Err(Error::new("STORAGE_CAPACITY"));
        }
        self.journal.save(next)?;
        Ok(view)
    }
    pub fn snapshot(&mut self, mid: &str) -> Result<Value> {
        segment(mid, false)?;
        let value = self.request("GET", &format!("/matches/{mid}"), None, true, None, false)?;
        match self.accept_snapshot(mid, value) {
            Err(error) if error.code == "STALE_STREAM" => Ok(self.state().snapshots[mid].clone()),
            result => result,
        }
    }
    pub fn control(&mut self, mid: &str, epoch: u64) -> Result<Value> {
        segment(mid, false)?;
        if self.state().pending.contains_key(mid) {
            return Err(Error::new("PENDING_COMMAND"));
        }
        let mut next = self.state().clone();
        let controller = next
            .controllers
            .entry(mid.into())
            .or_insert(crate::catalog::id()?)
            .clone();
        let body = if let Some(body) = next.control_pending.get(mid) {
            if body["expectedControllerEpoch"] != epoch {
                return Err(Error::new("PENDING_CONTROL"));
            }
            body.clone()
        } else {
            checked(
                "MatchControl",
                json!({"protocolVersion":"2.0","requestId":crate::catalog::id()?,"controllerId":controller,"expectedControllerEpoch":epoch}),
            )?
        };
        next.control_pending.insert(mid.into(), body.clone());
        self.journal.save(next)?;
        self.leases.remove(mid);
        let result = self
            .request(
                "POST",
                &format!("/matches/{mid}/control"),
                Some(&body),
                true,
                None,
                true,
            )
            .and_then(|v| self.accept_snapshot(mid, v));
        if let Err(error) = result {
            if matches!(error.status, 400 | 403 | 409 | 422) && !error.retryable {
                let mut next = self.state().clone();
                next.control_pending.remove(mid);
                self.journal.save(next)?;
            }
            return Err(error);
        }
        let mut next = self.state().clone();
        next.control_pending.remove(mid);
        self.journal.save(next)?;
        let view = self.snapshot(mid)?;
        let lease = crate::catalog::id()?;
        self.leases.insert(mid.into(), lease.clone());
        Ok(json!({"lease":lease,"snapshot":view}))
    }
    pub fn submit(&mut self, mid: &str, lease: &str, body: Value, resign: bool) -> Result<Value> {
        segment(mid, false)?;
        if !self.leases.get(mid).is_some_and(|v| v == lease) {
            return Err(Error::new("LOCAL_CONTROL_REQUIRED"));
        }
        let body = checked(if resign { "MatchResign" } else { "Command" }, body)?;
        segment(body["commandId"].as_str().unwrap(), true)?;
        let controller = self
            .state()
            .controllers
            .get(mid)
            .ok_or(Error::new("CONTROL_REQUIRED"))?
            .clone();
        let pending = json!({"body":body,"controller":controller,"action":if resign{"resign"}else{"commands"}});
        if let Some(old) = self.state().pending.get(mid) {
            if old != &pending {
                return Err(Error::new("PENDING_COMMAND"));
            }
            return self.recover(mid).map(|v| v.unwrap_or(Value::Null));
        }
        if self.state().pending.len() >= 8 {
            return Err(Error::new("STORAGE_CAPACITY"));
        }
        let mut next = self.state().clone();
        next.pending.insert(mid.into(), pending);
        self.journal.save(next)?;
        self.send_pending(mid, true)
    }
    fn send_pending(&mut self, mid: &str, fresh: bool) -> Result<Value> {
        let pending = self
            .state()
            .pending
            .get(mid)
            .ok_or(Error::new("NO_PENDING_COMMAND"))?
            .clone();
        let result = self
            .request(
                "POST",
                &format!("/matches/{mid}/{}", pending["action"].as_str().unwrap()),
                Some(&pending["body"]),
                true,
                pending["controller"].as_str(),
                false,
            )
            .and_then(|v| receipt(v, mid, pending["body"]["commandId"].as_str().unwrap()));
        if result.is_ok()
            || result
                .as_ref()
                .is_err_and(|e| fresh && matches!(e.status, 400 | 403 | 409 | 422) && !e.retryable)
        {
            let mut next = self.state().clone();
            next.pending.remove(mid);
            self.journal.save(next)?;
        }
        result
    }
    pub fn recover(&mut self, mid: &str) -> Result<Option<Value>> {
        segment(mid, false)?;
        let Some(pending) = self.state().pending.get(mid).cloned() else {
            return Ok(None);
        };
        let id = pending["body"]["commandId"].as_str().unwrap();
        segment(id, true)?;
        match self.request(
            "GET",
            &format!("/matches/{mid}/receipts/{id}"),
            None,
            true,
            None,
            false,
        ) {
            Ok(value) => {
                let value = receipt(value, mid, id)?;
                let mut next = self.state().clone();
                next.pending.remove(mid);
                self.journal.save(next)?;
                Ok(Some(value))
            }
            Err(error) if error.status == 404 => self.send_pending(mid, false).map(Some),
            Err(error) => Err(error),
        }
    }
    pub fn resolve(&mut self, mid: &str) -> Result<Option<Value>> {
        segment(mid, false)?;
        let Some(pending) = self.state().pending.get(mid).cloned() else {
            return Ok(None);
        };
        let view = self.snapshot(mid)?;
        if view["matchSeq"] == pending["body"]["expectedMatchSeq"]
            && view["controllerEpoch"] == pending["body"]["controllerEpoch"]
        {
            return Err(Error::new("OUTCOME_UNKNOWN"));
        }
        let id = pending["body"]["commandId"].as_str().unwrap();
        segment(id, true)?;
        let result = match self.request(
            "GET",
            &format!("/matches/{mid}/receipts/{id}"),
            None,
            true,
            None,
            false,
        ) {
            Ok(v) => Some(receipt(v, mid, id)?),
            Err(e) if e.status == 404 => None,
            Err(e) => return Err(e),
        };
        let mut next = self.state().clone();
        next.pending.remove(mid);
        self.journal.save(next)?;
        Ok(result)
    }
    pub fn replay(&mut self, mid: &str, cursor: Option<&str>) -> Result<Value> {
        segment(mid, false)?;
        let path = if let Some(cursor) = cursor {
            segment(cursor, false)?;
            format!("/matches/{mid}/frames/{cursor}")
        } else {
            format!("/matches/{mid}/frames")
        };
        let page = self
            .request("GET", &path, None, true, None, false)
            .and_then(|v| checked("ReplayPage", v))?;
        let view = self.snapshot(mid)?;
        for key in ["matchId", "viewerSeat", "ruleReleaseId"] {
            if page[key] != view[key] {
                return Err(Error::new("IDENTITY_MISMATCH"));
            }
        }
        Ok(page)
    }
    pub fn clear_leases(&mut self) {
        self.leases.clear();
    }
}
pub fn receipt(value: Value, mid: &str, id: &str) -> Result<Value> {
    let keys = std::collections::BTreeSet::from([
        "protocolVersion",
        "matchId",
        "commandId",
        "accepted",
        "matchSeq",
        "stateVersion",
        "streamCursor",
    ]);
    if value.as_object().map(|v| {
        v.keys()
            .map(String::as_str)
            .collect::<std::collections::BTreeSet<_>>()
    }) != Some(keys)
        || value["protocolVersion"] != "2.0"
        || value["matchId"] != mid
        || value["commandId"] != id
        || value["accepted"] != true
        || ["matchSeq", "stateVersion"]
            .iter()
            .any(|k| !value[k].as_u64().is_some_and(|n| n <= 9007199254740991))
        || value["streamCursor"]
            .as_str()
            .is_none_or(|s| segment(s, false).is_err())
    {
        return Err(Error::invalid());
    }
    Ok(value)
}
