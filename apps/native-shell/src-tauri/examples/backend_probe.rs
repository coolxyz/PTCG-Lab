//! Exercise the real Rust owner against a relocated payload and a NEW profile.
//! No credentials/cookies/PIDs are printed and no remote server is configured.
use ptcg_native_shell::{
    backend::{verify, Backend},
    platform::instance::Instance,
};
use std::{
    io::{Read, Write},
    net::TcpStream,
    path::PathBuf,
    time::Duration,
};

fn product(origin: &str) {
    let address = origin.strip_prefix("http://").unwrap();
    let mut socket = TcpStream::connect(address).unwrap();
    socket
        .set_read_timeout(Some(Duration::from_secs(5)))
        .unwrap();
    socket
        .write_all(
            format!("GET / HTTP/1.1\r\nHost: {address}\r\nConnection: close\r\n\r\n").as_bytes(),
        )
        .unwrap();
    let mut status = [0u8; 12];
    socket.read_exact(&mut status).unwrap();
    assert_eq!(&status, b"HTTP/1.1 200");
}

fn main() {
    let args: Vec<_> = std::env::args_os().skip(1).collect();
    assert_eq!(
        args.len(),
        2,
        "pass payload and a NEW private profile directory"
    );
    let (root, state) = (PathBuf::from(&args[0]), PathBuf::from(&args[1]));
    assert!(!state.exists(), "probe refuses an existing profile");
    let mut instance = Instance::acquire(&state).unwrap().unwrap();
    instance.listen(|| {});
    let (mut first, ready) = Backend::start(&root, &state).unwrap();
    product(&ready.origin);
    first.stop();
    assert!(first.exited());
    assert!(TcpStream::connect(ready.origin.strip_prefix("http://").unwrap()).is_err());
    let (mut second, reopened) = Backend::start(&root, &state).unwrap();
    assert_ne!(ready.cookie, reopened.cookie);
    product(&reopened.origin);
    second.stop();
    assert!(second.exited());
    assert!(TcpStream::connect(reopened.origin.strip_prefix("http://").unwrap()).is_err());
    assert!(verify(&root).is_ok());
    println!("{{\"rustOwnerStart\":true,\"sameProfileRestart\":true,\"freshCookie\":true,\"ownedExit\":true,\"immutablePayload\":true}}");
}
