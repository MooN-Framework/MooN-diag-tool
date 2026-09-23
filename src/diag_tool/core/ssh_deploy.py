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
import time
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
    remote_log_dir: str = "/opt/voting/logs"   # THE one directory to know about, passed as the node
                                                # binary's `--log-dir`: the framework itself maintains
                                                # `node_<id>_current.log` as a symlink to the latest
                                                # session's log inside here (see current_log_path()).
                                                # remote_log (below) is derived from this, not separately
                                                # configurable -- there's only one location to think about.
    # Commands run via ssh. `{bin}`/`{cfg}`/`{log}`/`{log_dir}` are
    # substituted with remote_binary/remote_config/remote_log/
    # remote_log_dir. Default is plain process management (kill + exec
    # the binary directly) rather than a systemd unit -- there's no
    # service installed on the node images. `--log-dir {log_dir}`
    # enables the framework's own per-session file logging; `{log}`
    # stays as a raw stdout+stderr catch-all (covers anything printed
    # before the tracing subscriber is initialized, e.g. a config-load
    # failure, plus the default Rust panic hook) written into the same
    # log_dir. start_cmd/stop_cmd stay per-node editable strings so a
    # future deployment (e.g. once a systemd unit does exist) can
    # switch back without a code change: e.g.
    # start_cmd="systemctl restart voting-node",
    # stop_cmd="systemctl stop voting-node || true".
    start_cmd: str = "nohup {bin} --config {cfg} --log-dir {log_dir} > {log} 2>&1 < /dev/null & disown"
    stop_cmd: str = "pkill -f '^{bin}' || true"  # anchored: an unanchored
                                                  # pattern also matches the remote
                                                  # `bash -c "pkill -f ..."` shell
                                                  # itself and kills it (rc 255)

    @property
    def remote_log(self) -> str:
        """Raw stdout+stderr catch-all path -- always inside
        remote_log_dir, not separately configurable."""
        return f"{self.remote_log_dir.rstrip('/')}/stdout.log"

    def resolved_start_cmd(self) -> str:
        cmd_template = self.start_cmd
        if "--log-dir" not in cmd_template and "{cfg}" in cmd_template:
            # Auto-repair: a start_cmd saved before --log-dir became part
            # of the default template (or hand-edited without it) would
            # otherwise silently run the node with no file logging at all
            # -- see validate_logging(). Insert it right after {cfg}
            # rather than overwriting the whole command, so anything else
            # about the saved command (env vars, extra flags, a
            # completely different redirect target) survives untouched.
            # Commands that don't reference {cfg} at all (e.g. a
            # systemd-based start_cmd, see the class docstring) aren't
            # touched -- there's no safe insertion point, and a systemd
            # unit would define its own logging anyway.
            cmd_template = cmd_template.replace("{cfg}", "{cfg} --log-dir {log_dir}", 1)
        return cmd_template.format(
            bin=self.remote_binary, cfg=self.remote_config,
            log=self.remote_log, log_dir=self.remote_log_dir,
        )

    def resolved_stop_cmd(self) -> str:
        return self.stop_cmd.format(
            bin=self.remote_binary, cfg=self.remote_config,
            log=self.remote_log, log_dir=self.remote_log_dir,
        )

    def current_log_path(self) -> str:
        """Remote path of the `--log-dir` "current session" symlink --
        the framework's own node.rs maintains this to always point at
        `node_<own_id>_current.log`, the latest session's log,
        regardless of how many restarts have happened. THE file to
        read for "what is this node doing right now"."""
        return f"{self.remote_log_dir.rstrip('/')}/node_{self.node_id}_current.log"

    def validate_logging(self) -> Optional[str]:
        """None if the SAVED start_cmd passes --log-dir to the binary.
        Otherwise a human-readable heads-up -- resolved_start_cmd()
        above already auto-repairs this for the actual run (as long as
        {cfg} appears in the template), so this is informational, not
        fatal: it just nudges towards fixing the saved config in
        "Configure hardware nodes…" so the auto-repair (and this
        message) stops being necessary.
        """
        if "--log-dir" not in self.start_cmd:
            if "{cfg}" in self.start_cmd:
                return (
                    f"{self.host}: saved start_cmd doesn't pass \"--log-dir {{log_dir}}\" "
                    f"to the binary -- auto-added for this run. Fix start_cmd in "
                    f"\"Configure hardware nodes…\" to stop seeing this."
                )
            return (
                f"{self.host}: start_cmd doesn't pass \"--log-dir {{log_dir}}\" to the "
                f"binary, and doesn't reference {{cfg}} either so this can't be "
                f"auto-repaired -- current_log_path() ({self.current_log_path()}) will "
                f"never be written. Fix start_cmd in \"Configure hardware nodes…\"."
            )
        return None


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


def spawn_tail_process(node: HardwareNode, remote_path: str) -> subprocess.Popen:
    """Spawn a persistent `ssh ... tail -n +1 -F <remote_path>` subprocess
    and return it (stdout=PIPE, text mode, line-buffered) for the caller
    to read line-by-line, same shape as a local Popen's stdout.

    Used by the hardware test fabric (harness/hw_node.py) to stream a
    node's `--log-dir` current-session log the same way harness/node.py
    streams a local subprocess's stdout -- this is what lets
    Node.wait_for_log() work against real hardware.

    `-F` (capital, "follow retry"), not `-f`: the framework's
    init_logging() removes and re-creates the `node_<id>_current.log`
    symlink (pointing at a brand-new session file) on every process
    start, which looks like a rename/removal from `tail`'s point of
    view. Only `-F` reopens the file by path after that instead of
    keeping the stale, now-detached file descriptor -- so this same
    tail process keeps working across a remote restart_node() without
    needing to be respawned.

    `-n +1` starts from the first line of whatever is already in the
    file at connect time (mirrors Node.wait_for_log's "scan existing
    lines first" semantics) instead of tail's default "last 10 lines".

    Caller owns the returned process: terminate()/kill() it when done
    (e.g. RemoteNode.stop()) to close the SSH connection.
    """
    sshpass = _require_sshpass()
    remote_cmd = f"tail -n +1 -F {shlex.quote(remote_path)}"
    cmd = [
        sshpass, "-e", "ssh",
        *_password_ssh_opts(),
        "-p", str(node.port),
        f"{node.user}@{node.host}", remote_cmd,
    ]
    return subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        env=_sshpass_env(node), text=True, bufsize=1,
    )


def write_hw_nodes_file(nodes: list[HardwareNode], path: Path) -> None:
    """Write `nodes` as JSON to `path` with 0600 permissions (contains
    plaintext SSH passwords -- same sensitivity as AppSettings'
    hardware_nodes_json, just handed to a pytest subprocess via
    --hw-nodes-file instead of staying in-process). Creates the file
    with restrictive permissions from the start rather than
    chmod-after-write, so there's no window where it's
    world/group-readable."""
    data = nodes_to_json(nodes).encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        raise


def ssh_exec(node: HardwareNode, remote_cmd: str, timeout: float = 30.0) -> str:
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
             timeout: float = 60.0) -> None:
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
              timeout: float = 60.0) -> None:
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
            timeout: float = 60.0) -> None:
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


def scp_get_dir(node: HardwareNode, remote_dir: str, local_dir: Path,
                 timeout: float = 90.0) -> None:
    """Pull an entire remote directory back recursively (`scp -r`) --
    used to fetch a node's whole `remote_log_dir` (all per-session log
    files, not just the current one) for offline inspection. Silently
    no-ops (raises SshError, caller decides how to report it) if the
    directory doesn't exist yet, e.g. the node has never been started
    with --log-dir."""
    sshpass = _require_sshpass()
    local_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        sshpass, "-e", "scp", "-r",
        *_password_ssh_opts(),
        "-P", str(node.port),
        f"{node.user}@{node.host}:{remote_dir}", str(local_dir),
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


def is_process_running(node: HardwareNode, timeout: float = 15.0) -> bool:
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


def tail_remote_log(node: HardwareNode, lines: int = 20, timeout: float = 15.0) -> str:
    """Last few lines of a node's raw remote_log (stdout+stderr of
    start_cmd) -- for surfacing *why* a start failed instead of just
    reporting silence. Catches anything printed before the tracing
    subscriber comes up (config-load errors, panics); for everything
    after that, tail_remote_current_log gives the nicer formatted
    output."""
    cmd = f"tail -n {lines} {shlex.quote(node.remote_log)} 2>&1 || echo '(no log at {node.remote_log})'"
    try:
        return ssh_exec(node, cmd, timeout=timeout)
    except SshError as e:
        return f"(could not read log: {e.output})"


def tail_remote_current_log(node: HardwareNode, lines: int = 20, timeout: float = 15.0) -> str:
    """Last few lines of the node binary's own structured file log
    (`--log-dir`'s `node_<id>_current.log` symlink) -- has levels,
    target and thread ids, and (unlike remote_log) isn't truncated by
    a `>` shell redirect on every restart since each session gets its
    own file. Empty/missing until the node has actually started
    file logging (e.g. old binary without --log-dir support, or the
    log dir isn't writable)."""
    path = node.current_log_path()
    cmd = f"tail -n {lines} {shlex.quote(path)} 2>&1 || echo '(no log at {path})'"
    try:
        return ssh_exec(node, cmd, timeout=timeout)
    except SshError as e:
        return f"(could not read log: {e.output})"


def tail_remote_logs(node: HardwareNode, lines: int = 20, timeout: float = 15.0) -> str:
    """Combined tail: structured current-session log first (the useful
    one in the common case), raw stdout/stderr catch-all appended
    below it. Use this wherever a failure needs to be explained to the
    user -- it's the union of what tail_remote_log and
    tail_remote_current_log show individually."""
    current = tail_remote_current_log(node, lines=lines, timeout=timeout)
    raw = tail_remote_log(node, lines=lines, timeout=timeout)
    return (
        f"--- {node.current_log_path()} (tail) ---\n{current}\n"
        f"--- {node.remote_log} (raw stdout/stderr, tail) ---\n{raw}"
    )


def remote_log_dir_report(node: HardwareNode, timeout: float = 15.0) -> str:
    """`id` plus `ls -la` of remote_log_dir -- for explaining a failed
    start: a log dir the SSH user can't write to makes start_cmd's
    redirect fail before the binary is ever executed, which neither
    log tail can show (neither file gets written)."""
    cmd = f"id; ls -la {shlex.quote(node.remote_log_dir)} 2>&1"
    try:
        return ssh_exec(node, cmd, timeout=timeout).rstrip()
    except SshError as e:
        return f"(could not inspect log dir: {e.output})"


def ensure_remote_dirs(node: HardwareNode, timeout: float = 15.0) -> None:
    """mkdir -p the parent directories of remote_binary/remote_config/
    remote_log/remote_log_dir and make sure the SSH user can write to them.

    scp (the legacy protocol this module shells out to) can NOT create
    missing destination directories -- it just fails with a generic
    "dest open ... Failure" if the parent doesn't exist. This matters
    especially on the MooN images, where /opt/moon is a tmpfs that starts
    empty on every boot: subdirectories like /opt/moon/bin are normally
    only created by moon-pkg-load.service when it extracts a package.

    Ownership matters as well: moon-pkg-load.service and moon-node.service
    run as root, so a directory one of them created earlier (typically
    the production service's own --log-dir) is not writable for a
    non-root SSH user. start_cmd's `> {log}` redirect then fails inside
    the backgrounded job while the shell itself still exits 0 (`disown`
    succeeds), so the node binary is silently never executed and the
    only visible symptom is "process not running after start_cmd".
    Take ownership via passwordless sudo and verify writability
    explicitly, so this surfaces here as a clear error instead. Root can
    still write to the chowned directories, so the production service
    is unaffected.
    """
    dirs = " ".join(sorted({
        shlex.quote(str(Path(node.remote_binary).parent)),
        shlex.quote(str(Path(node.remote_config).parent)),
        shlex.quote(str(Path(node.remote_log).parent)),
        shlex.quote(node.remote_log_dir),
    }))
    cmd = f"mkdir -p {dirs} 2>/dev/null || sudo -n mkdir -p {dirs}; "
    if node.user != "root":
        cmd += f'sudo -n chown "$(id -u):$(id -g)" {dirs} 2>/dev/null; '
    cmd += (
        f'for d in {dirs}; do test -w "$d" || '
        f'{{ echo "not writable for $(id -un): $d"; ls -ld "$d"; exit 1; }}; done'
    )
    ssh_exec(node, cmd, timeout=timeout)


def check_reachable(node: HardwareNode) -> tuple[bool, str]:
    """Quick liveness check: `ssh host echo ok`. Returns (ok, message)."""
    try:
        out = ssh_exec(node, "echo ok", timeout=15.0)
        return (out.strip() == "ok", out.strip() or "ok")
    except SshError as e:
        return (False, e.output or str(e))


def stop_moon_node_service(node: HardwareNode, timeout: float = 30.0) -> None:
    """Best-effort: stop the production `moon-node.service` systemd unit
    (see ui/package_tab.py's Deploy flow / core/moon_package.py) before
    the test harness starts its own directly-exec'd binary on the same
    node via HardwareNode.start_cmd/stop_cmd.

    These are two independent things that can end up running the SAME
    node role on the same hardware at once: moon-node.service runs the
    production binary from /opt/moon/bin/node, while the harness runs
    whatever's at HardwareNode.remote_binary (default /opt/voting/node)
    via plain nohup+exec. resolved_stop_cmd()'s `pkill -f {bin}` only
    matches the harness's own binary path -- it does NOT touch
    moon-node.service, so on a node that was ever deployed to via the
    Package tab, the production binary keeps running in parallel with
    the harness's test binary, both fighting over the same multicast
    group/port. Call this once per node right before the harness's own
    push+start sequence.

    `|| true`: a node that was never given the production package
    doesn't have this unit installed at all -- systemctl then reports
    "not found", which is fine, there's nothing to stop.
    """
    try:
        ssh_exec(node, "sudo systemctl stop moon-node.service || true", timeout=timeout)
    except SshError:
        pass  # best-effort -- if the node's unreachable, the push/start right after will surface that


def get_os_uptime(node: HardwareNode, timeout: float = 15.0) -> Optional[float]:
    """Seconds since the node's OS booted, read from `/proc/uptime`
    (its first field). Returns None on any SSH failure or unparsable
    output -- caller decides how to display that (e.g. "n/a")."""
    try:
        out = ssh_exec(node, "cat /proc/uptime", timeout=timeout)
    except SshError:
        return None
    try:
        return float(out.strip().split()[0])
    except (ValueError, IndexError):
        return None


def get_service_uptime(
    node: HardwareNode, unit: str = "moon-node.service", timeout: float = 15.0,
) -> Optional[float]:
    """Seconds since `unit` last entered the active state, per systemd --
    i.e. how long the actual moon-node.service has been running, as
    opposed to get_os_uptime() which only says when the machine booted
    (the service could have been (re)started long after boot, or have
    crashed and restarted since -- OS uptime alone would hide that).
    Returns None if the unit isn't currently active, or on any
    SSH/parse failure.

    Computed from `systemctl show`'s ActiveEnterTimestampMonotonic
    (microseconds since boot when the unit last became active) combined
    with /proc/uptime (current seconds since boot) -- both come from
    the same boot-relative clock, so subtracting them avoids parsing
    any wall-clock timestamp/timezone at all.
    """
    try:
        out = ssh_exec(
            node,
            "cat /proc/uptime && echo --- && "
            f"systemctl show -p ActiveState -p ActiveEnterTimestampMonotonic "
            f"--value {shlex.quote(unit)}",
            timeout=timeout,
        )
    except SshError:
        return None
    try:
        uptime_part, status_part = out.split("---", 1)
        boot_uptime_s = float(uptime_part.strip().split()[0])
        lines = [ln.strip() for ln in status_part.strip().splitlines() if ln.strip()]
        active_state, active_enter_us = lines[0], lines[1]
        if active_state != "active":
            return None
        return boot_uptime_s - (int(active_enter_us) / 1_000_000)
    except (ValueError, IndexError):
        return None


def wait_for_reachable(node: HardwareNode, timeout: float = 90.0, interval: float = 3.0) -> bool:
    """Poll check_reachable() until it succeeds or timeout runs out.

    Used after `sudo reboot`: SSH refuses connections for a while during
    boot (network not up yet, sshd not started yet), so a single
    check_reachable() called right after issuing the reboot would almost
    always report unreachable even on a node that comes back up fine.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        ok, _ = check_reachable(node)
        if ok:
            return True
        time.sleep(interval)
    return False


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
        # key_path: legacy field, dropped long ago. remote_log: used to
        # be its own stored field, now derived from remote_log_dir (see
        # HardwareNode.remote_log property) -- a config saved by an
        # older version of this tool would otherwise fail to load here
        # with a TypeError (unexpected keyword argument).
        item = {k: v for k, v in item.items() if k not in ("key_path", "remote_log")}
        try:
            out.append(HardwareNode(**item))
        except TypeError:
            continue
    return out
