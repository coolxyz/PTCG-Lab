use serde::Deserialize;
use serde_json::json;
use sha2::{Digest, Sha256};
use std::{
    collections::BTreeMap,
    fs,
    io::{BufRead, BufReader, Write},
    path::{Component, Path},
    process::{Child, ChildStdin, Command, Stdio},
    sync::mpsc,
    thread,
    time::{Duration, Instant},
};

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Manifest {
    schema: u32,
    platform: String,
    python: String,
    files: BTreeMap<String, String>,
}

pub fn verify(root: &Path) -> Result<String, &'static str> {
    if root.is_symlink() || !root.is_dir() {
        return Err("INVALID_BUNDLE");
    }
    let data = fs::read(root.join("manifest.json")).map_err(|_| "INVALID_BUNDLE")?;
    if data.len() > 4 * 1024 * 1024 {
        return Err("INVALID_BUNDLE");
    }
    let manifest: Manifest = serde_json::from_slice(&data).map_err(|_| "INVALID_BUNDLE")?;
    if manifest.schema != 1
        || manifest.platform != "macos-arm64"
        || manifest.files.is_empty()
        || manifest.files.len() > 20000
    {
        return Err("INVALID_BUNDLE");
    }
    if !manifest.files.contains_key(&manifest.python)
        || !manifest.files.contains_key("bootstrap.py")
    {
        return Err("INVALID_BUNDLE");
    }
    let mut pending = vec![root.to_path_buf()];
    let mut actual = 0;
    while let Some(directory) = pending.pop() {
        for item in fs::read_dir(directory).map_err(|_| "INVALID_BUNDLE")? {
            let item = item.map_err(|_| "INVALID_BUNDLE")?;
            let kind = item.file_type().map_err(|_| "INVALID_BUNDLE")?;
            if kind.is_symlink() {
                return Err("INVALID_BUNDLE");
            }
            if kind.is_dir() {
                pending.push(item.path());
            } else if kind.is_file() {
                let path = item.path();
                let name = path
                    .strip_prefix(root)
                    .map_err(|_| "INVALID_BUNDLE")?
                    .to_str()
                    .ok_or("INVALID_BUNDLE")?;
                if name == "manifest.json" {
                    continue;
                }
                if !manifest.files.contains_key(name) {
                    return Err("INVALID_BUNDLE");
                }
                actual += 1;
            } else {
                return Err("INVALID_BUNDLE");
            }
        }
    }
    if actual != manifest.files.len() {
        return Err("INVALID_BUNDLE");
    }
    for (name, digest) in &manifest.files {
        let relative = Path::new(name);
        if relative
            .components()
            .any(|c| !matches!(c, Component::Normal(_)))
            || relative.as_os_str().is_empty()
        {
            return Err("INVALID_BUNDLE");
        }
        let mut path = root.to_path_buf();
        for component in relative.components() {
            path.push(component);
            let info = fs::symlink_metadata(&path).map_err(|_| "INVALID_BUNDLE")?;
            if info.file_type().is_symlink() {
                return Err("INVALID_BUNDLE");
            }
        }
        let content = fs::read(path).map_err(|_| "INVALID_BUNDLE")?;
        if format!("{:x}", Sha256::digest(content)) != *digest {
            return Err("INVALID_BUNDLE");
        }
    }
    Ok(manifest.python)
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Ready {
    schema: u32,
    #[serde(rename = "type")]
    kind: String,
    nonce: String,
    pid: u32,
    pub origin: String,
    pub cookie: String,
}

impl Ready {
    fn validate(&self, nonce: &str, pid: u32) -> Result<(), &'static str> {
        let url = tauri::Url::parse(&self.origin).map_err(|_| "BACKEND_START_FAILED")?;
        if self.schema != 1
            || self.kind != "ready"
            || self.nonce != nonce
            || self.pid != pid
            || url.scheme() != "http"
            || url.host_str() != Some("127.0.0.1")
            || url.port().is_none()
            || url.port() == Some(0)
            || !url.username().is_empty()
            || url.password().is_some()
            || url.path() != "/"
            || url.query().is_some()
            || url.fragment().is_some()
            || self.origin != format!("http://127.0.0.1:{}", url.port().unwrap())
            || self.cookie.len() != 43
            || !self
                .cookie
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_')
        {
            return Err("BACKEND_START_FAILED");
        }
        Ok(())
    }
}

pub struct Backend {
    child: Child,
    input: Option<ChildStdin>,
    nonce: String,
}

impl Backend {
    pub fn start(root: &Path, state: &Path) -> Result<(Self, Ready), &'static str> {
        let python = verify(root)?;
        let mut entropy = [0u8; 32];
        getrandom::fill(&mut entropy).map_err(|_| "BACKEND_START_FAILED")?;
        let nonce: String = entropy.iter().map(|b| format!("{b:02x}")).collect();
        let mut command = Command::new(root.join(python));
        command
            .args(["-I", "-B"])
            .arg(root.join("bootstrap.py"))
            .current_dir(root)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .env_clear();
        for key in ["HOME", "USER", "LANG", "TMPDIR"] {
            if let Some(value) = std::env::var_os(key) {
                command.env(key, value);
            }
        }
        let child = command.spawn().map_err(|_| "BACKEND_START_FAILED")?;
        let mut backend = Self {
            child,
            input: None,
            nonce,
        };
        backend.input = backend.child.stdin.take();
        let config_path = state.join("connection.json");
        let config = if config_path.exists() {
            let info = fs::symlink_metadata(&config_path).map_err(|_| "BACKEND_START_FAILED")?;
            if info.file_type().is_symlink() || info.len() > 16384 {
                return Err("BACKEND_START_FAILED");
            }
            serde_json::from_slice::<serde_json::Value>(
                &fs::read(config_path).map_err(|_| "BACKEND_START_FAILED")?,
            )
            .map_err(|_| "BACKEND_START_FAILED")?
        } else {
            json!({"servers": {}})
        };
        let start = json!({"schema":1,"type":"start","nonce":backend.nonce,"stateDir":state,"config":config});
        writeln!(
            backend.input.as_mut().ok_or("BACKEND_START_FAILED")?,
            "{start}"
        )
        .map_err(|_| "BACKEND_START_FAILED")?;
        let output = backend.child.stdout.take().ok_or("BACKEND_START_FAILED")?;
        let (tx, rx) = mpsc::channel();
        thread::spawn(move || {
            // A bounded read prevents an invalid child from allocating an unlimited line.
            let mut line = Vec::new();
            let mut input = BufReader::new(output);
            while line.len() <= 16384 {
                let buffer = match input.fill_buf() {
                    Ok(b) if !b.is_empty() => b,
                    _ => break,
                };
                let count = buffer
                    .iter()
                    .position(|b| *b == b'\n')
                    .map(|i| i + 1)
                    .unwrap_or(buffer.len());
                if line.len() + count > 16384 {
                    break;
                }
                line.extend_from_slice(&buffer[..count]);
                input.consume(count);
                if line.last() == Some(&b'\n') {
                    let _ = tx.send(line);
                    return;
                }
            }
        });
        let line = rx
            .recv_timeout(Duration::from_secs(35))
            .map_err(|_| "BACKEND_START_FAILED")?;
        let ready: Ready = serde_json::from_slice(&line).map_err(|_| "BACKEND_START_FAILED")?;
        ready.validate(&backend.nonce, backend.child.id())?;
        if backend
            .child
            .try_wait()
            .map_err(|_| "BACKEND_START_FAILED")?
            .is_some()
        {
            return Err("BACKEND_START_FAILED");
        }
        Ok((backend, ready))
    }

    pub fn exited(&mut self) -> bool {
        !matches!(self.child.try_wait(), Ok(None))
    }

    pub fn stop(&mut self) {
        if let Some(mut input) = self.input.take() {
            let _ = writeln!(
                input,
                "{}",
                json!({"schema":1,"type":"shutdown","nonce":self.nonce})
            );
            // Closing the pipe also tells the child to shut down if writing failed.
        }
        let until = Instant::now() + Duration::from_secs(6);
        while Instant::now() < until {
            if self.exited() {
                return;
            }
            thread::sleep(Duration::from_millis(50));
        }
        // Only this owned child is killed. Never search or stop other APP processes.
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

impl Drop for Backend {
    fn drop(&mut self) {
        self.stop();
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn ready_is_bound_to_owned_pid_nonce_and_loopback() {
        let make = |origin: &str| Ready {
            schema: 1,
            kind: "ready".into(),
            nonce: "a".repeat(64),
            pid: 123,
            origin: origin.into(),
            cookie: "a".repeat(43),
        };
        assert!(make("http://127.0.0.1:13456")
            .validate(&"a".repeat(64), 123)
            .is_ok());
        for origin in [
            "http://localhost:13456",
            "https://127.0.0.1:13456",
            "http://127.0.0.1:13456/?x=1",
            "http://evil.example:13456",
            "http://127.0.0.1:13456/",
        ] {
            assert!(make(origin).validate(&"a".repeat(64), 123).is_err());
        }
        assert!(make("http://127.0.0.1:13456")
            .validate(&"b".repeat(64), 123)
            .is_err());
        assert!(make("http://127.0.0.1:13456")
            .validate(&"a".repeat(64), 124)
            .is_err());
    }

    #[test]
    fn payload_rejects_tamper_extras_links_and_path_escape() {
        let root = crate::tests::scratch("manifest");
        fs::write(root.join("python"), b"synthetic interpreter").unwrap();
        fs::write(root.join("bootstrap.py"), b"synthetic entry").unwrap();
        let mut files = BTreeMap::new();
        for name in ["python", "bootstrap.py"] {
            files.insert(
                name.to_string(),
                format!("{:x}", Sha256::digest(fs::read(root.join(name)).unwrap())),
            );
        }
        let write_manifest = |files: &BTreeMap<String, String>| {
            fs::write(
                root.join("manifest.json"),
                json!({"schema":1,"platform":"macos-arm64","python":"python","files":files})
                    .to_string(),
            )
            .unwrap();
        };
        write_manifest(&files);
        assert!(verify(&root).is_ok());
        fs::write(root.join("extra.py"), b"unexpected").unwrap();
        assert!(verify(&root).is_err());
        fs::remove_file(root.join("extra.py")).unwrap();
        fs::write(root.join("python"), b"tamper").unwrap();
        assert!(verify(&root).is_err());
        fs::remove_file(root.join("python")).unwrap();
        std::os::unix::fs::symlink(root.join("bootstrap.py"), root.join("python")).unwrap();
        assert!(verify(&root).is_err());
        files.insert("../escape".into(), "0".repeat(64));
        write_manifest(&files);
        assert!(verify(&root).is_err());
        fs::remove_dir_all(root).unwrap();
    }
}
