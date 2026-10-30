use std::{
    ffi::OsString,
    io,
    path::Path,
    process::{Child, ChildStdin, ChildStdout, Command, Stdio},
};
pub type Input = ChildStdin;
pub type Output = ChildStdout;
pub struct OwnedChild(Child);

impl OwnedChild {
    pub fn spawn(executable: &Path, args: &[OsString], cwd: &Path) -> io::Result<Self> {
        let mut command = Command::new(executable);
        command
            .args(args)
            .current_dir(cwd)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .env_clear();
        for key in ["HOME", "USER", "LANG", "TMPDIR"] {
            if let Some(value) = std::env::var_os(key) {
                command.env(key, value);
            }
        }
        command.spawn().map(Self)
    }
    pub fn id(&self) -> u32 {
        self.0.id()
    }
    pub fn take_input(&mut self) -> Option<Input> {
        self.0.stdin.take()
    }
    pub fn take_output(&mut self) -> Option<Output> {
        self.0.stdout.take()
    }
    pub fn exited(&mut self) -> bool {
        !matches!(self.0.try_wait(), Ok(None))
    }
    pub fn kill(&mut self) {
        let _ = self.0.kill();
    }
    pub fn wait(&mut self) {
        let _ = self.0.wait();
    }
    pub fn cleanup(&mut self) {}
}
