//! Twins of the descriptor-relative walks in `yoetz.adapters.git_subject_state`.
//!
//! * [`reject_unsafe_tree_entries`] is `GitSubjectStateAdapter._reject_unsafe_tree_entries`
//!   after its ignored-prefix Git call: a LIFO directory walk whose children are visited in
//!   file-system-encoded name byte order, counting every entry before the limit checks.
//! * [`check_index_entries`] and [`stat_tracked_paths`] are the two phases of
//!   `_reject_unsupported_index_entries`: every `ls-files --stage -z` record is parsed and
//!   validated before any path is stat-ed beneath the root descriptor.
//! * [`hash_untracked`] is the loop body of `_hash_untracked`, with the reference framing
//!   (domain, U32 path length, path, U32 permission bits, U64 size, content) and failure order.
//!
//! Failures are closed reason values; no path or content is ever carried in one.

use std::collections::HashSet;
use std::ffi::{CStr, CString};
use std::io;
use std::os::raw::c_int;

use sha2::{Digest, Sha256};

/// How a structural walk stopped. Each maps to one `_CaptureFailure` in the reference.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Failure {
    /// `_CaptureFailure(UNSAFE_ROOT)`.
    UnsafeRoot,
    /// `_CaptureFailure(FILE_LIMIT_EXCEEDED, UNSUPPORTED, detail=(bound, observed, limit))`.
    FileLimit(u64),
    /// `_CaptureFailure(READ_LIMIT_EXCEEDED, UNSUPPORTED)`.
    ReadLimit,
    /// `_CaptureFailure(SYMLINK_UNSUPPORTED, UNSUPPORTED)`.
    SymlinkUnsupported,
    /// `_CaptureFailure(SYMLINK_UNSUPPORTED)` (status `STATE_NOT_OBSERVED`): an `OSError` while
    /// reading one untracked file.
    SymlinkNotObserved,
    /// `_CaptureFailure(SUBMODULE_PRESENT, UNSUPPORTED)`.
    SubmodulePresent,
    /// `_CaptureFailure(INPUT_CHANGED, CHANGED_DURING_CAPTURE)`.
    InputChanged,
    /// `_GitProcessFailure()`: malformed Git output.
    GitFailed,
    /// An uncaught `OSError` with this errno.
    Os(i32),
}

/// A walk either fails closed or is aborted by its caller (for example a pending signal).
#[derive(Debug)]
pub enum Stop<E> {
    Fail(Failure),
    Abort(E),
}

impl<E> From<Failure> for Stop<E> {
    fn from(failure: Failure) -> Self {
        Stop::Fail(failure)
    }
}

// `mode_t` is narrower than u32 on some targets.
#[allow(clippy::unnecessary_cast)]
const S_IFMT: u32 = libc::S_IFMT as u32;
#[allow(clippy::unnecessary_cast)]
const S_IFDIR: u32 = libc::S_IFDIR as u32;
#[allow(clippy::unnecessary_cast)]
const S_IFREG: u32 = libc::S_IFREG as u32;
#[allow(clippy::unnecessary_cast)]
const S_IFLNK: u32 = libc::S_IFLNK as u32;

/// The `os.stat_result` fields the reference reads.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Facts {
    pub dev: u64,
    pub ino: u64,
    pub mode: u32,
    pub nlink: u64,
    pub uid: u32,
    pub size: i64,
    pub mtime: (i64, i64),
    pub ctime: (i64, i64),
}

impl Facts {
    #[allow(clippy::unnecessary_cast)]
    fn from_stat(raw: &libc::stat) -> Self {
        Facts {
            dev: raw.st_dev as u64,
            ino: raw.st_ino as u64,
            mode: raw.st_mode as u32,
            nlink: raw.st_nlink as u64,
            uid: raw.st_uid as u32,
            size: raw.st_size as i64,
            mtime: (raw.st_mtime as i64, raw.st_mtime_nsec as i64),
            ctime: (raw.st_ctime as i64, raw.st_ctime_nsec as i64),
        }
    }

    #[inline]
    pub fn is_dir(&self) -> bool {
        self.mode & S_IFMT == S_IFDIR
    }
    #[inline]
    pub fn is_reg(&self) -> bool {
        self.mode & S_IFMT == S_IFREG
    }
    #[inline]
    pub fn is_lnk(&self) -> bool {
        self.mode & S_IFMT == S_IFLNK
    }
    /// `stat.S_IMODE(st_mode)`.
    #[inline]
    pub fn imode(&self) -> u32 {
        self.mode & 0o7777
    }

    /// `_same_file_snapshot(before, after)`.
    fn same_snapshot(&self, other: &Facts) -> bool {
        (
            self.dev, self.ino, self.mode, self.size, self.mtime, self.ctime, self.nlink,
        ) == (
            other.dev,
            other.ino,
            other.mode,
            other.size,
            other.mtime,
            other.ctime,
            other.nlink,
        )
    }
}

fn last_errno() -> i32 {
    io::Error::last_os_error()
        .raw_os_error()
        .unwrap_or(libc::EIO)
}

fn c_path(path: &[u8]) -> Result<CString, i32> {
    // Every caller has already refused NUL; ENOENT would be the reference's ValueError path.
    CString::new(path).map_err(|_| libc::EINVAL)
}

/// `fstatat(dir_fd, path, AT_SYMLINK_NOFOLLOW)`.
pub fn lstat_at(dir_fd: c_int, path: &CStr) -> Result<Facts, i32> {
    let mut raw = std::mem::MaybeUninit::<libc::stat>::uninit();
    let result = unsafe {
        libc::fstatat(
            dir_fd,
            path.as_ptr(),
            raw.as_mut_ptr(),
            libc::AT_SYMLINK_NOFOLLOW,
        )
    };
    if result != 0 {
        return Err(last_errno());
    }
    let raw = unsafe { raw.assume_init() };
    Ok(Facts::from_stat(&raw))
}

/// `fstat(fd)`.
pub fn fstat(fd: c_int) -> Result<Facts, i32> {
    let mut raw = std::mem::MaybeUninit::<libc::stat>::uninit();
    if unsafe { libc::fstat(fd, raw.as_mut_ptr()) } != 0 {
        return Err(last_errno());
    }
    let raw = unsafe { raw.assume_init() };
    Ok(Facts::from_stat(&raw))
}

/// An owned descriptor, closed on drop (close errors are not observable in the reference).
pub struct Fd(c_int);

impl Fd {
    pub fn raw(&self) -> c_int {
        self.0
    }
}

impl Drop for Fd {
    fn drop(&mut self) {
        if self.0 >= 0 {
            unsafe { libc::close(self.0) };
        }
    }
}

/// `openat(dir_fd, path, flags | O_CLOEXEC)`, retrying `EINTR` like `os.open`.
pub fn open_at(dir_fd: c_int, path: &CStr, flags: c_int) -> Result<Fd, i32> {
    loop {
        let fd = unsafe { libc::openat(dir_fd, path.as_ptr(), flags | libc::O_CLOEXEC) };
        if fd >= 0 {
            return Ok(Fd(fd));
        }
        let errno = last_errno();
        if errno != libc::EINTR {
            return Err(errno);
        }
    }
}

/// `os.read(fd, n)`, retrying `EINTR`.
fn read_some(fd: c_int, buffer: &mut [u8]) -> Result<usize, i32> {
    loop {
        let count = unsafe { libc::read(fd, buffer.as_mut_ptr().cast(), buffer.len()) };
        if count >= 0 {
            return Ok(count as usize);
        }
        let errno = last_errno();
        if errno != libc::EINTR {
            return Err(errno);
        }
    }
}

#[cfg(any(target_os = "linux", target_os = "android"))]
unsafe fn errno_location() -> *mut c_int {
    unsafe { libc::__errno_location() }
}

#[cfg(any(
    target_os = "macos",
    target_os = "ios",
    target_os = "freebsd",
    target_os = "dragonfly"
))]
unsafe fn errno_location() -> *mut c_int {
    unsafe { libc::__error() }
}

#[cfg(any(target_os = "netbsd", target_os = "openbsd"))]
unsafe fn errno_location() -> *mut c_int {
    unsafe { libc::__errno() }
}

/// An open directory stream over an owned descriptor.
struct Dir(*mut libc::DIR);

impl Dir {
    fn from_fd(fd: Fd) -> Result<Dir, i32> {
        let stream = unsafe { libc::fdopendir(fd.0) };
        if stream.is_null() {
            return Err(last_errno());
        }
        std::mem::forget(fd);
        Ok(Dir(stream))
    }

    fn fd(&self) -> c_int {
        unsafe { libc::dirfd(self.0) }
    }

    /// Every name except `.` and `..`, in stream order (`os.scandir`).
    fn names(&mut self) -> Result<Vec<Vec<u8>>, i32> {
        let mut names = Vec::new();
        loop {
            unsafe { *errno_location() = 0 };
            let entry = unsafe { libc::readdir(self.0) };
            if entry.is_null() {
                let errno = unsafe { *errno_location() };
                if errno != 0 {
                    return Err(errno);
                }
                return Ok(names);
            }
            let name = unsafe { CStr::from_ptr((*entry).d_name.as_ptr()) }.to_bytes();
            if name == b"." || name == b".." {
                continue;
            }
            names.push(name.to_vec());
        }
    }
}

impl Drop for Dir {
    fn drop(&mut self) {
        unsafe { libc::closedir(self.0) };
    }
}

/// Limits and identity `_reject_unsafe_tree_entries` reads from its adapter and module.
pub struct TreeLimits {
    pub max_files: u64,
    pub path_output_limit: u64,
    pub expected_uid: u32,
}

/// `GitSubjectStateAdapter._reject_unsafe_tree_entries` after `_collect_ignored_prefixes`.
///
/// `checkpoint` runs before each directory is read; an `Err` aborts the walk (the binding uses
/// it to deliver pending signals such as `KeyboardInterrupt`).
pub fn reject_unsafe_tree_entries<E>(
    root: &[u8],
    ignored_prefixes: &HashSet<Vec<u8>>,
    limits: &TreeLimits,
    checkpoint: &mut dyn FnMut() -> Result<(), E>,
) -> Result<(), Stop<E>> {
    let root_path = c_path(root).map_err(|_| Failure::UnsafeRoot)?;
    let root_fd = open_at(
        libc::AT_FDCWD,
        &root_path,
        libc::O_RDONLY | libc::O_DIRECTORY,
    )
    .map_err(|_| Failure::UnsafeRoot)?;
    let mut pending: Vec<Vec<u8>> = vec![Vec::new()];
    let mut entries_seen: u64 = 0;
    let mut path_bytes_seen: u64 = 0;
    while let Some(relative_dir) = pending.pop() {
        checkpoint().map_err(Stop::Abort)?;
        let mut directory = if relative_dir.is_empty() {
            let fd = open_at(root_fd.raw(), c".", libc::O_RDONLY | libc::O_DIRECTORY);
            fd.and_then(Dir::from_fd)
        } else {
            c_path(&relative_dir)
                .and_then(|path| {
                    open_at(
                        root_fd.raw(),
                        &path,
                        libc::O_RDONLY | libc::O_DIRECTORY | libc::O_NOFOLLOW,
                    )
                })
                .and_then(Dir::from_fd)
        }
        .map_err(|_| Failure::UnsafeRoot)?;
        let mut names = directory.names().map_err(|_| Failure::UnsafeRoot)?;
        names.sort_unstable();
        for name in names {
            entries_seen += 1;
            path_bytes_seen += name.len() as u64;
            if entries_seen > limits.max_files {
                return Err(Failure::FileLimit(entries_seen).into());
            }
            if path_bytes_seen > limits.path_output_limit {
                return Err(Failure::ReadLimit.into());
            }
            let c_name = c_path(&name).map_err(|_| Failure::UnsafeRoot)?;
            let facts = lstat_at(directory.fd(), &c_name).map_err(|_| Failure::UnsafeRoot)?;
            if facts.uid != limits.expected_uid || facts.imode() & 0o022 != 0 {
                return Err(Failure::UnsafeRoot.into());
            }
            if facts.is_lnk() || !(facts.is_reg() || facts.is_dir()) {
                return Err(Failure::SymlinkUnsupported.into());
            }
            if facts.is_dir() {
                if name == b".git" {
                    if !relative_dir.is_empty() {
                        return Err(Failure::UnsafeRoot.into());
                    }
                    continue;
                }
                let relative = if relative_dir.is_empty() {
                    name
                } else {
                    let mut joined = Vec::with_capacity(relative_dir.len() + 1 + name.len());
                    joined.extend_from_slice(&relative_dir);
                    joined.push(b'/');
                    joined.extend_from_slice(&name);
                    joined
                };
                if ignored_prefixes.contains(&relative) {
                    continue;
                }
                let mut slashed = relative;
                slashed.push(b'/');
                if ignored_prefixes.contains(&slashed) {
                    continue;
                }
                slashed.pop();
                pending.push(slashed);
            }
        }
        drop(directory);
    }
    Ok(())
}

/// `_nul_entries(payload)`; `None` is the reference's `_GitProcessFailure`.
pub fn nul_entries(payload: &[u8]) -> Option<Vec<&[u8]>> {
    if payload.is_empty() {
        return Some(Vec::new());
    }
    if *payload.last()? != 0 {
        return None;
    }
    let entries: Vec<&[u8]> = payload[..payload.len() - 1]
        .split(|byte| *byte == 0)
        .collect();
    if entries.iter().any(|entry| entry.is_empty()) {
        return None;
    }
    Some(entries)
}

/// `_validate_relative_git_path(path)` succeeded.
pub fn valid_relative_git_path(path: &[u8]) -> bool {
    !path.is_empty()
        && path[0] != b'/'
        && !path.contains(&0)
        && path
            .split(|byte| *byte == b'/')
            .all(|part| !part.is_empty() && part != b"." && part != b"..")
}

/// The parse-and-validate phase of `_reject_unsupported_index_entries`: every entry, in order,
/// is split, its path validated, and its mode refused for submodules and symlinks.
pub fn check_index_entries<'a>(entries: &[&'a [u8]]) -> Result<Vec<&'a [u8]>, Failure> {
    let mut tracked = Vec::with_capacity(entries.len());
    for entry in entries {
        let Some(tab) = entry.iter().position(|byte| *byte == b'\t') else {
            return Err(Failure::GitFailed);
        };
        let (metadata, path) = (&entry[..tab], &entry[tab + 1..]);
        let mut fields = metadata.split(|byte| *byte == b' ');
        let mode = fields.next().unwrap_or_default();
        if fields.count() != 2 {
            return Err(Failure::GitFailed);
        }
        if !valid_relative_git_path(path) {
            return Err(Failure::UnsafeRoot);
        }
        if mode == b"160000" {
            return Err(Failure::SubmodulePresent);
        }
        if mode == b"120000" {
            return Err(Failure::SymlinkUnsupported);
        }
        tracked.push(path);
    }
    Ok(tracked)
}

/// The stat phase of `_reject_unsupported_index_entries`: a vanished path is skipped, any other
/// `OSError` escapes, and anything but a regular file is refused.
pub fn stat_tracked_paths<E>(
    dir_fd: c_int,
    paths: &[&[u8]],
    checkpoint: &mut dyn FnMut() -> Result<(), E>,
) -> Result<(), Stop<E>> {
    for (index, path) in paths.iter().enumerate() {
        if index % 1024 == 0 {
            checkpoint().map_err(Stop::Abort)?;
        }
        let c_path = c_path(path).map_err(Failure::Os)?;
        match lstat_at(dir_fd, &c_path) {
            Ok(facts) => {
                if !facts.is_reg() {
                    return Err(Failure::SymlinkUnsupported.into());
                }
            }
            Err(libc::ENOENT) => continue,
            Err(errno) => return Err(Failure::Os(errno).into()),
        }
    }
    Ok(())
}

/// What `_hash_untracked` needs besides the inventory.
pub struct UntrackedLimits {
    pub max_hash_bytes: u64,
    pub already_hashed: u64,
    pub read_chunk: usize,
    pub expected_uid: u32,
}

/// The digest, byte total, and file count `_hash_untracked` returns.
#[derive(Debug, PartialEq, Eq)]
pub struct UntrackedDigest {
    pub digest: String,
    pub total_bytes: u64,
}

/// The `_hash_untracked` loop over an already-split, already-counted inventory.
pub fn hash_untracked<E>(
    dir_fd: c_int,
    domain: &[u8],
    paths: &[&[u8]],
    limits: &UntrackedLimits,
    checkpoint: &mut dyn FnMut() -> Result<(), E>,
) -> Result<UntrackedDigest, Stop<E>> {
    let mut hasher = Sha256::new();
    hasher.update(domain);
    let max = u128::from(limits.max_hash_bytes);
    let already = u128::from(limits.already_hashed);
    let mut total_bytes: u128 = 0;
    let mut previous: Option<&[u8]> = None;
    let chunk = limits.read_chunk.max(1);
    let mut buffer = vec![0_u8; chunk];
    for path in paths {
        checkpoint().map_err(Stop::Abort)?;
        if !valid_relative_git_path(path) {
            return Err(Failure::UnsafeRoot.into());
        }
        if let Some(prior) = previous {
            if *path <= prior {
                return Err(Failure::GitFailed.into());
            }
        }
        previous = Some(path);
        let c_path = c_path(path).map_err(|_| Failure::SymlinkNotObserved)?;
        let fd = open_at(dir_fd, &c_path, libc::O_RDONLY | libc::O_NOFOLLOW)
            .map_err(|_| Failure::SymlinkNotObserved)?;
        let before = fstat(fd.raw()).map_err(|_| Failure::SymlinkNotObserved)?;
        if !before.is_reg() || before.uid != limits.expected_uid || before.nlink != 1 {
            return Err(Failure::SymlinkUnsupported.into());
        }
        // A regular file's size is never negative.
        let size = u128::try_from(before.size).unwrap_or(0);
        if already + total_bytes + size > max {
            return Err(Failure::ReadLimit.into());
        }
        hasher.update((path.len() as u32).to_be_bytes());
        hasher.update(path);
        hasher.update(before.imode().to_be_bytes());
        hasher.update((before.size as u64).to_be_bytes());
        let mut file_bytes: u128 = 0;
        loop {
            // min(_READ_CHUNK, size - file_bytes + 1) never goes below zero: the loop stops as
            // soon as file_bytes reaches size + 1 because the next request is for 0 bytes.
            let wanted = (size + 1).saturating_sub(file_bytes).min(chunk as u128) as usize;
            let count = read_some(fd.raw(), &mut buffer[..wanted])
                .map_err(|_| Failure::SymlinkNotObserved)?;
            if count == 0 {
                break;
            }
            file_bytes += count as u128;
            if already + total_bytes + file_bytes > max {
                return Err(Failure::ReadLimit.into());
            }
            hasher.update(&buffer[..count]);
        }
        let after = fstat(fd.raw()).map_err(|_| Failure::SymlinkNotObserved)?;
        if file_bytes != size || !before.same_snapshot(&after) {
            return Err(Failure::InputChanged.into());
        }
        total_bytes += file_bytes;
    }
    Ok(UntrackedDigest {
        digest: format!("sha256:{}", hex::encode(hasher.finalize())),
        total_bytes: total_bytes as u64,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn nul_entries_matches_reference() {
        assert_eq!(nul_entries(b""), Some(vec![]));
        assert_eq!(nul_entries(b"a\0b\0"), Some(vec![&b"a"[..], b"b"]));
        assert_eq!(nul_entries(b"a"), None);
        assert_eq!(nul_entries(b"a\0\0"), None);
        assert_eq!(nul_entries(b"\0"), None);
    }

    #[test]
    fn relative_paths() {
        assert!(valid_relative_git_path(b"a/b"));
        assert!(!valid_relative_git_path(b""));
        assert!(!valid_relative_git_path(b"/a"));
        assert!(!valid_relative_git_path(b"a//b"));
        assert!(!valid_relative_git_path(b"a/./b"));
        assert!(!valid_relative_git_path(b"a/.."));
        assert!(!valid_relative_git_path(b"a/"));
    }

    #[test]
    fn index_entries_parse_before_mode_checks() {
        let entries: Vec<&[u8]> = vec![b"100644 abc 0\tok", b"160000 abc 0\tsub"];
        assert_eq!(
            check_index_entries(&entries),
            Err(Failure::SubmodulePresent)
        );
        let entries: Vec<&[u8]> = vec![b"100644 abc\tok"];
        assert_eq!(check_index_entries(&entries), Err(Failure::GitFailed));
        let entries: Vec<&[u8]> = vec![b"120000 abc 0\t../x"];
        assert_eq!(check_index_entries(&entries), Err(Failure::UnsafeRoot));
        let entries: Vec<&[u8]> = vec![b"100644 a 0\tx\ty"];
        assert_eq!(check_index_entries(&entries), Ok(vec![&b"x\ty"[..]]));
    }
}
