//! Protected local message pipe. The entire protocol is focus\n -> ok\n.
use super::security::{wide, Handle, PrivateDirectory, Security};
use sha2::{Digest, Sha256};
use std::{
    mem::zeroed,
    path::Path,
    ptr::{null, null_mut},
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc,
    },
    thread,
    time::Duration,
};
use windows_sys::Win32::{
    Foundation::*,
    Storage::FileSystem::*,
    System::{Pipes::*, Threading::*, IO::*},
};

pub struct Instance {
    _directory: PrivateDirectory,
    _lock: Handle,
    pipe: Option<Handle>,
    stopped: Arc<AtomicBool>,
    worker: Option<thread::JoinHandle<()>>,
}

fn pipe_name(state: &Path, security: &Security) -> Result<Vec<u16>, &'static str> {
    let state = state.to_str().ok_or("UNSAFE_PROFILE")?.to_lowercase();
    let identity = format!("{}\0{}\0{state}", security.user, security.session);
    wide(format!(
        "\\\\.\\pipe\\PTCGLab.focus.{:x}",
        Sha256::digest(identity)
    ))
}

// Cancellation is drained before stack buffers/OVERLAPPED/event are destroyed.
fn operation(
    pipe: &Handle,
    timeout: u32,
    call: impl FnOnce(*mut OVERLAPPED) -> i32,
) -> Result<u32, &'static str> {
    unsafe {
        let event = Handle::new(CreateEventW(null(), 1, 0, null()))?;
        let mut overlap: OVERLAPPED = zeroed();
        overlap.hEvent = event.raw();
        let result = call(&mut overlap);
        let error = if result == 0 { GetLastError() } else { 0 };
        if error == ERROR_PIPE_CONNECTED {
            return Ok(0);
        }
        if result == 0 && error != ERROR_IO_PENDING {
            return Err("INSTANCE_FOCUS_FAILED");
        }
        if error == ERROR_IO_PENDING && WaitForSingleObject(event.raw(), timeout) != WAIT_OBJECT_0 {
            CancelIoEx(pipe.raw(), &overlap);
            let mut transferred = 0;
            GetOverlappedResult(pipe.raw(), &overlap, &mut transferred, 1);
            return Err("INSTANCE_FOCUS_FAILED");
        }
        let mut transferred = 0;
        if GetOverlappedResult(pipe.raw(), &overlap, &mut transferred, 1) == 0 {
            return Err("INSTANCE_FOCUS_FAILED");
        }
        Ok(transferred)
    }
}

fn focus(name: &[u16], security: &Security) -> Result<(), &'static str> {
    for _ in 0..40 {
        unsafe {
            let raw = CreateFileW(
                name.as_ptr(),
                FILE_READ_DATA
                    | FILE_WRITE_DATA
                    | FILE_WRITE_ATTRIBUTES
                    | READ_CONTROL
                    | SYNCHRONIZE,
                0,
                null(),
                OPEN_EXISTING,
                FILE_FLAG_OVERLAPPED | SECURITY_SQOS_PRESENT | SECURITY_IDENTIFICATION,
                null_mut(),
            );
            if raw == INVALID_HANDLE_VALUE {
                if !matches!(GetLastError(), ERROR_PIPE_BUSY | ERROR_FILE_NOT_FOUND) {
                    return Err("INSTANCE_FOCUS_FAILED");
                }
                thread::sleep(Duration::from_millis(50));
                continue;
            }
            let pipe = Handle::new(raw)?;
            security.validate(pipe.raw())?;
            let mut pid = 0;
            if GetNamedPipeServerProcessId(pipe.raw(), &mut pid) == 0 || !security.same_peer(pid) {
                return Err("INSTANCE_FOCUS_FAILED");
            }
            let mode = PIPE_READMODE_MESSAGE;
            if SetNamedPipeHandleState(pipe.raw(), &mode, null(), null()) == 0 {
                return Err("INSTANCE_FOCUS_FAILED");
            }
            let request = b"focus\n";
            if operation(&pipe, 250, |o| {
                WriteFile(
                    pipe.raw(),
                    request.as_ptr(),
                    request.len() as u32,
                    null_mut(),
                    o,
                )
            })? != 6
            {
                return Err("INSTANCE_FOCUS_FAILED");
            }
            let mut reply = [0u8; 4];
            let count = operation(&pipe, 250, |o| {
                ReadFile(
                    pipe.raw(),
                    reply.as_mut_ptr(),
                    reply.len() as u32,
                    null_mut(),
                    o,
                )
            })?;
            return if count == 3 && &reply[..3] == b"ok\n" {
                Ok(())
            } else {
                Err("INSTANCE_FOCUS_FAILED")
            };
        }
    }
    Err("INSTANCE_FOCUS_FAILED")
}

impl Instance {
    pub fn acquire(state: &Path) -> Result<Option<Self>, &'static str> {
        let directory = PrivateDirectory::open(state)?;
        let name = pipe_name(state, &directory.security)?;
        let lock = match directory.lock() {
            Ok(lock) => lock,
            Err("PROFILE_IN_USE") => {
                focus(&name, &directory.security)?;
                return Ok(None);
            }
            Err(code) => return Err(code),
        };
        let attributes = directory.security.attributes(false);
        let pipe = unsafe {
            Handle::new(CreateNamedPipeW(
                name.as_ptr(),
                PIPE_ACCESS_DUPLEX | FILE_FLAG_OVERLAPPED | FILE_FLAG_FIRST_PIPE_INSTANCE,
                PIPE_TYPE_MESSAGE | PIPE_READMODE_MESSAGE | PIPE_WAIT | PIPE_REJECT_REMOTE_CLIENTS,
                1,
                32,
                32,
                250,
                &attributes,
            ))
        }
        .map_err(|_| "PROFILE_LOCK_FAILED")?;
        directory.security.validate(pipe.raw())?;
        Ok(Some(Self {
            _directory: directory,
            _lock: lock,
            pipe: Some(pipe),
            stopped: Arc::new(AtomicBool::new(false)),
            worker: None,
        }))
    }
    pub fn listen(&mut self, focus: impl Fn() + Send + 'static) {
        let Some(pipe) = self.pipe.take() else {
            return;
        };
        let stopped = self.stopped.clone();
        self.worker = Some(thread::spawn(move || {
            let Ok(security) = Security::current() else {
                return;
            };
            while !stopped.load(Ordering::Acquire) {
                let connected =
                    operation(&pipe, 50, |o| unsafe { ConnectNamedPipe(pipe.raw(), o) });
                if connected.is_ok() {
                    unsafe {
                        let mut pid = 0;
                        if GetNamedPipeClientProcessId(pipe.raw(), &mut pid) != 0
                            && security.same_peer(pid)
                        {
                            let mut message = [0u8; 7];
                            if operation(&pipe, 250, |o| {
                                ReadFile(
                                    pipe.raw(),
                                    message.as_mut_ptr(),
                                    message.len() as u32,
                                    null_mut(),
                                    o,
                                )
                            })
                            .is_ok_and(|n| n == 6 && &message[..6] == b"focus\n")
                            {
                                focus();
                                let reply = b"ok\n";
                                if operation(&pipe, 250, |o| {
                                    WriteFile(
                                        pipe.raw(),
                                        reply.as_ptr(),
                                        reply.len() as u32,
                                        null_mut(),
                                        o,
                                    )
                                })
                                .is_ok()
                                {
                                    // Wait for peer EOF after it reads the ACK. Disconnect
                                    // would otherwise discard an ACK still in the pipe.
                                    let mut tail = [0u8; 1];
                                    let _ = operation(&pipe, 250, |o| {
                                        ReadFile(pipe.raw(), tail.as_mut_ptr(), 1, null_mut(), o)
                                    });
                                }
                            }
                        }
                    }
                }
                unsafe {
                    DisconnectNamedPipe(pipe.raw());
                }
            }
        }));
    }
}
impl Drop for Instance {
    fn drop(&mut self) {
        self.stopped.store(true, Ordering::Release);
        if let Some(worker) = self.worker.take() {
            let _ = worker.join();
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{
        fs,
        process::{Command, Stdio},
    };
    #[test]
    fn windows_instance_owner_helper() {
        let root = std::env::current_dir().unwrap();
        if !root.join("run-instance-helper").is_file() {
            return;
        }
        let mut owner = Instance::acquire(&root).unwrap().unwrap();
        let target = root.join("focused");
        owner.listen(move || {
            fs::write(&target, b"focused").unwrap();
        });
        fs::write(root.join("ready"), b"ready").unwrap();
        loop {
            thread::sleep(Duration::from_millis(50));
        }
    }
    #[test]
    fn windows_focus_competition_and_killed_owner_recovery() {
        let root = crate::tests::scratch("instance");
        fs::write(root.join("run-instance-helper"), b"synthetic").unwrap();
        let mut child = Command::new(std::env::current_exe().unwrap())
            .args([
                "--exact",
                "platform::instance::tests::windows_instance_owner_helper",
            ])
            .current_dir(&root)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
            .unwrap();
        let outcome = std::panic::catch_unwind(|| {
            for _ in 0..100 {
                if root.join("ready").exists() {
                    break;
                }
                thread::sleep(Duration::from_millis(50));
            }
            assert!(root.join("ready").exists());
            assert!(Instance::acquire(&root).unwrap().is_none());
            assert_eq!(fs::read(root.join("focused")).unwrap(), b"focused");
        });
        child.kill().unwrap();
        child.wait().unwrap();
        outcome.unwrap();
        drop(Instance::acquire(&root).unwrap().unwrap());
        fs::remove_dir_all(root).unwrap();
    }
    #[test]
    fn windows_malformed_or_idle_focus_peer_cannot_block_drop() {
        let root = crate::tests::scratch("focus-timeout");
        let mut owner = Instance::acquire(&root).unwrap().unwrap();
        let hits = Arc::new(AtomicBool::new(false));
        let received = hits.clone();
        owner.listen(move || {
            received.store(true, Ordering::Release);
        });
        let security = Security::current().unwrap();
        let name = pipe_name(&root, &security).unwrap();
        let open = || unsafe {
            Handle::new(CreateFileW(
                name.as_ptr(),
                FILE_READ_DATA | FILE_WRITE_DATA | SYNCHRONIZE,
                0,
                null(),
                OPEN_EXISTING,
                FILE_FLAG_OVERLAPPED,
                null_mut(),
            ))
            .unwrap()
        };
        let idle = open();
        thread::sleep(Duration::from_millis(400));
        drop(idle);
        let bad = open();
        let value = b"focus\nextra";
        let _ = operation(&bad, 250, |o| unsafe {
            WriteFile(bad.raw(), value.as_ptr(), value.len() as u32, null_mut(), o)
        });
        thread::sleep(Duration::from_millis(400));
        drop(bad);
        assert!(!hits.load(Ordering::Acquire));
        assert!(Instance::acquire(&root).unwrap().is_none());
        assert!(hits.load(Ordering::Acquire));
        let start = std::time::Instant::now();
        drop(owner);
        assert!(start.elapsed() < Duration::from_secs(2));
        fs::remove_dir_all(root).unwrap();
    }
}
