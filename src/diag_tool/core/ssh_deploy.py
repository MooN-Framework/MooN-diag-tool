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
    enabled: bool = True                       # participates in deploy/sweep/mode when True
    user: str = "root"
    port: int = 22
    password: str = ""                        # required -- see module docstring
    remote_binary: str = "/opt/voting/node"
    remote_config: str = "/opt/voting/node.toml"
    remote_log: str = "/opt/voting/node.log"   # stdout+stderr target of start_cmd
    # Commands run via ssh. `{bin}`/`{cfg}`/`{log}` are substituted with
    # remote_binary/remote_config/remote_log. Default is plain process
    # management (kill + exec the binary directly) rather than a
    # systemd unit -- there's no service installed on the node images.
    # Both stay per-node editable strings so a future deployment (e.g.
    # once a systemd unit does exist) can switch back without a code
    # change: e.g. start_cmd="systemctl restart voting-node",
    # stop_cmd="systemctl stop voting-node || true".
    start_cmd: str = "nohup {bin} --config {cfg} > {log} 2>&1 < /dev/null & disown"
    stop_cmd: str = "pkill -f {bin} || true"

    def resolved_start_cmd(self) -> str:
        return self.start_cmd.format(bin=self.remote_binary, cfg=self.remote_config, log=self.remote_log)

    def resolved_stop_cmd(self) -> str:
        return self.stop_cmd.format(bin=self.remote_binary, cfg=self.remote_config, log=self.remote_log)


def enabled_nodes(nodes: list[HardwareNode]) -> list[HardwareNode]:
    """The subset that actually participates in deploy/sweep/mode
    derivation. A node stays in the configured list (and keeps its
    password/paths/etc.) when disabled -- it's just excluded from
    every hardware action and from the nominal/participant count,
    without having to delete and re-enter it later."""
    return sorted((n for n in nodes if n.enabled), key=lambda n: n.node_id)



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


def scp_get(node: HardwareNode, remote_path: str, local_path: Path,
            timeout: float = 30.0) -> None:
    """Pull a file FROM the node to local_path -- reverse direction of
    scp_file. Used to fetch a node's remote_log back for inspection."""
    sshpass = _require_sshpass()
    local_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        sshpass, "-e", "scp",
        *_password_ssh_opts(),
        "-P", str(node.port),
        f"{node.user}@{node.host}:{remote_path}", str(local_path),
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


def is_process_running(node: HardwareNode, timeout: float = 8.0) -> bool:
    """Best-effort post-start liveness check via `ps aux | grep`.

    `start_cmd`'s default `nohup {bin} ... & disown` almost always
    exits 0 from the shell's point of view regardless of whether {bin}
    itself actually launched successfully -- backgrounding a command
    "succeeds" even if that command immediately crashes (missing +x,
    wrong path, bad config checksum, bind failure on the configured
    interface, ...). ssh_exec reporting rc=0 for start_cmd is
    therefore NOT proof the node is actually running; check for real
    with this right after starting it.

    Deliberately `ps aux | grep` instead of `pgrep` -- procps isn't
    guaranteed present on a stripped-down node image, `ps` is.
    """
    cmd = f"ps aux | grep -F {shlex.quote(node.remote_binary)} | grep -v grep || true"
    try:
        out = ssh_exec(node, cmd, timeout=timeout)
    except SshError:
        return False
    return bool(out.strip())


def tail_remote_log(node: HardwareNode, lines: int = 20, timeout: float = 8.0) -> str:
    """Last few lines of a node's remote_log -- for surfacing *why* a
    start failed instead of just reporting silence."""
    cmd = f"tail -n {lines} {shlex.quote(node.remote_log)} 2>&1 || echo '(no log at {node.remote_log})'"
    try:
        return ssh_exec(node, cmd, timeout=timeout)
    except SshError as e:
        return f"(could not read log: {e.output})"


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
