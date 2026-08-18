"""
Thin SSH/SCP wrapper based on system ssh/scp binaries.

Design decisions:
- No paramiko dependency. `ssh` and `scp` are available on every
  developer machine and honour the local ~/.ssh/config and jumphosts
  without extra code.
- Password-based auth (matches the Pi images' default SSH password
  for the internal test network -- password auth accepted, no
  pubkey-only requirement). `ssh`/`scp` can't take a password on the
  command line or read one from stdin non-interactively, so this
  shells out through `sshpass`, which drives the password prompt for
  us. The password itself is passed via the `SSHPASS` environment
  variable of the subprocess (not argv), so it never shows up in
  `ps`/`/proc/<pid>/cmdline`, only briefly in the child's own
  environment.
- `sshpass` has to be installed separately (e.g. `apt install
  sshpass`) -- it's not part of the base openssh-client package.
  Missing it raises a clear SshError instead of a bare
  FileNotFoundError.
- Every call has a hard timeout to keep the UI responsive.

HardwareNode is a plain dataclass and gets JSON-serialised into
AppSettings. Nodes saved by an older version of this tool (key_path
field instead of password) are silently dropped on load by
nodes_from_json -- re-add them via 'Configure hardware nodes…' with a
password.
"""
from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional


@dataclass
class HardwareNode:
    node_id: int
    host: str
    user: str = "root"
    port: int = 22
    password: str = ""                        # required -- see module docstring
    remote_binary: str = "/opt/voting/node"
    remote_config: str = "/opt/voting/node.toml"
    # Commands run via ssh. `{cfg}` is substituted with remote_config,
    # `{bin}` with remote_binary.
    start_cmd: str = "systemctl restart voting-node"
    stop_cmd: str = "systemctl stop voting-node || true"

    def resolved_start_cmd(self) -> str:
        return self.start_cmd.format(bin=self.remote_binary, cfg=self.remote_config)

    def resolved_stop_cmd(self) -> str:
        return self.stop_cmd.format(bin=self.remote_binary, cfg=self.remote_config)


class SshError(RuntimeError):
    def __init__(self, cmd: list[str], rc: int, output: str) -> None:
        super().__init__(f"ssh command failed (rc={rc}): {' '.join(shlex.quote(c) for c in cmd)}\n{output}")
        self.rc = rc
        self.output = output


def _require_sshpass() -> str:
    path = shutil.which("sshpass")
    if not path:
        raise SshError(
            ["sshpass"], -1,
            "sshpass not found on PATH -- install it (e.g. `apt install "
            "sshpass` / `brew install hudochenkov/sshpass/sshpass`) to use "
            "password-based deploy.",
        )
    return path


def _sshpass_env(node: HardwareNode) -> dict:
    env = os.environ.copy()
    env["SSHPASS"] = node.password
    return env


def _password_ssh_opts() -> list[str]:
    # BatchMode=yes (as used for key auth) would refuse a password
    # prompt entirely, so it's deliberately absent here. sshpass drives
    # the prompt; PreferredAuthentications/PubkeyAuthentication make
    # sure we don't first burn the ConnectTimeout on a key handshake
    # against a server that also offers pubkey auth.
    return [
        "-o", "PreferredAuthentications=password",
        "-o", "PubkeyAuthentication=no",
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", "ConnectTimeout=8",
    ]


def ssh_exec(node: HardwareNode, remote_cmd: str, timeout: float = 15.0) -> str:
    """Run one shell command on the node, return combined stdout+stderr."""
    sshpass = _require_sshpass()
    cmd = [
        sshpass, "-e", "ssh",
        *_password_ssh_opts(),
        "-p", str(node.port),
        f"{node.user}@{node.host}", remote_cmd,
    ]
    try:
        p = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False,
            env=_sshpass_env(node),
        )
    except subprocess.TimeoutExpired as e:
        raise SshError(cmd, -1, f"timeout after {timeout}s") from e
    except FileNotFoundError as e:
        raise SshError(cmd, -1, f"could not start ssh/sshpass: {e}") from e
    output = (p.stdout or "") + (p.stderr or "")
    if p.returncode != 0:
        raise SshError(cmd, p.returncode, output.strip())
    return output


def scp_file(node: HardwareNode, local_path: Path, remote_path: str,
             timeout: float = 30.0) -> None:
    sshpass = _require_sshpass()
    cmd = [
        sshpass, "-e", "scp",
        *_password_ssh_opts(),
        "-P", str(node.port),
        str(local_path), f"{node.user}@{node.host}:{remote_path}",
    ]
    try:
        p = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False,
            env=_sshpass_env(node),
        )
    except subprocess.TimeoutExpired as e:
        raise SshError(cmd, -1, f"timeout after {timeout}s") from e
    except FileNotFoundError as e:
        raise SshError(cmd, -1, f"could not start scp/sshpass: {e}") from e
    if p.returncode != 0:
        raise SshError(cmd, p.returncode, (p.stdout or "") + (p.stderr or ""))


def scp_bytes(node: HardwareNode, data: bytes, remote_path: str,
              timeout: float = 30.0) -> None:
    """Convenience: write bytes to a temp file, scp it, delete."""
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp.write(data)
        tmp_path = Path(tmp.name)
    try:
        scp_file(node, tmp_path, remote_path, timeout=timeout)
    finally:
        try:
            tmp_path.unlink()
        except OSError:
            pass


def check_reachable(node: HardwareNode) -> tuple[bool, str]:
    """Quick liveness check: `ssh host echo ok`. Returns (ok, message)."""
    try:
        out = ssh_exec(node, "echo ok", timeout=8.0)
        return (out.strip() == "ok", out.strip() or "ok")
    except SshError as e:
        return (False, e.output or str(e))


# ---- Serialisation for AppSettings -----------------------------------

def nodes_to_json(nodes: list[HardwareNode]) -> str:
    return json.dumps([asdict(n) for n in nodes])


def nodes_from_json(s: str) -> list[HardwareNode]:
    if not s:
        return []
    try:
        raw = json.loads(s)
    except json.JSONDecodeError:
        return []
    out: list[HardwareNode] = []
    for item in raw:
        item = {k: v for k, v in item.items() if k != "key_path"}  # drop legacy field
        try:
            out.append(HardwareNode(**item))
        except TypeError:
            continue
    return out
