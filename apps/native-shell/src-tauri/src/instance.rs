//! macOS owner-only, bounded focus IPC. No argv, paths or authorization travel here.

use std::{
    fs::{self, File, OpenOptions},
    io::{Read, Write},
    os::unix::{
        fs::{DirBuilderExt, FileTypeExt, MetadataExt, OpenOptionsExt, PermissionsExt},
        io::AsRawFd,
        net::{UnixListener, UnixStream},
    },
    path::{Path, PathBuf},
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc,
    },
    thread,
    time::Duration,
};

pub struct Instance {
    _lock: File,
    socket: PathBuf,
    inode: u64,
    listener: Option<UnixListener>,
    stopped: Arc<AtomicBool>,
    worker: Option<thread::JoinHandle<()>>,
}

impl Instance {
    pub fn acquire(state: &Path) -> Result<Option<Self>, &'static str> {
        if !state.is_absolute() || state.ancestors().any(|p| p.is_symlink()) {
            return Err("UNSAFE_PROFILE");
        }
        if !state.exists() {
            fs::DirBuilder::new()
                .recursive(true)
                .mode(0o700)
                .create(state)
                .map_err(|_| "UNSAFE_PROFILE")?;
        }
        let uid = unsafe { libc::getuid() };
        let info = fs::metadata(state).map_err(|_| "UNSAFE_PROFILE")?;
        if info.uid() != uid || info.mode() & 0o077 != 0 {
            return Err("UNSAFE_PROFILE");
        }
        let lock = OpenOptions::new()
            .read(true)
            .write(true)
            .create(true)
            .mode(0o600)
            .custom_flags(libc::O_NOFOLLOW | libc::O_CLOEXEC)
            .open(state.join("shell.lock"))
            .map_err(|_| "UNSAFE_PROFILE")?;
        let info = lock.metadata().map_err(|_| "UNSAFE_PROFILE")?;
        if !info.is_file() || info.uid() != uid || info.mode() & 0o077 != 0 || info.nlink() != 1 {
            return Err("UNSAFE_PROFILE");
        }
        let socket = state.join("focus.sock");
        if socket.as_os_str().as_encoded_bytes().len() >= 100 {
            return Err("PROFILE_PATH_TOO_LONG");
        }
        if unsafe { libc::flock(lock.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) } != 0 {
            if std::io::Error::last_os_error().kind() != std::io::ErrorKind::WouldBlock {
                return Err("PROFILE_LOCK_FAILED");
            }
            for _ in 0..20 {
                if let Ok(mut peer) = UnixStream::connect(&socket) {
                    if Self::same_user(&peer, uid) {
                        let _ = peer.set_write_timeout(Some(Duration::from_millis(250)));
                        if peer.write_all(b"focus\n").is_ok() {
                            return Ok(None);
                        }
                    }
                }
                thread::sleep(Duration::from_millis(50));
            }
            return Err("INSTANCE_FOCUS_FAILED");
        }
        if let Ok(info) = fs::symlink_metadata(&socket) {
            if !info.file_type().is_socket() || info.uid() != uid || info.mode() & 0o077 != 0 {
                return Err("UNSAFE_PROFILE");
            }
            fs::remove_file(&socket).map_err(|_| "PROFILE_LOCK_FAILED")?;
        }
        let listener = UnixListener::bind(&socket).map_err(|_| "PROFILE_LOCK_FAILED")?;
        fs::set_permissions(&socket, fs::Permissions::from_mode(0o600))
            .map_err(|_| "PROFILE_LOCK_FAILED")?;
        listener
            .set_nonblocking(true)
            .map_err(|_| "PROFILE_LOCK_FAILED")?;
        let inode = fs::metadata(&socket)
            .map_err(|_| "PROFILE_LOCK_FAILED")?
            .ino();
        Ok(Some(Self {
            _lock: lock,
            socket,
            inode,
            listener: Some(listener),
            stopped: Arc::new(AtomicBool::new(false)),
            worker: None,
        }))
    }

    fn same_user(peer: &UnixStream, uid: u32) -> bool {
        let (mut peer_uid, mut peer_gid) = (0, 0);
        unsafe {
            libc::getpeereid(peer.as_raw_fd(), &mut peer_uid, &mut peer_gid) == 0 && peer_uid == uid
        }
    }

    pub fn listen(&mut self, focus: impl Fn() + Send + 'static) {
        let listener = self.listener.take().unwrap();
        let stopped = self.stopped.clone();
        self.worker = Some(thread::spawn(move || {
            let uid = unsafe { libc::getuid() };
            while !stopped.load(Ordering::Relaxed) {
                if let Ok((mut peer, _)) = listener.accept() {
                    let _ = peer.set_read_timeout(Some(Duration::from_millis(250)));
                    let mut message = [0u8; 6];
                    if Self::same_user(&peer, uid)
                        && peer.read_exact(&mut message).is_ok()
                        && message == *b"focus\n"
                    {
                        focus();
                    }
                } else {
                    thread::sleep(Duration::from_millis(50));
                }
            }
        }));
    }
}

impl Drop for Instance {
    fn drop(&mut self) {
        self.stopped.store(true, Ordering::Relaxed);
        if let Some(worker) = self.worker.take() {
            let _ = worker.join();
        }
        if fs::symlink_metadata(&self.socket)
            .is_ok_and(|m| m.file_type().is_socket() && m.ino() == self.inode)
        {
            let _ = fs::remove_file(&self.socket);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn private_singleton_focus_and_stale_socket_recovery() {
        let root = crate::tests::scratch("instance");
        let hits = Arc::new(AtomicBool::new(false));
        let received = hits.clone();
        let mut first = Instance::acquire(&root).unwrap().unwrap();
        first.listen(move || {
            received.store(true, Ordering::SeqCst);
        });
        assert!(Instance::acquire(&root).unwrap().is_none());
        for _ in 0..20 {
            if hits.load(Ordering::SeqCst) {
                break;
            }
            thread::sleep(Duration::from_millis(50));
        }
        assert!(hits.load(Ordering::SeqCst));
        drop(first);
        assert!(!root.join("focus.sock").exists());
        let stale = UnixListener::bind(root.join("focus.sock")).unwrap();
        fs::set_permissions(root.join("focus.sock"), fs::Permissions::from_mode(0o600)).unwrap();
        drop(stale);
        drop(Instance::acquire(&root).unwrap().unwrap());
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn unsafe_lock_and_socket_paths_are_never_replaced() {
        let root = crate::tests::scratch("unsafe");
        fs::write(root.join("focus.sock"), b"sentinel").unwrap();
        assert!(Instance::acquire(&root).is_err());
        assert_eq!(fs::read(root.join("focus.sock")).unwrap(), b"sentinel");
        fs::remove_file(root.join("focus.sock")).unwrap();
        fs::hard_link(root.join("shell.lock"), root.join("linked")).unwrap();
        assert!(Instance::acquire(&root).is_err());
        fs::remove_dir_all(root).unwrap();
    }
}
