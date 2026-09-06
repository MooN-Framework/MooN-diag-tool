# Config-Checksum: Rust-seitige Verifikation

## Ziel

Jede generierte `node.toml` traegt eine SHA-256-Checksum ueber ihren
Inhalt in einer `[integrity]`-Section. Der Rust-Loader parsed das TOML,
prueft die Checksumme, und lehnt korrupte/veraenderte Configs beim
Start ab.

## Canonical form

Damit Python und Rust denselben Digest berechnen, wird ein sprach-
neutrales Zwischenformat verwendet — siehe
`src/harness/config_gen.py` (Python-Referenzimplementierung):

- Nested TOML wird in eine flache Liste `(dotted.path, leaf_value)` gefaltet.
- Sortierung: nach voll qualifiziertem Pfad, byteweise alphabetisch —
  NACH dem Flatten, nicht innerhalb der Sub-Tables.
- Jede Zeile: `path=<json-encoded value>`
  - Strings: `"..."` mit JSON-Escaping
  - Ints/Bools: literal (`42`, `true`)
  - Floats: JSON default (`1.0`)
- LF-Trennung, KEIN trailing LF.
- Die Top-Level-Key `integrity` wird beim Digest weggelassen.
- SHA-256 -> Hex (lowercase).

Beispiel-Auszug fuer eine Config:

```
crc_offset_ms=17
cycle_duration_ms=20
diagnostic.enabled=true
diagnostic.interface="lo"
diagnostic.multicast_group="239.10.0.2"
diagnostic.port=6666
own_id=0
participants.minimum=2
participants.nominal=3
...
```

## Cargo.toml

Neue Dependencies:

```toml
sha2 = "0.10"
serde_json = "1"           # bereits vorhanden? falls nicht, hinzu.
```

`toml` ist bereits im Projekt.

## src/framework/config.rs

Neue `IntegritySection` und ein `verify_and_load` Konstruktor:

```rust
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;

#[derive(Debug, Clone, Deserialize)]
pub struct IntegritySection {
    pub algo: String,
    pub checksum: String,
}

impl NodeConfig {
    /// Parse a TOML string, verify its integrity section, return the
    /// NodeConfig. All rendered configs must carry an [integrity]
    /// section (produced by harness.config_gen). Errors are fatal —
    /// callers should refuse to start on any of them.
    pub fn verify_and_parse(text: &str) -> Result<Self, ConfigError> {
        // 1. Parse into a generic toml::Value so we can walk the tree
        //    for the canonical form BEFORE deserializing.
        let value: toml::Value = toml::from_str(text)
            .map_err(|e| ConfigError::Parse(e.to_string()))?;

        // 2. Read the integrity section.
        let integrity = value
            .get("integrity")
            .and_then(|v| v.as_table())
            .ok_or(ConfigError::MissingIntegrity)?;
        let algo = integrity
            .get("algo")
            .and_then(|v| v.as_str())
            .ok_or(ConfigError::MissingIntegrityField("algo"))?;
        if algo != "sha256" {
            return Err(ConfigError::UnsupportedAlgo(algo.to_string()));
        }
        let expected_digest = integrity
            .get("checksum")
            .and_then(|v| v.as_str())
            .ok_or(ConfigError::MissingIntegrityField("checksum"))?;

        // 3. Compute canonical form + digest, excluding [integrity].
        let canonical = canonical_bytes(&value);
        let mut hasher = Sha256::new();
        hasher.update(&canonical);
        let actual_digest = hex_lower(&hasher.finalize());

        if actual_digest != expected_digest {
            return Err(ConfigError::ChecksumMismatch {
                expected: expected_digest.to_string(),
                actual: actual_digest,
            });
        }

        // 4. Real deserialize into NodeConfig.
        //    NodeConfig doesn't include the integrity field so serde
        //    will happily ignore it (deny_unknown_fields would need to
        //    be off, which is the default).
        toml::from_str::<NodeConfig>(text)
            .map_err(|e| ConfigError::Parse(e.to_string()))
    }
}

/// Walk the toml::Value tree, produce the canonical bytes.
/// Excludes the top-level `integrity` key.
fn canonical_bytes(root: &toml::Value) -> Vec<u8> {
    let mut leaves: Vec<(String, String)> = Vec::new();
    if let Some(t) = root.as_table() {
        for (k, v) in t.iter() {
            if k == "integrity" {
                continue;
            }
            flatten(k, v, &mut leaves);
        }
    }
    leaves.sort_by(|a, b| a.0.cmp(&b.0));
    let mut out = String::new();
    for (i, (k, v)) in leaves.iter().enumerate() {
        if i > 0 {
            out.push('\n');
        }
        out.push_str(k);
        out.push('=');
        out.push_str(v);
    }
    out.into_bytes()
}

fn flatten(prefix: &str, v: &toml::Value, out: &mut Vec<(String, String)>) {
    match v {
        toml::Value::Table(t) => {
            for (k, sub) in t.iter() {
                let path = format!("{prefix}.{k}");
                flatten(&path, sub, out);
            }
        }
        _ => out.push((prefix.to_string(), value_to_json(v))),
    }
}

/// JSON-encode a scalar toml::Value the same way `json.dumps` would.
fn value_to_json(v: &toml::Value) -> String {
    match v {
        toml::Value::String(s) => {
            // JSON string with standard escaping.
            let mut out = String::with_capacity(s.len() + 2);
            out.push('"');
            for c in s.chars() {
                match c {
                    '"' => out.push_str("\\\""),
                    '\\' => out.push_str("\\\\"),
                    '\n' => out.push_str("\\n"),
                    '\r' => out.push_str("\\r"),
                    '\t' => out.push_str("\\t"),
                    c if (c as u32) < 0x20 => {
                        out.push_str(&format!("\\u{:04x}", c as u32));
                    }
                    c => out.push(c),
                }
            }
            out.push('"');
            out
        }
        toml::Value::Integer(i) => i.to_string(),
        toml::Value::Boolean(b) => b.to_string(),
        toml::Value::Float(f) => {
            // Match Python json.dumps behaviour: no unnecessary trailing zeros,
            // but always include a decimal point / exponent.
            let s = format!("{}", f);
            if s.contains('.') || s.contains('e') || s.contains('E') {
                s
            } else {
                format!("{}.0", s)
            }
        }
        toml::Value::Datetime(dt) => format!("\"{}\"", dt),  // rare in configs
        toml::Value::Array(_) | toml::Value::Table(_) => {
            unreachable!("flatten already peeled tables; arrays not used in our schema")
        }
    }
}

fn hex_lower(bytes: &[u8]) -> String {
    let mut s = String::with_capacity(bytes.len() * 2);
    for b in bytes {
        s.push_str(&format!("{:02x}", b));
    }
    s
}

#[derive(Debug)]
pub enum ConfigError {
    Parse(String),
    MissingIntegrity,
    MissingIntegrityField(&'static str),
    UnsupportedAlgo(String),
    ChecksumMismatch { expected: String, actual: String },
}

impl std::fmt::Display for ConfigError {
    fn fmt(&self, f: &mut std::fmt::Formatter) -> std::fmt::Result {
        match self {
            Self::Parse(msg) => write!(f, "config parse failed: {msg}"),
            Self::MissingIntegrity => write!(f, "missing [integrity] section"),
            Self::MissingIntegrityField(name) => {
                write!(f, "[integrity].{name} missing or wrong type")
            }
            Self::UnsupportedAlgo(a) => write!(f, "unsupported checksum algo: {a}"),
            Self::ChecksumMismatch { expected, actual } => write!(
                f,
                "checksum mismatch: file says {expected}, computed {actual}"
            ),
        }
    }
}

impl std::error::Error for ConfigError {}
```

## src/bin/node.rs

Loader auf die neue Funktion umstellen:

```diff
 fn load_config(path: &PathBuf) -> Result<NodeConfig, Box<dyn std::error::Error>> {
     let bytes = fs::read_to_string(path)?;
-    Ok(toml::from_str(&bytes)?)
+    NodeConfig::verify_and_parse(&bytes).map_err(|e| Box::<dyn std::error::Error>::from(e.to_string()))
 }
```

## Tests

Unit-Test in `src/framework/config.rs`:

```rust
#[cfg(test)]
mod checksum_tests {
    use super::*;

    const KNOWN_GOOD: &str = r#"
own_id = 0

[participants]
nominal          = 3
minimum          = 2
probation_cycles = 10

[timing]
cycle_duration_ms = 20
share_inputs_offset_ms  = 5
share_result_offset_ms  = 10
send_ack_offset_ms      = 14
crc_offset_ms           = 17
init_sync_timeout_ms        = 2000
clock_sync_timeout_ms       = 100
cycle_sync_timeout_ms       = 10
error_mgmt_timeout_ms       = 20
state_sync_timeout_ms       = 60
resync_returning_timeout_ms = 500
resync_healthy_timeout_ms   = 100
send_interval_ms         = 1
stale_frame_threshold_ms = 100
resync_interval_cycles   = 500

[transport]
interface       = "lo"
multicast_group = "239.10.0.1"
port            = 5555

[diagnostic]
enabled         = true
interface       = "lo"
multicast_group = "239.10.0.2"
port            = 6666

[integrity]
algo     = "sha256"
checksum = "<HEX>"
"#;

    #[test]
    fn accepts_correct_checksum() {
        // Generate with harness.config_gen first, drop the digest into
        // KNOWN_GOOD, run: cargo test.
        let ok = NodeConfig::verify_and_parse(KNOWN_GOOD).is_ok();
        assert!(ok);
    }

    #[test]
    fn rejects_tampered_config() {
        let tampered = KNOWN_GOOD.replace("nominal          = 3", "nominal          = 4");
        let err = NodeConfig::verify_and_parse(&tampered).unwrap_err();
        assert!(matches!(err, ConfigError::ChecksumMismatch { .. }));
    }

    #[test]
    fn rejects_missing_integrity() {
        let no_integrity = KNOWN_GOOD.split("[integrity]").next().unwrap();
        let err = NodeConfig::verify_and_parse(no_integrity).unwrap_err();
        assert!(matches!(err, ConfigError::MissingIntegrity));
    }
}
```

Der `<HEX>`-Platzhalter wird durch die konkrete Checksumme ersetzt —
einmal `python -m harness.config_gen show tests/scenarios/some.toml`
oder `render_toml_str(spec)` aus Python aufrufen.
