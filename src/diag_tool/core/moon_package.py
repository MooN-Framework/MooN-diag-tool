"""
Build a signed .moonpkg package by shelling out to the pi-gen repo's own
tools/build-moon-package.sh -- deliberately NOT a from-scratch
reimplementation of the tar/sha256/openssl-sign steps, so packages built
here are byte-for-byte what that already-tested script produces.

Format (from moon-pkg-load.sh, the on-target verifier):
    .moonpkg is a plain uncompressed tar of:
      manifest.json   {"name", "version", "created", "payload_sha256"}
      manifest.sig     raw ed25519 signature of manifest.json
      payload.tar.gz   extracted verbatim to /opt/moon on the node
    Expected payload layout: bin/node + config/node.toml (matches
    ExecStart in moon-node.service: /opt/moon/bin/node --config
    /opt/moon/config/node.toml).

Only the signed (prod) flow is supported here -- the private key never
leaves this machine, it's just a path handed to `openssl` via the
existing shell script, same as running that script by hand.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Callable, Optional


class PackageBuildError(RuntimeError):
    pass


def build_payload_dir(bin_path: Path, config_toml: str, workdir: Path) -> Path:
    """Lay out bin/node + config/node.toml under workdir, matching what
    moon-pkg-load.sh extracts to /opt/moon. Returns the payload dir."""
    payload_dir = workdir / "payload"
    (payload_dir / "bin").mkdir(parents=True, exist_ok=True)
    (payload_dir / "config").mkdir(parents=True, exist_ok=True)
    shutil.copy2(bin_path, payload_dir / "bin" / "node")
    (payload_dir / "bin" / "node").chmod(0o755)
    (payload_dir / "config" / "node.toml").write_text(config_toml)
    return payload_dir


def build_package(
    pi_gen_repo: Path,
    signing_key: Path,
    name: str,
    version: str,
    payload_dir: Path,
    out_path: Path,
    on_line: Optional[Callable[[str], None]] = None,
) -> None:
    """Run tools/build-moon-package.sh -k ... -n ... -v ... -p ... -o ...
    Raises PackageBuildError with the script's own stderr/stdout on
    failure -- that script already validates key/payload-dir existence
    itself, no need to duplicate those checks here."""
    script = pi_gen_repo / "tools" / "build-moon-package.sh"
    if not script.is_file():
        raise PackageBuildError(
            f"tools/build-moon-package.sh not found under {pi_gen_repo} "
            f"-- check 'pi-gen repository' in Settings."
        )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "bash", str(script),
        "-k", str(signing_key),
        "-n", name,
        "-v", version,
        "-p", str(payload_dir),
        "-o", str(out_path),
    ]
    if on_line:
        on_line("$ " + " ".join(cmd))
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired as e:
        raise PackageBuildError(f"build-moon-package.sh timed out: {e}") from e
    except OSError as e:
        raise PackageBuildError(f"could not run build-moon-package.sh: {e}") from e
    output = (p.stdout or "") + (p.stderr or "")
    if on_line and output:
        for line in output.splitlines():
            on_line(line)
    if p.returncode != 0:
        raise PackageBuildError(f"build-moon-package.sh exit rc={p.returncode}: {output.strip()}")
    if not out_path.is_file():
        raise PackageBuildError(f"build-moon-package.sh reported success but {out_path} wasn't created")
