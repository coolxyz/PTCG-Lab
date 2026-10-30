//! Same-user protected NTFS storage. All handles are non-inheritable.
use std::{
    ffi::OsStr,
    mem::{size_of, zeroed},
    os::windows::{
        ffi::OsStrExt,
        io::{AsRawHandle, FromRawHandle, OwnedHandle},
    },
    path::{Component, Path, PathBuf, Prefix},
    ptr::{null, null_mut},
};
use windows_sys::Win32::{
    Foundation::*,
    Security::{Authorization::*, *},
    Storage::FileSystem::*,
    System::{RemoteDesktop::ProcessIdToSessionId, Threading::*},
};

pub struct Handle(OwnedHandle);
impl Handle {
    pub unsafe fn new(raw: HANDLE) -> Result<Self, &'static str> {
        if raw.is_null() || raw == INVALID_HANDLE_VALUE {
            Err("NATIVE_OS_FAILED")
        } else {
            Ok(Self(OwnedHandle::from_raw_handle(raw)))
        }
    }
    pub fn raw(&self) -> HANDLE {
        self.0.as_raw_handle()
    }
    pub fn into_file(self) -> std::fs::File {
        self.0.into()
    }
}

pub fn wide(value: impl AsRef<OsStr>) -> Result<Vec<u16>, &'static str> {
    let mut value: Vec<u16> = value.as_ref().encode_wide().collect();
    if value.contains(&0) {
        return Err("UNSAFE_PROFILE");
    }
    value.push(0);
    Ok(value)
}

struct Local(PSECURITY_DESCRIPTOR);
// This uniquely owned LocalAlloc allocation has no thread affinity or aliases.
unsafe impl Send for Local {}
impl Drop for Local {
    fn drop(&mut self) {
        unsafe {
            LocalFree(self.0);
        }
    }
}

fn sid_string(sid: PSID) -> Result<String, &'static str> {
    let mut text = null_mut();
    unsafe {
        if ConvertSidToStringSidW(sid, &mut text) == 0 {
            return Err("UNSAFE_PROFILE");
        }
        let allocation = Local(text.cast());
        let mut len = 0;
        while *text.add(len) != 0 {
            len += 1;
        }
        let result =
            String::from_utf16(std::slice::from_raw_parts(text, len)).map_err(|_| "UNSAFE_PROFILE");
        drop(allocation);
        result
    }
}

fn process_user(process: HANDLE) -> Result<String, &'static str> {
    unsafe {
        let mut token = null_mut();
        if OpenProcessToken(process, TOKEN_QUERY, &mut token) == 0 {
            return Err("UNSAFE_PROFILE");
        }
        let token = Handle::new(token)?;
        let mut bytes = 0;
        GetTokenInformation(token.raw(), TokenUser, null_mut(), 0, &mut bytes);
        if bytes == 0 || bytes > 65536 {
            return Err("UNSAFE_PROFILE");
        }
        let mut aligned = vec![0usize; (bytes as usize).div_ceil(size_of::<usize>())];
        if GetTokenInformation(
            token.raw(),
            TokenUser,
            aligned.as_mut_ptr().cast(),
            bytes,
            &mut bytes,
        ) == 0
        {
            return Err("UNSAFE_PROFILE");
        }
        let user = &*(aligned.as_ptr().cast::<TOKEN_USER>());
        sid_string(user.User.Sid)
    }
}

pub struct Security {
    descriptor: Local,
    pub user: String,
    pub session: u32,
}
impl Security {
    pub fn current() -> Result<Self, &'static str> {
        unsafe {
            let user = process_user(GetCurrentProcess())?;
            let mut session = 0;
            if ProcessIdToSessionId(GetCurrentProcessId(), &mut session) == 0 {
                return Err("UNSAFE_PROFILE");
            }
            let sddl = wide(format!("O:{user}D:P(A;OICI;FA;;;{user})(A;OICI;FA;;;SY)"))?;
            let mut descriptor = null_mut();
            if ConvertStringSecurityDescriptorToSecurityDescriptorW(
                sddl.as_ptr(),
                1,
                &mut descriptor,
                null_mut(),
            ) == 0
            {
                return Err("UNSAFE_PROFILE");
            }
            Ok(Self {
                descriptor: Local(descriptor),
                user,
                session,
            })
        }
    }
    pub fn attributes(&self, inherit: bool) -> SECURITY_ATTRIBUTES {
        SECURITY_ATTRIBUTES {
            nLength: size_of::<SECURITY_ATTRIBUTES>() as u32,
            lpSecurityDescriptor: self.descriptor.0,
            bInheritHandle: inherit as i32,
        }
    }
    pub fn same_peer(&self, pid: u32) -> bool {
        unsafe {
            let mut session = 0;
            if ProcessIdToSessionId(pid, &mut session) == 0 || session != self.session {
                return false;
            }
            let Ok(process) = Handle::new(OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid))
            else {
                return false;
            };
            process_user(process.raw()).is_ok_and(|sid| sid == self.user)
        }
    }
    pub fn validate(&self, handle: HANDLE) -> Result<(), &'static str> {
        unsafe {
            let (mut owner, mut acl, mut descriptor) = (null_mut(), null_mut(), null_mut());
            if GetSecurityInfo(
                handle,
                SE_FILE_OBJECT,
                OWNER_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION,
                &mut owner,
                null_mut(),
                &mut acl,
                null_mut(),
                &mut descriptor,
            ) != 0
            {
                return Err("UNSAFE_PROFILE");
            }
            let allocation = Local(descriptor);
            let mut control = 0;
            let mut revision = 0;
            if owner.is_null()
                || acl.is_null()
                || sid_string(owner)? != self.user
                || GetSecurityDescriptorControl(descriptor, &mut control, &mut revision) == 0
                || control & SE_DACL_PROTECTED == 0
            {
                return Err("UNSAFE_PROFILE");
            }
            let mut owner_full = false;
            for index in 0..(*acl).AceCount as u32 {
                let mut entry = null_mut();
                if GetAce(acl, index, &mut entry) == 0 {
                    return Err("UNSAFE_PROFILE");
                }
                let entry = &*entry.cast::<ACCESS_ALLOWED_ACE>();
                if entry.Header.AceType != 0
                /* ACCESS_ALLOWED_ACE_TYPE */
                {
                    return Err("UNSAFE_PROFILE");
                }
                let sid = sid_string((&entry.SidStart as *const u32).cast_mut().cast())?;
                if sid != self.user && sid != "S-1-5-18" {
                    return Err("UNSAFE_PROFILE");
                }
                owner_full |= sid == self.user
                    && u32::from(entry.Header.AceFlags) & INHERIT_ONLY_ACE == 0
                    && entry.Mask & FILE_ALL_ACCESS == FILE_ALL_ACCESS;
            }
            drop(allocation);
            if owner_full {
                Ok(())
            } else {
                Err("UNSAFE_PROFILE")
            }
        }
    }
}

pub fn full(path: &Path) -> Result<Vec<u16>, &'static str> {
    let mut components = path.components();
    let verbatim = matches!(components.clone().next(), Some(Component::Prefix(p)) if matches!(p.kind(), Prefix::VerbatimDisk(_)));
    if !matches!(components.next(), Some(Component::Prefix(p)) if matches!(p.kind(), Prefix::Disk(_) | Prefix::VerbatimDisk(_)))
        || !matches!(components.next(), Some(Component::RootDir))
    {
        return Err("UNSAFE_PROFILE");
    }
    for part in components {
        let Component::Normal(name) = part else {
            return Err("UNSAFE_PROFILE");
        };
        let text = name.to_str().ok_or("UNSAFE_PROFILE")?;
        if text.ends_with(['.', ' ']) || text.contains(':') {
            return Err("UNSAFE_PROFILE");
        }
    }
    if verbatim {
        return wide(path);
    }
    let mut value = std::ffi::OsString::from("\\\\?\\");
    value.push(path);
    wide(value)
}

pub struct PrivateDirectory {
    pub path: PathBuf,
    pub security: Security,
    _ancestors: Vec<Handle>,
}
impl PrivateDirectory {
    pub fn open(path: &Path) -> Result<Self, &'static str> {
        let security = Security::current()?;
        full(path)?;
        unsafe {
            let drive = wide(path.ancestors().last().ok_or("UNSAFE_PROFILE")?)?;
            let mut flags = 0;
            let mut fs_name = [0u16; 32];
            if GetDriveTypeW(drive.as_ptr()) != 3
                || GetVolumeInformationW(
                    drive.as_ptr(),
                    null_mut(),
                    0,
                    null_mut(),
                    null_mut(),
                    &mut flags,
                    fs_name.as_mut_ptr(),
                    fs_name.len() as u32,
                ) == 0
                || flags & 8 /* FILE_PERSISTENT_ACLS */ == 0
                || !fs_name.starts_with(&[78, 84, 70, 83, 0])
            {
                return Err("UNSAFE_PROFILE");
            }
            let attributes = security.attributes(false);
            let mut handles = Vec::new();
            for ancestor in path.ancestors().collect::<Vec<_>>().into_iter().rev() {
                let name = full(ancestor)?;
                let open = || {
                    CreateFileW(
                        name.as_ptr(),
                        FILE_READ_ATTRIBUTES | READ_CONTROL,
                        FILE_SHARE_READ | FILE_SHARE_WRITE,
                        null(),
                        OPEN_EXISTING,
                        FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT,
                        null_mut(),
                    )
                };
                let mut raw = open();
                if raw == INVALID_HANDLE_VALUE
                    && matches!(GetLastError(), ERROR_FILE_NOT_FOUND | ERROR_PATH_NOT_FOUND)
                {
                    if CreateDirectoryW(name.as_ptr(), &attributes) == 0
                        && GetLastError() != ERROR_ALREADY_EXISTS
                    {
                        return Err("UNSAFE_PROFILE");
                    }
                    raw = open();
                }
                let handle = Handle::new(raw).map_err(|_| "UNSAFE_PROFILE")?;
                inspect(handle.raw(), true)?;
                if ancestor == path {
                    security.validate(handle.raw())?;
                }
                handles.push(handle);
            }
            Ok(Self {
                path: path.to_path_buf(),
                security,
                _ancestors: handles,
            })
        }
    }
    pub fn lock(&self) -> Result<Handle, &'static str> {
        unsafe {
            let attributes = self.security.attributes(false);
            let path = full(&self.path.join("shell.lock"))?;
            let raw = CreateFileW(
                path.as_ptr(),
                GENERIC_READ | GENERIC_WRITE | READ_CONTROL,
                0,
                &attributes,
                OPEN_ALWAYS,
                FILE_ATTRIBUTE_NORMAL | FILE_FLAG_OPEN_REPARSE_POINT,
                null_mut(),
            );
            if raw == INVALID_HANDLE_VALUE
                && matches!(
                    GetLastError(),
                    ERROR_SHARING_VIOLATION | ERROR_LOCK_VIOLATION
                )
            {
                return Err("PROFILE_IN_USE");
            }
            let handle = Handle::new(raw).map_err(|_| "UNSAFE_PROFILE")?;
            inspect(handle.raw(), false)?;
            self.security.validate(handle.raw())?;
            Ok(handle)
        }
    }
}

fn inspect(handle: HANDLE, directory: bool) -> Result<(), &'static str> {
    unsafe {
        let mut info: BY_HANDLE_FILE_INFORMATION = zeroed();
        if GetFileInformationByHandle(handle, &mut info) == 0
            || info.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT != 0
            || (info.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY != 0) != directory
            || (!directory && info.nNumberOfLinks != 1)
        {
            return Err("UNSAFE_PROFILE");
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{
        fs,
        process::{Command, Stdio},
    };
    #[test]
    fn windows_private_profile_rejects_foreign_acl_without_repair() {
        let root = crate::tests::scratch("acl");
        let security = Security::current().unwrap();
        unsafe {
            let sddl = wide(format!(
                "O:{}D:P(A;;FA;;;{})(A;;FR;;;WD)",
                security.user, security.user
            ))
            .unwrap();
            let mut descriptor = null_mut();
            assert_ne!(
                ConvertStringSecurityDescriptorToSecurityDescriptorW(
                    sddl.as_ptr(),
                    1,
                    &mut descriptor,
                    null_mut()
                ),
                0
            );
            let allocation = Local(descriptor);
            let mut present = 0;
            let mut defaulted = 0;
            let mut acl = null_mut();
            assert_ne!(
                GetSecurityDescriptorDacl(descriptor, &mut present, &mut acl, &mut defaulted),
                0
            );
            let mut path = full(&root).unwrap();
            assert_eq!(
                SetNamedSecurityInfoW(
                    path.as_mut_ptr(),
                    SE_FILE_OBJECT,
                    DACL_SECURITY_INFORMATION | PROTECTED_DACL_SECURITY_INFORMATION,
                    null_mut(),
                    null_mut(),
                    acl,
                    null()
                ),
                0
            );
            drop(allocation);
        }
        assert!(matches!(
            PrivateDirectory::open(&root),
            Err("UNSAFE_PROFILE")
        ));
        // A second attempt must still reject; no automatic ACL "repair".
        assert!(matches!(
            PrivateDirectory::open(&root),
            Err("UNSAFE_PROFILE")
        ));
        fs::remove_dir_all(root).unwrap();
    }
    #[test]
    fn windows_profile_rejects_hardlinked_lock_and_junction_ancestor() {
        let root = crate::tests::scratch("links");
        let directory = PrivateDirectory::open(&root).unwrap();
        drop(directory.lock().unwrap());
        fs::hard_link(root.join("shell.lock"), root.join("linked.lock")).unwrap();
        assert!(matches!(directory.lock(), Err("UNSAFE_PROFILE")));
        drop(directory);
        let target = crate::tests::scratch("junction-target");
        let junction = root.join("junction");
        assert!(Command::new("cmd.exe")
            .args(["/d", "/c", "mklink", "/J"])
            .arg(&junction)
            .arg(&target)
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status()
            .unwrap()
            .success());
        assert!(matches!(
            PrivateDirectory::open(&junction.join("child")),
            Err("UNSAFE_PROFILE")
        ));
        assert!(!target.join("child").exists());
        fs::remove_dir(junction).unwrap();
        fs::remove_dir_all(target).unwrap();
        fs::remove_dir_all(root).unwrap();
    }
}
