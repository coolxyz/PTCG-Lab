//! JSON-lines conformance helper: never prints rejected content or credentials.
use ptcg_mobile_lib::online::protocol::{checked, unpack};
use std::io::{BufRead, Write};
fn main() {
    let mut output = std::io::stdout().lock();
    for line in std::io::stdin().lock().split(b'\n') {
        let valid = line
            .ok()
            .and_then(|raw| unpack(&raw).ok())
            .is_some_and(|v| {
                v["kind"]
                    .as_str()
                    .is_some_and(|kind| checked(kind, v["value"].clone()).is_ok())
            });
        writeln!(output, "{{\"valid\":{valid}}}").unwrap();
        output.flush().unwrap();
    }
}
