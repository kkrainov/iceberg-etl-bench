// Licensed under the Apache License, Version 2.0 (see LICENSE in the repository root).

//! Embeds the resolved engine-library versions from Cargo.lock, so `/status`
//! reports exactly what was compiled in (e.g. "55.0.0" or "4.0.0@ee3eacd9").

fn main() {
    println!("cargo:rerun-if-changed=Cargo.lock");
    let lock = std::fs::read_to_string("Cargo.lock").unwrap_or_default();
    let wanted = [
        "datafusion",
        "datafusion-distributed",
        "iceberg",
        "iceberg-datafusion",
    ];
    for name in wanted {
        let mut found = String::from("unknown");
        for pkg in lock.split("[[package]]") {
            let field = |key: &str| {
                pkg.lines()
                    .find_map(|l| l.strip_prefix(&format!("{key} = \"")))
                    .map(|v| v.trim_end_matches('"').to_string())
            };
            if field("name").as_deref() == Some(name) {
                let version = field("version").unwrap_or_default();
                found = match field("source").and_then(|s| s.split_once('#').map(|(_, r)| r[..8].to_string())) {
                    Some(rev) => format!("{version}@{rev}"),
                    None => version,
                };
            }
        }
        let var = name.to_uppercase().replace('-', "_");
        println!("cargo:rustc-env=DFRUNNER_DEP_{var}={found}");
    }
}
