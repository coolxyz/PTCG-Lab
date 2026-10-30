//! Atomically born in a non-inheritable kill-on-close Job. No suspended orphan gap.
use super::security::{full, wide, Handle};
use std::{
    collections::BTreeMap,
    ffi::{OsStr, OsString},
    fs::File,
    io,
    mem::{size_of, zeroed},
    os::windows::ffi::OsStrExt,
    path::Path,
    ptr::{null, null_mut},
};
use windows_sys::Win32::{
    Foundation::*,
    Security::SECURITY_ATTRIBUTES,
    Storage::FileSystem::*,
    System::{JobObjects::*, Pipes::CreatePipe, Threading::*},
};

pub type Input = File;
pub type Output = File;
pub struct OwnedChild {
    process: Handle,
    job: Option<Handle>,
    pid: u32,
    input: Option<File>,
    output: Option<File>,
}
struct Attributes {
    storage: Vec<usize>,
}
impl Attributes {
    fn new() -> io::Result<Self> {
        unsafe {
            let mut bytes = 0;
            InitializeProcThreadAttributeList(null_mut(), 2, 0, &mut bytes);
            if bytes == 0 {
                return Err(io::Error::last_os_error());
            }
            let mut storage = vec![0usize; bytes.div_ceil(size_of::<usize>())];
            if InitializeProcThreadAttributeList(storage.as_mut_ptr().cast(), 2, 0, &mut bytes) == 0
            {
                return Err(io::Error::last_os_error());
            }
            Ok(Self { storage })
        }
    }
    fn pointer(&mut self) -> LPPROC_THREAD_ATTRIBUTE_LIST {
        self.storage.as_mut_ptr().cast()
    }
    fn set(
        &mut self,
        kind: usize,
        pointer: *const std::ffi::c_void,
        size: usize,
    ) -> io::Result<()> {
        if unsafe {
            UpdateProcThreadAttribute(self.pointer(), 0, kind, pointer, size, null_mut(), null())
        } == 0
        {
            Err(io::Error::last_os_error())
        } else {
            Ok(())
        }
    }
}
impl Drop for Attributes {
    fn drop(&mut self) {
        unsafe {
            DeleteProcThreadAttributeList(self.pointer());
        }
    }
}

// Windows C argv quoting: preserve Unicode, quotes and trailing backslashes.
fn argument(value: &OsStr) -> io::Result<Vec<u16>> {
    let text: Vec<u16> = value.encode_wide().collect();
    if text.contains(&0) {
        return Err(io::ErrorKind::InvalidInput.into());
    }
    let mut result = vec![34];
    let mut slashes = 0;
    for c in text {
        if c == 92 {
            slashes += 1;
            continue;
        }
        result.extend(std::iter::repeat_n(
            92,
            if c == 34 { 2 * slashes + 1 } else { slashes },
        ));
        result.push(c);
        slashes = 0;
    }
    result.extend(std::iter::repeat_n(92, slashes * 2));
    result.push(34);
    Ok(result)
}

fn pipe() -> io::Result<(Handle, Handle)> {
    unsafe {
        let mut attributes: SECURITY_ATTRIBUTES = zeroed();
        attributes.nLength = size_of::<SECURITY_ATTRIBUTES>() as u32;
        attributes.bInheritHandle = 1;
        let (mut read, mut write) = (null_mut(), null_mut());
        if CreatePipe(&mut read, &mut write, &mut attributes, 0) == 0 {
            return Err(io::Error::last_os_error());
        }
        Ok((
            Handle::new(read).map_err(io::Error::other)?,
            Handle::new(write).map_err(io::Error::other)?,
        ))
    }
}

impl OwnedChild {
    pub fn spawn(executable: &Path, args: &[OsString], cwd: &Path) -> io::Result<Self> {
        let mut env = BTreeMap::new();
        // Do not inherit PATH, Python overrides, proxy credentials, launch secrets
        // or arbitrary application variables. DLLs come from the verified bundle.
        for key in [
            "SystemRoot",
            "WINDIR",
            "USERPROFILE",
            "LOCALAPPDATA",
            "APPDATA",
            "TEMP",
            "TMP",
            "USERNAME",
        ] {
            if let Some(value) = std::env::var_os(key) {
                env.insert(OsString::from(key.to_ascii_uppercase()), value);
            }
        }
        Self::spawn_environment(executable, args, cwd, env)
    }
    fn spawn_environment(
        executable: &Path,
        args: &[OsString],
        cwd: &Path,
        env: BTreeMap<OsString, OsString>,
    ) -> io::Result<Self> {
        unsafe {
            let job = Handle::new(CreateJobObjectW(null(), null())).map_err(io::Error::other)?;
            let mut limits: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = zeroed();
            limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
            if SetInformationJobObject(
                job.raw(),
                JobObjectExtendedLimitInformation,
                (&limits as *const JOBOBJECT_EXTENDED_LIMIT_INFORMATION).cast(),
                size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
            ) == 0
            {
                return Err(io::Error::last_os_error());
            }
            let (child_in, parent_in) = pipe()?;
            let (parent_out, child_out) = pipe()?;
            for parent in [&parent_in, &parent_out] {
                if SetHandleInformation(parent.raw(), HANDLE_FLAG_INHERIT, 0) == 0 {
                    return Err(io::Error::last_os_error());
                }
            }
            let mut inheritable: SECURITY_ATTRIBUTES = zeroed();
            inheritable.nLength = size_of::<SECURITY_ATTRIBUTES>() as u32;
            inheritable.bInheritHandle = 1;
            let stderr = Handle::new(CreateFileW(
                wide("NUL").map_err(io::Error::other)?.as_ptr(),
                GENERIC_WRITE,
                FILE_SHARE_READ | FILE_SHARE_WRITE,
                &inheritable,
                OPEN_EXISTING,
                FILE_ATTRIBUTE_NORMAL,
                null_mut(),
            ))
            .map_err(io::Error::other)?;
            let inherited = [child_in.raw(), child_out.raw(), stderr.raw()];
            let jobs = [job.raw()];
            let mut attributes = Attributes::new()?;
            attributes.set(
                PROC_THREAD_ATTRIBUTE_HANDLE_LIST as usize,
                inherited.as_ptr().cast(),
                size_of_val(&inherited),
            )?;
            attributes.set(
                PROC_THREAD_ATTRIBUTE_JOB_LIST as usize,
                jobs.as_ptr().cast(),
                size_of_val(&jobs),
            )?;
            let executable_wide = full(executable).map_err(io::Error::other)?;
            let directory = full(cwd).map_err(io::Error::other)?;
            let mut command = argument(executable.as_os_str())?;
            for arg in args {
                command.push(32);
                command.extend(argument(arg)?);
            }
            command.push(0);
            if command.len() > 32767 {
                return Err(io::ErrorKind::InvalidInput.into());
            }
            let mut environment = Vec::new();
            for (key, value) in env {
                if key.encode_wide().any(|c| c == 61) {
                    return Err(io::ErrorKind::InvalidInput.into());
                }
                environment.extend(
                    wide(key)
                        .map_err(io::Error::other)?
                        .into_iter()
                        .take_while(|c| *c != 0),
                );
                environment.push(61);
                environment.extend(wide(value).map_err(io::Error::other)?);
            }
            if environment.is_empty() {
                environment.push(0);
            }
            environment.push(0);
            let mut start: STARTUPINFOEXW = zeroed();
            start.StartupInfo.cb = size_of::<STARTUPINFOEXW>() as u32;
            start.StartupInfo.dwFlags = STARTF_USESTDHANDLES;
            start.StartupInfo.hStdInput = child_in.raw();
            start.StartupInfo.hStdOutput = child_out.raw();
            start.StartupInfo.hStdError = stderr.raw();
            start.lpAttributeList = attributes.pointer();
            let mut info: PROCESS_INFORMATION = zeroed();
            if CreateProcessW(
                executable_wide.as_ptr(),
                command.as_mut_ptr(),
                null(),
                null(),
                1,
                CREATE_NO_WINDOW | CREATE_UNICODE_ENVIRONMENT | EXTENDED_STARTUPINFO_PRESENT,
                environment.as_ptr().cast(),
                directory.as_ptr(),
                &start.StartupInfo,
                &mut info,
            ) == 0
            {
                return Err(io::Error::last_os_error());
            }
            let process = Handle::new(info.hProcess).map_err(io::Error::other)?;
            drop(Handle::new(info.hThread).map_err(io::Error::other)?);
            Ok(Self {
                process,
                job: Some(job),
                pid: info.dwProcessId,
                input: Some(parent_in.into_file()),
                output: Some(parent_out.into_file()),
            })
        }
    }
    pub fn id(&self) -> u32 {
        self.pid
    }
    pub fn take_input(&mut self) -> Option<Input> {
        self.input.take()
    }
    pub fn take_output(&mut self) -> Option<Output> {
        self.output.take()
    }
    pub fn exited(&mut self) -> bool {
        unsafe { WaitForSingleObject(self.process.raw(), 0) != WAIT_TIMEOUT }
    }
    pub fn kill(&mut self) {
        if let Some(job) = &self.job {
            unsafe {
                TerminateJobObject(job.raw(), 1);
            }
        }
    }
    pub fn wait(&mut self) {
        unsafe {
            WaitForSingleObject(self.process.raw(), 5000);
        }
    }
    pub fn cleanup(&mut self) {
        self.job.take();
    }
}
impl Drop for OwnedChild {
    fn drop(&mut self) {
        self.cleanup();
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{
        fs,
        process::{Command, Stdio},
        thread,
        time::{Duration, Instant},
    };

    fn wait_file(root: &Path, name: &str) -> u32 {
        let until = Instant::now() + Duration::from_secs(10);
        while Instant::now() < until {
            if let Ok(text) = fs::read_to_string(root.join(name)) {
                if let Ok(pid) = text.parse() {
                    return pid;
                }
            }
            thread::sleep(Duration::from_millis(25));
        }
        panic!("owned helper did not become ready");
    }
    fn running(pid: u32) -> bool {
        unsafe {
            let Ok(handle) = Handle::new(OpenProcess(PROCESS_SYNCHRONIZE, 0, pid)) else {
                return false;
            };
            WaitForSingleObject(handle.raw(), 0) == WAIT_TIMEOUT
        }
    }
    fn wait_dead(pid: u32) {
        let until = Instant::now() + Duration::from_secs(5);
        while running(pid) && Instant::now() < until {
            thread::sleep(Duration::from_millis(25));
        }
        assert!(!running(pid), "owned process survived Job cleanup");
    }
    fn ordinary_helper(root: &Path, name: &str) -> std::process::Child {
        Command::new(std::env::current_exe().unwrap())
            .args(["--exact", name])
            .current_dir(root)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
            .unwrap()
    }
    #[test]
    fn windows_grandchild_helper() {
        let root = std::env::current_dir().unwrap();
        if !root.join("run-job-helper").exists() {
            return;
        }
        fs::write(root.join("grandchild.pid"), std::process::id().to_string()).unwrap();
        loop {
            thread::sleep(Duration::from_secs(1));
        }
    }
    #[test]
    fn windows_job_child_helper() {
        let root = std::env::current_dir().unwrap();
        if !root.join("run-job-helper").exists() {
            return;
        }
        // Descendants must automatically inherit the job; no breakaway is enabled.
        let _descendant =
            ordinary_helper(&root, "platform::process::tests::windows_grandchild_helper");
        fs::write(root.join("child.pid"), std::process::id().to_string()).unwrap();
        loop {
            thread::sleep(Duration::from_secs(1));
        }
    }
    #[test]
    fn windows_job_owner_helper() {
        let root = std::env::current_dir().unwrap();
        if !root.join("run-owner-helper").exists() {
            return;
        }
        let _child = OwnedChild::spawn(
            &std::env::current_exe().unwrap(),
            &[
                "--exact".into(),
                "platform::process::tests::windows_job_child_helper".into(),
            ],
            &root,
        )
        .unwrap();
        wait_file(&root, "grandchild.pid");
        fs::write(root.join("owner.ready"), std::process::id().to_string()).unwrap();
        loop {
            thread::sleep(Duration::from_secs(1));
        }
    }
    #[test]
    fn windows_job_cleanup_kills_descendants_and_preserves_unrelated_process() {
        let root = crate::tests::scratch("job 中文 with spaces");
        fs::write(root.join("run-job-helper"), b"synthetic").unwrap();
        let unrelated_root = crate::tests::scratch("unrelated");
        fs::write(unrelated_root.join("run-job-helper"), b"synthetic").unwrap();
        let mut unrelated = ordinary_helper(
            &unrelated_root,
            "platform::process::tests::windows_grandchild_helper",
        );
        let outcome = std::panic::catch_unwind(|| {
            let mut child = OwnedChild::spawn(
                &std::env::current_exe().unwrap(),
                &[
                    "--exact".into(),
                    "platform::process::tests::windows_job_child_helper".into(),
                ],
                &root,
            )
            .unwrap();
            let pid = wait_file(&root, "child.pid");
            let descendant = wait_file(&root, "grandchild.pid");
            unsafe {
                let mut inside = 0;
                assert_ne!(
                    IsProcessInJob(
                        child.process.raw(),
                        child.job.as_ref().unwrap().raw(),
                        &mut inside
                    ),
                    0
                );
                assert_ne!(inside, 0);
                let mut flags = 0;
                assert_ne!(
                    GetHandleInformation(child.job.as_ref().unwrap().raw(), &mut flags),
                    0
                );
                assert_eq!(flags & HANDLE_FLAG_INHERIT, 0);
            }
            child.kill();
            child.wait();
            child.cleanup();
            wait_dead(pid);
            wait_dead(descendant);
            assert!(running(unrelated.id()));
        });
        unrelated.kill().unwrap();
        unrelated.wait().unwrap();
        fs::remove_dir_all(unrelated_root).unwrap();
        fs::remove_dir_all(root).unwrap();
        outcome.unwrap();
    }
    #[test]
    fn windows_owner_crash_closes_job_and_reaps_process_tree() {
        let root = crate::tests::scratch("owner-crash");
        fs::write(root.join("run-job-helper"), b"synthetic").unwrap();
        fs::write(root.join("run-owner-helper"), b"synthetic").unwrap();
        let mut owner =
            ordinary_helper(&root, "platform::process::tests::windows_job_owner_helper");
        let outcome = std::panic::catch_unwind(|| {
            wait_file(&root, "owner.ready");
            (
                wait_file(&root, "child.pid"),
                wait_file(&root, "grandchild.pid"),
            )
        });
        owner.kill().unwrap();
        owner.wait().unwrap();
        let (child, descendant) = outcome.unwrap();
        wait_dead(child);
        wait_dead(descendant);
        fs::remove_dir_all(root).unwrap();
    }
    #[test]
    fn windows_arguments_preserve_quotes_spaces_and_trailing_slashes() {
        let quote = |s: &str| String::from_utf16(&argument(OsStr::new(s)).unwrap()).unwrap();
        assert_eq!(quote("中文 profile"), "\"中文 profile\"");
        assert_eq!(quote("a\\\"b\\"), "\"a\\\\\\\"b\\\\\"");
        assert_eq!(quote(""), "\"\"");
        assert!(argument(OsStr::new("bad\0arg")).is_err());
    }
}
