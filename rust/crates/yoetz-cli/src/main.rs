//! `yoetz-rs`: native Yoetz tools built on `yoetz-core`.
//!
//! These commands need no Python interpreter and start in about a millisecond. They read one
//! value from standard input and never touch Yoetz state, the service, or the network.
//!
//! ```text
//! yoetz-rs canonical < value.json    # restricted-JCS canonical bytes
//! yoetz-rs digest < value.json       # sha256:<hex> of the canonical bytes
//! yoetz-rs check < value.json        # exit 0 when the input is already canonical
//! yoetz-rs sha256 < file             # sha256:<hex> of the raw bytes
//! yoetz-rs version
//! ```

use std::io::{self, Read, Write};
use std::process::ExitCode;

use yoetz_core::protocol::{canonical, json};

const USAGE: &str = "usage: yoetz-rs <canonical|digest|check|sha256|version>\n";

/// Exit codes follow the Python CLI's bounded vocabulary: 2 for usage, 65 for invalid input.
const EXIT_USAGE: u8 = 2;
const EXIT_DATA: u8 = 65;
const EXIT_IO: u8 = 74;

fn read_stdin() -> Result<Vec<u8>, ExitCode> {
    let mut input = Vec::new();
    match io::stdin().lock().read_to_end(&mut input) {
        Ok(_) => Ok(input),
        Err(_) => {
            eprintln!("io_error: standard input could not be read");
            Err(ExitCode::from(EXIT_IO))
        }
    }
}

fn write_stdout(bytes: &[u8]) -> ExitCode {
    let mut out = io::stdout().lock();
    if out.write_all(bytes).and_then(|()| out.flush()).is_err() {
        return ExitCode::from(EXIT_IO);
    }
    ExitCode::SUCCESS
}

fn refuse(reason: &str) -> ExitCode {
    // Reason codes only: input bytes never reach an error message.
    eprintln!("invalid_input: {reason}");
    ExitCode::from(EXIT_DATA)
}

fn run(command: &str) -> ExitCode {
    match command {
        "version" => write_stdout(
            format!(
                "yoetz-rs {} (yoetz-core interface {})\n",
                env!("CARGO_PKG_VERSION"),
                yoetz_core::INTERFACE_VERSION
            )
            .as_bytes(),
        ),
        "sha256" => match read_stdin() {
            Ok(input) => write_stdout(format!("{}\n", canonical::sha256_prefixed(&input)).as_bytes()),
            Err(code) => code,
        },
        "canonical" | "digest" | "check" => {
            let input = match read_stdin() {
                Ok(input) => input,
                Err(code) => return code,
            };
            let value = match json::parse(&input) {
                Ok(value) => value,
                Err(reason) => return refuse(reason),
            };
            let encoded = match canonical::encode(&value) {
                Ok(encoded) => encoded,
                Err(reason) => return refuse(reason),
            };
            match command {
                "canonical" => write_stdout(&encoded),
                "digest" => write_stdout(format!("{}\n", canonical::sha256_prefixed(&encoded)).as_bytes()),
                _ if encoded == input => ExitCode::SUCCESS,
                _ => refuse("noncanonical_bytes"),
            }
        }
        _ => {
            let _ = io::stderr().write_all(USAGE.as_bytes());
            ExitCode::from(EXIT_USAGE)
        }
    }
}

fn main() -> ExitCode {
    let arguments: Vec<String> = std::env::args().skip(1).collect();
    match arguments.as_slice() {
        [command] => run(command),
        _ => {
            let _ = io::stderr().write_all(USAGE.as_bytes());
            ExitCode::from(EXIT_USAGE)
        }
    }
}
