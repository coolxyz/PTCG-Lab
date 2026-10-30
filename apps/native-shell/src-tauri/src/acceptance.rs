//! Opt-in local operator test channel. No HTTP/JS native command authority.
//! Existing profiles are refused unless marked by this exact isolated test run.
use serde::Deserialize;
use serde_json::json;
use std::{
    fs,
    io::{BufRead, Read, Write},
    path::PathBuf,
    sync::{
        atomic::{AtomicBool, AtomicU32, Ordering},
        Arc, Mutex,
    },
};
use tauri::{Manager, WebviewWindow};

const MARKER: &str = ".ptcg-acceptance.json";

#[derive(Deserialize)]
#[serde(deny_unknown_fields, rename_all = "camelCase")]
struct Request {
    schema: u32,
    nonce: String,
    state_dir: PathBuf,
    mode: String,
    network: Option<Network>,
}

#[derive(Clone, Deserialize)]
#[serde(deny_unknown_fields, rename_all = "camelCase")]
struct Network {
    origin: String,
    ca_file: PathBuf,
}
impl Network {
    fn valid(&self, state: &std::path::Path) -> bool {
        let Ok(url) = tauri::Url::parse(&self.origin) else {
            return false;
        };
        let Some(parent) = state.parent() else {
            return false;
        };
        let Ok(info) = fs::symlink_metadata(&self.ca_file) else {
            return false;
        };
        url.scheme() == "https"
            && url.host_str() == Some("127.0.0.1")
            && url.port().is_some_and(|p| p >= 20000)
            && self.origin == url.origin().ascii_serialization()
            && url.username().is_empty()
            && url.password().is_none()
            && url.query().is_none()
            && url.fragment().is_none()
            && url.path() == "/"
            && self.ca_file.is_absolute()
            && self.ca_file.starts_with(parent)
            && self
                .ca_file
                .components()
                .all(|p| !matches!(p, std::path::Component::ParentDir))
            && !self.ca_file.ancestors().any(|p| p.is_symlink())
            && info.is_file()
            && !crate::platform::reparse(&info)
            && info.len() <= 65536
    }
    fn config(&self) -> serde_json::Value {
        json!({"servers":{"acceptance":{"origin":self.origin,"ca_file":self.ca_file}},"manifests":[]})
    }
}

fn frame() -> Result<Vec<u8>, &'static str> {
    let mut line = Vec::new();
    std::io::stdin()
        .lock()
        .take(8193)
        .read_until(b'\n', &mut line)
        .map_err(|_| "INVALID_ACCEPTANCE")?;
    if line.len() > 8192 || line.last() != Some(&b'\n') {
        return Err("INVALID_ACCEPTANCE");
    }
    Ok(line)
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields, rename_all = "camelCase")]
struct Checks {
    nonce: String,
    app_rendered: bool,
    same_origin_session: bool,
    offline_bridge: bool,
    card_catalog: bool,
    deck_persistence: bool,
}
impl Checks {
    fn valid(&self, nonce: &str) -> bool {
        self.nonce == nonce
            && self.app_rendered
            && self.same_origin_session
            && self.offline_bridge
            && self.card_catalog
            && self.deck_persistence
    }
}

pub struct Session {
    pub state: PathBuf,
    nonce: String,
    new: bool,
    observed: Mutex<Option<(u32, String)>>,
    injected: AtomicBool,
    reported: AtomicBool,
    network: Option<Network>,
    online_phase: AtomicU32,
}

impl Session {
    pub fn load() -> Result<Option<Arc<Self>>, &'static str> {
        let args: Vec<_> = std::env::args_os().skip(1).collect();
        if !args.iter().any(|v| v == "--acceptance") {
            return Ok(None);
        }
        if args.len() != 1 {
            return Err("INVALID_ACCEPTANCE");
        }
        Self::parse(&frame()?).map(|s| Some(Arc::new(s)))
    }
    fn parse(raw: &[u8]) -> Result<Self, &'static str> {
        let r: Request = serde_json::from_slice(raw).map_err(|_| "INVALID_ACCEPTANCE")?;
        if r.schema != 1
            || r.nonce.len() != 64
            || !r
                .nonce
                .bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
            || !r.state_dir.is_absolute()
            || !matches!(r.mode.as_str(), "new" | "reopen")
        {
            return Err("INVALID_ACCEPTANCE");
        }
        if r.mode == "new" {
            if fs::symlink_metadata(&r.state_dir).is_ok() {
                return Err("EXISTING_ACCEPTANCE_PROFILE");
            }
        } else {
            let marker = r.state_dir.join(MARKER);
            let info = fs::symlink_metadata(&marker).map_err(|_| "INVALID_ACCEPTANCE_PROFILE")?;
            if !info.is_file() || crate::platform::reparse(&info) || info.len() > 256 {
                return Err("INVALID_ACCEPTANCE_PROFILE");
            }
            let expected = json!({"schema":1,"run":r.nonce});
            let actual: serde_json::Value = serde_json::from_slice(
                &fs::read(marker).map_err(|_| "INVALID_ACCEPTANCE_PROFILE")?,
            )
            .map_err(|_| "INVALID_ACCEPTANCE_PROFILE")?;
            if actual != expected {
                return Err("INVALID_ACCEPTANCE_PROFILE");
            }
        }
        let config = r.state_dir.join("connection.json");
        if let Some(network) = &r.network {
            if !network.valid(&r.state_dir) {
                return Err("INVALID_ACCEPTANCE_SERVER");
            }
            if r.mode == "reopen" {
                let info =
                    fs::symlink_metadata(&config).map_err(|_| "INVALID_ACCEPTANCE_PROFILE")?;
                if !info.is_file()
                    || crate::platform::reparse(&info)
                    || info.len() > 16384
                    || serde_json::from_slice::<serde_json::Value>(
                        &fs::read(&config).map_err(|_| "INVALID_ACCEPTANCE_PROFILE")?,
                    )
                    .ok()
                    .as_ref()
                        != Some(&network.config())
                {
                    return Err("INVALID_ACCEPTANCE_PROFILE");
                }
            }
        } else if config.symlink_metadata().is_ok() {
            return Err("ACCEPTANCE_REQUIRES_OFFLINE_PROFILE");
        }
        Ok(Self {
            state: r.state_dir,
            nonce: r.nonce,
            new: r.mode == "new",
            observed: Mutex::new(None),
            injected: AtomicBool::new(false),
            reported: AtomicBool::new(false),
            network: r.network,
            online_phase: AtomicU32::new(0),
        })
    }
    pub fn mark(&self) -> Result<(), &'static str> {
        if self.new {
            let mut options = fs::OpenOptions::new();
            options.write(true).create_new(true);
            #[cfg(unix)]
            {
                use std::os::unix::fs::OpenOptionsExt;
                options.mode(0o600);
            }
            let mut file = options
                .open(self.state.join(MARKER))
                .map_err(|_| "INVALID_ACCEPTANCE_PROFILE")?;
            write!(file, "{}", json!({"schema":1,"run":self.nonce}))
                .map_err(|_| "INVALID_ACCEPTANCE_PROFILE")?;
            file.sync_all().map_err(|_| "INVALID_ACCEPTANCE_PROFILE")?;
            if let Some(network) = &self.network {
                let mut options = fs::OpenOptions::new();
                options.write(true).create_new(true);
                #[cfg(unix)]
                {
                    use std::os::unix::fs::OpenOptionsExt;
                    options.mode(0o600);
                }
                let mut file = options
                    .open(self.state.join("connection.json"))
                    .map_err(|_| "INVALID_ACCEPTANCE_PROFILE")?;
                write!(file, "{}", network.config()).map_err(|_| "INVALID_ACCEPTANCE_PROFILE")?;
                file.sync_all().map_err(|_| "INVALID_ACCEPTANCE_PROFILE")?;
            }
        }
        Ok(())
    }
    fn emit(&self, value: serde_json::Value) {
        let mut output = std::io::stdout().lock();
        let _ = writeln!(output, "{value}");
        let _ = output.flush();
    }
    pub fn failed(&self) {
        self.emit(json!({"schema":1,"type":"error","nonce":self.nonce,"code":"NATIVE_GUI_FAILED"}));
    }
    pub fn focused(&self) {
        self.emit(json!({"schema":1,"type":"focused","nonce":self.nonce}));
    }
    pub fn backend(&self, pid: u32, origin: &str) {
        *self.observed.lock().unwrap() = Some((pid, origin.into()));
    }
    pub fn page(&self, window: &WebviewWindow, url: &tauri::Url) {
        let observed = self.observed.lock().unwrap();
        if !observed
            .as_ref()
            .is_some_and(|(_, origin)| url.as_str() == format!("{origin}/"))
            || self.injected.swap(true, Ordering::AcqRel)
        {
            return;
        }
        let source = if self.network.is_some() {
            include_str!("online_probe.js")
        } else {
            include_str!("gui_probe.js")
        };
        let script = source
            .replace("__NONCE__", &serde_json::to_string(&self.nonce).unwrap())
            .replace("__NEW_PROFILE__", if self.new { "true" } else { "false" });
        let _ = window.eval(script);
    }
    pub fn title(&self, window: &WebviewWindow, title: &str) {
        if self.network.is_some() {
            self.online_title(window, title);
            return;
        }
        let Some(raw) = title.strip_prefix("PTCG-ACCEPTANCE:") else {
            return;
        };
        if raw.len() > 1024 || self.reported.load(Ordering::Acquire) {
            return;
        }
        let Ok(checks) = serde_json::from_str::<Checks>(raw) else {
            return;
        };
        if checks.nonce != self.nonce {
            return;
        }
        let observed = self.observed.lock().unwrap();
        let Some((pid, origin)) = observed.as_ref() else {
            return;
        };
        if self.reported.swap(true, Ordering::AcqRel) {
            return;
        }
        let valid = checks.valid(&self.nonce);
        self.emit(json!({"schema":1,"type":if valid {"gui-ready"} else {"gui-failed"},"nonce":self.nonce,"backendPid":pid,"origin":origin,"checks":{"appRendered":checks.app_rendered,"sameOriginSession":checks.same_origin_session,"offlineBridge":checks.offline_bridge,"cardCatalog":checks.card_catalog,"deckPersistence":checks.deck_persistence}}));
        if !valid {
            window.app_handle().exit(1);
        }
    }
    pub fn control(self: &Arc<Self>, handle: tauri::AppHandle) {
        let owner = self.clone();
        std::thread::spawn(move || {
            loop {
                let value = frame()
                    .ok()
                    .and_then(|raw| serde_json::from_slice::<serde_json::Value>(&raw).ok());
                if value.as_ref()
                    == Some(&json!({"schema":1,"type":"start-online","nonce":owner.nonce}))
                    && owner.network.is_some()
                    && owner.online_phase.load(Ordering::Acquire) == 1
                {
                    if let Some(window) = handle.get_webview_window("main") {
                        if window
                            .eval("window.dispatchEvent(new Event('ptcg-p8d-start'))")
                            .is_ok()
                        {
                            owner.emit(
                                json!({"schema":1,"type":"online-started","nonce":owner.nonce}),
                            );
                        } else {
                            owner.failed();
                            handle.exit(1);
                            break;
                        }
                    } else {
                        owner.failed();
                        handle.exit(1);
                        break;
                    }
                    continue;
                }
                let valid =
                    value == Some(json!({"schema":1,"type":"shutdown","nonce":owner.nonce}));
                handle.exit(if valid { 0 } else { 1 });
                break; // Parent EOF also closes only this owned APP.
            }
        });
    }

    fn online_title(&self, window: &WebviewWindow, title: &str) {
        let Some(raw) = title.strip_prefix("PTCG-P8D:") else {
            return;
        };
        if raw.len() > 2048 {
            return;
        }
        let Ok(value) = serde_json::from_str::<serde_json::Value>(raw) else {
            return;
        };
        if value.get("nonce").and_then(|v| v.as_str()) != Some(&self.nonce) {
            return;
        }
        let observed = self.observed.lock().unwrap();
        let Some((pid, origin)) = observed.as_ref() else {
            return;
        };
        match value.get("stage").and_then(|v| v.as_str()) {
            Some("progress") if (1..=2).contains(&self.online_phase.load(Ordering::Acquire)) => {
                if let Some(phase @ ("deck" | "room" | "commands" | "replay")) =
                    value.get("phase").and_then(|v| v.as_str())
                {
                    self.emit(json!({"schema":1,"type":"online-progress","nonce":self.nonce,"phase":phase}));
                }
            }
            Some("ready")
                if self
                    .online_phase
                    .compare_exchange(0, 1, Ordering::AcqRel, Ordering::Acquire)
                    .is_ok() =>
            {
                self.emit(json!({"schema":1,"type":"online-ready","nonce":self.nonce,"backendPid":pid,"origin":origin}));
            }
            Some("invite") if self.online_phase.load(Ordering::Acquire) == 1 => {
                let Some(code) = value.get("inviteCode").and_then(|v| v.as_str()) else {
                    return;
                };
                if !(4..=12).contains(&code.len())
                    || !code
                        .bytes()
                        .all(|c| c.is_ascii_uppercase() || c.is_ascii_digit())
                {
                    return;
                }
                self.online_phase.store(2, Ordering::Release);
                self.emit(
                    json!({"schema":1,"type":"online-invite","nonce":self.nonce,"inviteCode":code}),
                );
            }
            Some("complete") if self.online_phase.load(Ordering::Acquire) == 2 => {
                let keys = [
                    "nativeLobby",
                    "sharedDeck",
                    "invite",
                    "legalCommands",
                    "naturalEnd",
                    "privateReplay",
                    "replayReadOnly",
                ];
                let Some(checks) = value.get("checks").and_then(|v| v.as_object()) else {
                    return;
                };
                let seq = value.get("commands").and_then(|v| v.as_u64()).unwrap_or(0);
                if checks.len() != keys.len()
                    || !keys
                        .iter()
                        .all(|k| checks.get(*k).and_then(|v| v.as_bool()) == Some(true))
                    || !(1..=2000).contains(&seq)
                {
                    self.failed();
                    window.app_handle().exit(1);
                    return;
                }
                self.online_phase.store(3, Ordering::Release);
                self.emit(json!({"schema":1,"type":"online-complete","nonce":self.nonce,"checks":checks,"commands":seq}));
            }
            Some("failed") => {
                let phase = value
                    .get("phase")
                    .and_then(|v| v.as_str())
                    .unwrap_or("unknown");
                let phase = if ["bootstrap", "deck", "room", "commands", "replay"].contains(&phase)
                {
                    phase
                } else {
                    "unknown"
                };
                self.emit(
                    json!({"schema":1,"type":"online-failed","nonce":self.nonce,"phase":phase}),
                );
                window.app_handle().exit(1);
            }
            _ => {}
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::Path;
    fn request(path: &Path, mode: &str) -> Vec<u8> {
        json!({"schema":1,"nonce":"a".repeat(64),"stateDir":path,"mode":mode})
            .to_string()
            .into_bytes()
    }
    #[test]
    fn operator_mode_refuses_real_or_configured_profiles_and_reuses_only_its_marker() {
        let root = crate::tests::scratch("acceptance");
        assert!(Session::parse(&request(&root, "new")).is_err());
        assert!(Session::parse(&request(&root, "reopen")).is_err());
        let profile = root.join("test");
        let first = Session::parse(&request(&profile, "new")).unwrap();
        fs::create_dir(&profile).unwrap();
        first.mark().unwrap();
        assert!(Session::parse(&request(&profile, "reopen")).is_ok());
        fs::write(profile.join("connection.json"), b"{}").unwrap();
        assert!(Session::parse(&request(&profile, "reopen")).is_err());
        fs::remove_dir_all(root).unwrap();
    }
    #[test]
    fn operator_frames_and_renderer_reports_cannot_add_authority_or_skip_checks() {
        assert!(Session::parse(
            br#"{"schema":1,"nonce":"bad","stateDir":"relative","mode":"new"}"#
        )
        .is_err());
        let valid = Checks {
            nonce: "a".repeat(64),
            app_rendered: true,
            same_origin_session: true,
            offline_bridge: true,
            card_catalog: true,
            deck_persistence: true,
        };
        assert!(valid.valid(&"a".repeat(64)));
        assert!(!valid.valid(&"b".repeat(64)));
        assert!(serde_json::from_value::<Checks>(json!({"nonce":"a".repeat(64),"appRendered":true,"sameOriginSession":true,"offlineBridge":true,"cardCatalog":true,"deckPersistence":true,"cookie":"forbidden"})).is_err());
    }

    #[test]
    fn network_operator_accepts_only_local_tls_and_its_own_trust_file() {
        let root = crate::tests::scratch("network-acceptance");
        let ca = root.join("test-ca.pem");
        fs::write(&ca, "synthetic contract-only CA").unwrap();
        let state = root.join("profile");
        let request = |origin: &str, cert: &Path| {
            json!({"schema":1,"nonce":"a".repeat(64),"stateDir":state,"mode":"new","network":{"origin":origin,"caFile":cert}}).to_string().into_bytes()
        };
        assert!(Session::parse(&request("https://127.0.0.1:28874", &ca)).is_ok());
        for url in [
            "http://127.0.0.1:28874",
            "https://10.0.0.145:8874",
            "https://127.0.0.1:8874",
            "https://localhost:28874",
            "https://user:pass@127.0.0.1:28874",
            "https://127.0.0.1:28874/a",
            "https://127.0.0.1:28874?x=1",
        ] {
            assert!(Session::parse(&request(url, &ca)).is_err());
        }
        assert!(Session::parse(&request(
            "https://127.0.0.1:28874",
            &root.parent().unwrap().join("foreign.pem")
        ))
        .is_err());
        let owner = Session::parse(&request("https://127.0.0.1:28874", &ca)).unwrap();
        fs::create_dir(&state).unwrap();
        owner.mark().unwrap();
        let reopen = json!({"schema":1,"nonce":"a".repeat(64),"stateDir":state,"mode":"reopen","network":{"origin":"https://127.0.0.1:28874","caFile":ca}}).to_string().into_bytes();
        assert!(Session::parse(&reopen).is_ok());
        fs::write(state.join("connection.json"), "{}").unwrap();
        assert!(Session::parse(&reopen).is_err());
        fs::remove_dir_all(root).unwrap();
    }
}
