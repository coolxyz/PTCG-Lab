use ptcg_mobile_lib::{
    catalog::id,
    online::{
        error::{Error, Result},
        vault::{Journal, Keys, Vault},
    },
};
use serde_json::json;
use std::{
    path::PathBuf,
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc,
    },
};
use zeroize::Zeroizing;
struct Scratch(PathBuf);
impl Scratch {
    fn new() -> Self {
        Self(
            std::env::temp_dir()
                .canonicalize()
                .unwrap()
                .join(format!("ptcg-ios-vault-{}", id().unwrap())),
        )
    }
}
impl Drop for Scratch {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}
struct TestKeys {
    missing: Arc<AtomicBool>,
}
impl Keys for TestKeys {
    fn key(&self, _: &str, existing: bool) -> Result<Zeroizing<[u8; 32]>> {
        if existing && self.missing.load(Ordering::Acquire) {
            return Err(Error::new("CREDENTIAL_KEY_UNAVAILABLE"));
        }
        Ok(Zeroizing::new([7; 32]))
    }
}

#[test]
fn encrypted_journal_reopens_without_plaintext_tokens_or_private_frames() {
    let root = Scratch::new();
    let key = "a".repeat(64);
    let missing = Arc::new(AtomicBool::new(false));
    let keys = TestKeys { missing };
    let mut vault = Vault::open(&root.0, &key, &keys).unwrap();
    let mut next = vault.state().clone();
    next.credentials = Some(
        json!({"accessToken":"synthetic-secret-marker","refreshToken":"another-secret-marker"}),
    );
    next.pending
        .insert("mid".into(), json!({"body":{"commandId":"original-id"}}));
    vault.save(next).unwrap();
    drop(vault);
    let raw = std::fs::read(root.0.join(format!("{key}.sealed"))).unwrap();
    assert!(!raw
        .windows(b"secret-marker".len())
        .any(|w| w == b"secret-marker"));
    let reopened = Vault::open(&root.0, &key, &keys).unwrap();
    assert_eq!(
        reopened.state().pending["mid"]["body"]["commandId"],
        "original-id"
    );
}
#[test]
fn missing_key_or_corrupt_ciphertext_never_recreates_the_journal() {
    let root = Scratch::new();
    let key = "b".repeat(64);
    let missing = Arc::new(AtomicBool::new(false));
    let keys = TestKeys {
        missing: missing.clone(),
    };
    drop(Vault::open(&root.0, &key, &keys).unwrap());
    let path = root.0.join(format!("{key}.sealed"));
    let original = std::fs::read(&path).unwrap();
    missing.store(true, Ordering::Release);
    assert_eq!(
        Vault::open(&root.0, &key, &keys).err().unwrap().code,
        "CREDENTIAL_KEY_UNAVAILABLE"
    );
    assert_eq!(std::fs::read(&path).unwrap(), original);
    missing.store(false, Ordering::Release);
    let mut corrupt = original;
    *corrupt.last_mut().unwrap() ^= 1;
    std::fs::write(&path, &corrupt).unwrap();
    assert_eq!(
        Vault::open(&root.0, &key, &keys).err().unwrap().code,
        "JOURNAL_CORRUPT"
    );
    assert_eq!(std::fs::read(path).unwrap(), corrupt);
}
#[test]
fn origin_and_trust_binding_cannot_be_relabelled() {
    let root = Scratch::new();
    let a = "a".repeat(64);
    let b = "b".repeat(64);
    let keys = TestKeys {
        missing: Arc::new(AtomicBool::new(false)),
    };
    drop(Vault::open(&root.0, &a, &keys).unwrap());
    std::fs::copy(
        root.0.join(format!("{a}.sealed")),
        root.0.join(format!("{b}.sealed")),
    )
    .unwrap();
    assert_eq!(
        Vault::open(&root.0, &b, &keys).err().unwrap().code,
        "JOURNAL_CORRUPT"
    );
}
#[test]
fn another_writer_and_unfinished_initialization_are_refused() {
    let root = Scratch::new();
    let key = "c".repeat(64);
    let keys = TestKeys {
        missing: Arc::new(AtomicBool::new(false)),
    };
    let vault = Vault::open(&root.0, &key, &keys).unwrap();
    assert_eq!(
        Vault::open(&root.0, &key, &keys).err().unwrap().code,
        "PROFILE_IN_USE"
    );
    drop(vault);
    std::fs::remove_file(root.0.join(format!("{key}.sealed"))).unwrap();
    std::fs::write(
        root.0.join(format!("{key}.tmp-interrupted")),
        b"encrypted candidate",
    )
    .unwrap();
    assert_eq!(
        Vault::open(&root.0, &key, &keys).err().unwrap().code,
        "JOURNAL_RECOVERY_REQUIRED"
    );
}
#[test]
fn every_save_uses_a_fresh_nonce() {
    let root = Scratch::new();
    let key = "d".repeat(64);
    let keys = TestKeys {
        missing: Arc::new(AtomicBool::new(false)),
    };
    let mut vault = Vault::open(&root.0, &key, &keys).unwrap();
    let path = root.0.join(format!("{key}.sealed"));
    let first = std::fs::read(&path).unwrap();
    vault.save(vault.state().clone()).unwrap();
    let second = std::fs::read(&path).unwrap();
    assert_ne!(first, second);
}
#[test]
fn only_https_origins_and_public_CA_material_are_accepted() {
    use ptcg_mobile_lib::online::transport::Server;
    for origin in [
        "http://localhost:1234",
        "https://example.com/",
        "https://user:pass@example.com",
        "https://example.com/path",
        "https://example.com?token=x",
        "https://example.com:443",
        "https://EXAMPLE.COM",
    ] {
        assert!(Server {
            origin: origin.into(),
            ca_pem: String::new()
        }
        .validate()
        .is_err());
    }
    assert!(Server {
        origin: "https://example.com".into(),
        ca_pem: format!("-----BEGIN {}-----", "PRIVATE KEY")
    }
    .validate()
    .is_err());
    assert!(Server {
        origin: "https://192.0.2.1:8874".into(),
        ca_pem: String::new()
    }
    .validate()
    .is_ok());
    let a = Server {
        origin: "https://example.com".into(),
        ca_pem: String::new(),
    };
    let b = Server {
        origin: "https://example.org".into(),
        ca_pem: String::new(),
    };
    assert_ne!(a.key(), b.key());
}
