//! One encrypted, server/trust-bound owner. No plaintext fallback or identity reset.
use super::error::{Error, Result};
use aes_gcm::{
    aead::{Aead, Payload},
    Aes256Gcm, KeyInit, Nonce,
};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::{
    collections::BTreeMap,
    fs::{self, File, OpenOptions},
    io::Write,
    path::{Path, PathBuf},
};
use zeroize::Zeroizing;

const MAX: usize = 16 * 1024 * 1024;
const MAGIC: &[u8] = b"PTCGIOS2\0";
#[derive(Clone, Default, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct State {
    pub credentials: Option<Value>,
    pub refresh_pending: bool,
    pub guest_pending: bool,
    pub controllers: BTreeMap<String, String>,
    pub pending: BTreeMap<String, Value>,
    pub control_pending: BTreeMap<String, Value>,
    pub snapshots: BTreeMap<String, Value>,
    pub history: BTreeMap<String, Value>,
    pub lobby: BTreeMap<String, Value>,
}
pub trait Journal: Send {
    fn state(&self) -> &State;
    fn save(&mut self, next: State) -> Result<()>;
}
pub trait Keys {
    fn key(&self, account: &str, existing: bool) -> Result<Zeroizing<[u8; 32]>>;
}
pub struct SystemKeys;

#[cfg(any(target_os = "ios", target_os = "macos"))]
impl Keys for SystemKeys {
    #[allow(deprecated)] // Pinned crate exposes the query needed for create-only SecItemAdd.
    fn key(&self, account: &str, existing: bool) -> Result<Zeroizing<[u8; 32]>> {
        use core_foundation::{
            base::TCFType, data::CFData, dictionary::CFDictionary, string::CFString,
        };
        use security_framework::{
            access_control::{ProtectionMode, SecAccessControl},
            passwords::{generic_password, PasswordOptions},
        };
        use security_framework_sys::{item::kSecValueData, keychain_item::SecItemAdd};
        const SERVICE: &str = "org.ptcglab.mobile.preview.online.aes";
        let options = || {
            let mut o = PasswordOptions::new_generic_password(SERVICE, account);
            o.set_access_synchronized(Some(false));
            o
        };
        let decode = |bytes: Vec<u8>| -> Result<Zeroizing<[u8; 32]>> {
            let bytes = Zeroizing::new(bytes);
            let key = <[u8; 32]>::try_from(bytes.as_slice())
                .map_err(|_| Error::new("CREDENTIAL_KEY_UNAVAILABLE"))?;
            Ok(Zeroizing::new(key))
        };
        match generic_password(options()) {
            Ok(bytes) => decode(bytes),
            Err(error) if error.code() == -25300 && !existing => {
                let mut key = Zeroizing::new([0; 32]);
                getrandom::fill(key.as_mut()).map_err(|_| Error::new("RANDOM_UNAVAILABLE"))?;
                let mut options = options();
                options.set_access_control(
                    SecAccessControl::create_with_protection(
                        Some(ProtectionMode::AccessibleWhenUnlockedThisDeviceOnly),
                        0,
                    )
                    .map_err(|_| Error::new("CREDENTIAL_BACKEND_UNAVAILABLE"))?,
                );
                unsafe {
                    options.query.push((
                        CFString::wrap_under_get_rule(kSecValueData),
                        CFData::from_buffer(key.as_ref()).into_CFType(),
                    ));
                }
                let dictionary = CFDictionary::from_CFType_pairs(&options.query);
                // SecItemAdd is deliberately create-only; a concurrent creator must
                // never replace the key protecting an existing journal.
                let status =
                    unsafe { SecItemAdd(dictionary.as_concrete_TypeRef(), std::ptr::null_mut()) };
                if status == 0 {
                    Ok(key)
                } else if status == -25299 {
                    decode(
                        generic_password(options_for_read(SERVICE, account))
                            .map_err(|_| Error::new("CREDENTIAL_KEY_UNAVAILABLE"))?,
                    )
                } else {
                    Err(Error::new("CREDENTIAL_BACKEND_UNAVAILABLE"))
                }
            }
            Err(error) if error.code() == -25300 => Err(Error::new("CREDENTIAL_KEY_UNAVAILABLE")),
            Err(_) => Err(Error::new("CREDENTIAL_BACKEND_UNAVAILABLE")),
        }
    }
}
#[cfg(any(target_os = "ios", target_os = "macos"))]
fn options_for_read(
    service: &str,
    account: &str,
) -> security_framework::passwords::PasswordOptions {
    let mut value =
        security_framework::passwords::PasswordOptions::new_generic_password(service, account);
    value.set_access_synchronized(Some(false));
    value
}
#[cfg(not(any(target_os = "ios", target_os = "macos")))]
impl Keys for SystemKeys {
    fn key(&self, _: &str, _: bool) -> Result<Zeroizing<[u8; 32]>> {
        Err(Error::new("CREDENTIAL_BACKEND_UNAVAILABLE"))
    }
}

pub struct Vault {
    state: State,
    path: PathBuf,
    key: Zeroizing<[u8; 32]>,
    binding: String,
    _lock: File,
    poisoned: bool,
}
impl Vault {
    pub fn open(root: &Path, binding: &str, keys: &dyn Keys) -> Result<Self> {
        if binding.len() != 64 || !binding.bytes().all(|c| c.is_ascii_hexdigit()) {
            return Err(Error::new("INVALID_SERVER"));
        }
        private_dir(root)?;
        let lock_path = root.join(format!("{binding}.lock"));
        safe(&lock_path)?;
        let mut options = OpenOptions::new();
        options.create(true).read(true).write(true).truncate(false);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600);
        }
        let lock = options.open(lock_path)?;
        lock.try_lock().map_err(|_| Error::new("PROFILE_IN_USE"))?;
        let path = root.join(format!("{binding}.sealed"));
        safe(&path)?;
        if !path.exists()
            && fs::read_dir(root)?.any(|e| {
                e.is_ok_and(|e| {
                    e.file_name()
                        .to_string_lossy()
                        .starts_with(&format!("{binding}.tmp-"))
                })
            })
        {
            return Err(Error::new("JOURNAL_RECOVERY_REQUIRED"));
        }
        let key = keys.key(binding, path.exists())?;
        let state = if path.exists() {
            let raw = fs::read(&path)?;
            if raw.len() > MAX || raw.len() < MAGIC.len() + 12 + 16 || !raw.starts_with(MAGIC) {
                return Err(Error::new("JOURNAL_CORRUPT"));
            }
            let cipher = Aes256Gcm::new_from_slice(key.as_ref())
                .map_err(|_| Error::new("CREDENTIAL_KEY_UNAVAILABLE"))?;
            let clear = Zeroizing::new(
                cipher
                    .decrypt(
                        Nonce::from_slice(&raw[MAGIC.len()..MAGIC.len() + 12]),
                        Payload {
                            msg: &raw[MAGIC.len() + 12..],
                            aad: binding.as_bytes(),
                        },
                    )
                    .map_err(|_| Error::new("JOURNAL_CORRUPT"))?,
            );
            serde_json::from_slice::<State>(&clear).map_err(|_| Error::new("JOURNAL_CORRUPT"))?
        } else {
            State::default()
        };
        let mut vault = Self {
            state,
            path,
            key,
            binding: binding.into(),
            _lock: lock,
            poisoned: false,
        };
        if !vault.path.exists() {
            vault.save(State::default())?;
        }
        Ok(vault)
    }
}
impl Journal for Vault {
    fn state(&self) -> &State {
        &self.state
    }
    fn save(&mut self, next: State) -> Result<()> {
        if self.poisoned {
            return Err(Error::new("JOURNAL_UNAVAILABLE"));
        }
        let clear = Zeroizing::new(serde_json::to_vec(&next)?);
        if clear.len() > MAX - 128 {
            return Err(Error::new("STORAGE_CAPACITY"));
        }
        let mut nonce = [0u8; 12];
        getrandom::fill(&mut nonce).map_err(|_| Error::new("RANDOM_UNAVAILABLE"))?;
        let cipher = Aes256Gcm::new_from_slice(self.key.as_ref())
            .map_err(|_| Error::new("CREDENTIAL_KEY_UNAVAILABLE"))?;
        let encrypted = cipher
            .encrypt(
                Nonce::from_slice(&nonce),
                Payload {
                    msg: &clear,
                    aad: self.binding.as_bytes(),
                },
            )
            .map_err(|_| Error::new("JOURNAL_UNAVAILABLE"))?;
        let temporary = self.path.parent().unwrap().join(format!(
            "{}.tmp-{}",
            self.binding,
            crate::catalog::id()?
        ));
        let result = (|| -> Result<()> {
            safe(&self.path)?;
            let mut options = OpenOptions::new();
            options.write(true).create_new(true);
            #[cfg(unix)]
            {
                use std::os::unix::fs::OpenOptionsExt;
                options.mode(0o600);
            }
            let mut stream = options.open(&temporary)?;
            crate::protection::secure_file(&temporary)?;
            stream.write_all(MAGIC)?;
            stream.write_all(&nonce)?;
            stream.write_all(&encrypted)?;
            stream.sync_all()?;
            fs::rename(&temporary, &self.path)?;
            self.poisoned = true;
            File::open(self.path.parent().unwrap())?.sync_all()?;
            self.poisoned = false;
            Ok(())
        })();
        let _ = fs::remove_file(temporary);
        result?;
        self.state = next;
        Ok(())
    }
}
fn private_dir(root: &Path) -> Result<()> {
    if !root.exists() {
        let mut builder = fs::DirBuilder::new();
        builder.recursive(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::DirBuilderExt;
            builder.mode(0o700);
        }
        builder.create(root)?;
    }
    if root.ancestors().any(|p| p.is_symlink()) {
        return Err(Error::new("UNSAFE_STORAGE"));
    }
    Ok(())
}
fn safe(path: &Path) -> Result<()> {
    if let Ok(info) = fs::symlink_metadata(path) {
        if !info.is_file() || info.file_type().is_symlink() {
            return Err(Error::new("UNSAFE_STORAGE"));
        }
        #[cfg(unix)]
        {
            use std::os::unix::fs::{MetadataExt, PermissionsExt};
            if info.nlink() != 1 || info.permissions().mode() & 0o077 != 0 {
                return Err(Error::new("UNSAFE_STORAGE"));
            }
        }
        if info.len() > MAX as u64 {
            return Err(Error::new("JOURNAL_CORRUPT"));
        }
    }
    Ok(())
}
