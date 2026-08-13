"""
Thin SSH/SCP wrapper based on system ssh/scp binaries.

Design decisions:
- No paramiko dependency. `ssh` and `scp` are available on every
  developer machine and honour the local ~/.ssh/config, agent, and
  jumphosts without extra code.
- Key-based auth only (BatchMode=yes). Password auth would need a
  TTY and prompts, both unfriendly for a GUI. Point users to
  ssh-copy-id or ~/.ssh/config.
- Every call has a hard timeout to keep the UI responsive.

HardwareNode is a plain dataclass and gets JSON-serialised into
AppSettings.
"""
from __future__ import annotations

import json
import shlex
import subprocess
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class HardwareNode:
    node_id: int
    host: str
    user: str = "root"
    port: int = 22
    key_path: str = ""                       # empty -> default keys/agent
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


def _base_ssh_args(node: HardwareNode) -> list[str]:
    args = [
        "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", "ConnectTimeout=8",
        "-p", str(node.port),
    ]
    if node.key_path:
        args += ["-i", node.key_path]
    return args


class SshError(RuntimeError):
    def __init__(self, cmd: list[str], rc: int, output: str) -> None:
        super().__init__(f"ssh command failed (rc={rc}): {' '.join(shlex.quote(c) for c in cmd)}\n{output}")
        self.rc = rc
        self.output = output


def ssh_exec(node: HardwareNode, remote_cmd: str, timeout: float = 15.0) -> str:
    """Run one shell command on the node, return combined stdout+stderr."""
    cmd = ["ssh", *_base_ssh_args(node), f"{node.user}@{node.host}", remote_cmd]
    try:
        p = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired as e:
        raise SshError(cmd, -1, f"timeout after {timeout}s") from e
    output = (p.stdout or "") + (p.stderr or "")
    if p.returncode != 0:
        raise SshError(cmd, p.returncode, output.strip())
    return output


def scp_file(node: HardwareNode, local_path: Path, remote_path: str,
             timeout: float = 30.0) -> None:
    cmd = [
        "scp",
        "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", "ConnectTimeout=8",
        "-P", str(node.port),
    ]
    if node.key_path:
        cmd += ["-i", node.key_path]
    cmd += [str(local_path), f"{node.user}@{node.host}:{remote_path}"]
    try:
        p = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired as e:
        raise SshError(cmd, -1, f"timeout after {timeout}s") from e
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
        try:
            out.append(HardwareNode(**item))
        except TypeError:
            continue
    return out
