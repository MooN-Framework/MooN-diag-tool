"""
Hardware-mode counterpart to harness.node.Node.

Same duck-typed API (wait_for_log / log_lines / is_running / stop /
exit_code / node_id) as the local-subprocess Node in harness/node.py,
but backed by SSH instead of a local Popen: RemoteNode tails the
node's structured `--log-dir` current-session log over a persistent
`ssh ... tail -F` connection (see core.ssh_deploy.spawn_tail_process),
and is_running()/stop() go over `ssh_exec` against the node's
configured start_cmd/stop_cmd.

Requires the node binary to have actually been started with
`--log-dir` (the default HardwareNode.start_cmd does this -- see
core/ssh_deploy.py). Without a reachable current-log file,
wait_for_log() just times out every call, same as the old _NullNode
stub in conftest.py used to unconditionally.

Restart semantics: call start_tailing() exactly ONCE per RemoteNode,
right after construction. Fabric.restart_node() (see conftest.py's
_HardwareFabric) only stop_cmd/start_cmd's the remote process -- it
does NOT touch the tail subprocess. `tail -F` re-opens the
current-log path by name when the framework repoints the
`node_<id>_current.log` symlink at a new session file on the next
start, so the SAME tail process keeps streaming lines across a
restart, exactly like the simulated Node's append-mode local log file
does (see harness/fabric.py's restart_node docstring). This is what
lets a test observe a log line from *after* a restart_node() call
using the very same Node object.
"""
from __future__ import annotations

import re
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Optional, Pattern

from diag_tool.core.ssh_deploy import (
    HardwareNode,
    SshError,
    is_process_running,
    spawn_tail_process,
    ssh_exec,
)


@dataclass
class RemoteNode:
    node_id: int
    hw: HardwareNode

    _tail_proc: Optional[subprocess.Popen] = field(default=None, init=False, repr=False)
    _reader_thread: Optional[threading.Thread] = field(default=None, init=False, repr=False)
    _lines: deque = field(default_factory=lambda: deque(maxlen=100_000), init=False, repr=False)
    _lines_lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    _new_line_event: threading.Event = field(default_factory=threading.Event, init=False, repr=False)
    # Monotonic count of every line ever appended, NOT reset when the
    # bounded _lines deque evicts old entries -- see line_count / the
    # `after` param on wait_for_log for why this matters (stale-match
    # bug across restarts).
    _total_lines_seen: int = field(default=0, init=False, repr=False)
    exit_code: Optional[int] = field(default=None, init=False)

    def start_tailing(self) -> None:
        """Spawn the persistent `ssh ... tail -F <current-log>`
        process. Call once, right after construction -- NOT on every
        restart_node(), see module docstring."""
        self._tail_proc = spawn_tail_process(self.hw, self.hw.current_log_path())
        self._reader_thread = threading.Thread(
            target=self._read_loop,
            name=f"hw-node-{self.node_id}-tail",
            daemon=True,
        )
        self._reader_thread.start()

    def _read_loop(self) -> None:
        assert self._tail_proc and self._tail_proc.stdout
        for line in self._tail_proc.stdout:
            line = line.rstrip("\n")
            with self._lines_lock:
                self._lines.append(line)
                self._total_lines_seen += 1
            self._new_line_event.set()

    @property
    def line_count(self) -> int:
        """Monotonic count of every log line ever seen on this node's
        tail, independent of the (bounded, maxlen=100_000) _lines
        buffer's own eviction. Snapshot this right before restarting the
        node and pass it as wait_for_log(after=...) so the wait only
        matches lines produced by the NEW session -- otherwise, once a
        pattern has matched once (e.g. the very first successful boot),
        it keeps matching that same stale buffered line on every later
        restart, making a readiness check report success instantly
        without ever confirming the new session actually came up. This
        is exactly what made restart_all() (see conftest.py's
        _HardwareFabric) appear to work while every test after the
        first one actually ran against a not-yet-ready system.
        """
        with self._lines_lock:
            return self._total_lines_seen

    def wait_for_log(
        self, pattern: str | Pattern, timeout: float = 5.0, after: int = 0,
    ) -> Optional[re.Match]:
        """Identical semantics to harness.node.Node.wait_for_log: scan
        already-seen lines first (covers events that already happened
        before this call), then wait for new ones until timeout.

        `after`: only lines with a global sequence number >= `after`
        (see line_count) are considered -- pass a line_count snapshot
        taken right before a restart to skip stale pre-restart lines.
        Default 0 preserves the original "match anything ever seen"
        behaviour every existing caller relies on.
        """
        pat = re.compile(pattern) if isinstance(pattern, str) else pattern
        deadline = time.monotonic() + timeout

        while True:
            with self._lines_lock:
                # _lines may have evicted lines older than `after` once
                # it's full (maxlen) -- reconstruct how many were
                # dropped so the slice stays correct regardless.
                evicted = max(0, self._total_lines_seen - len(self._lines))
                start = max(0, after - evicted)
                candidates = list(self._lines)[start:]
            for ln in candidates:
                m = pat.search(ln)
                if m:
                    return m
            if time.monotonic() >= deadline:
                return None
            self._new_line_event.wait(timeout=0.1)
            self._new_line_event.clear()

    @property
    def log_lines(self) -> list[str]:
        with self._lines_lock:
            return list(self._lines)

    def is_running(self) -> bool:
        """Real liveness check via SSH (`ps aux | grep`), unlike the
        old _NullNode stub which always returned True. Short timeout
        since this may be polled from wait_node_died-style loops --
        callers wanting the definitive, framework-semantics answer
        ("does the node still answer GetStatus") should prefer
        fabric.diag over this, same guidance as for simulated Node."""
        try:
            return is_process_running(self.hw, timeout=5.0)
        except SshError:
            return False

    def stop(self, timeout: float = 2.0) -> None:
        """Stop the remote node process (stop_cmd) and close the
        local tail subprocess. Does NOT get called on plain fixture
        teardown for hardware nodes -- see _HardwareFabric.stop_all in
        conftest.py, which only tears down tailing, not the remote
        processes (those are expected to keep running for the rest of
        the pytest session). This is for tests that explicitly want a
        node gone."""
        try:
            ssh_exec(self.hw, self.hw.resolved_stop_cmd(), timeout=15.0)
        except SshError:
            pass
        self._stop_tailing(timeout=timeout)

    def _stop_tailing(self, timeout: float = 2.0) -> None:
        if not self._tail_proc:
            return
        if self._tail_proc.poll() is None:
            self._tail_proc.terminate()
            try:
                self._tail_proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self._tail_proc.kill()
        if self._reader_thread:
            self._reader_thread.join(timeout=1.0)
