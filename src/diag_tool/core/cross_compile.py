"""
Cross-compile helper for the Rust binary.

Host builds (no target triple, e.g. for simulated mode) run plain
`cargo build --release [--features=...]` in the repository root.

Cross builds (target triple set, e.g. aarch64-unknown-linux-gnu for
the Pi nodes) run through `cross build` instead of bare
`cargo build --target=...` -- `cross` builds inside a Docker/Podman
container that already has the matching linker/toolchain for the
target, so the host doesn't need e.g. gcc-aarch64-linux-gnu installed.
Requires the `cross` binary (`cargo install cross --locked`) and a
running Docker/Podman on the machine that runs the GUI.

Output layout is identical either way (`target/<triple>/release/...`
or `target/release/...`), since `cross` mounts the repo and drives
the same cargo underneath.

Streams stdout+stderr line by line to a callback. The caller uses the
callback to feed a GUI text view (in the UI thread).

Returns the path to the produced binary on success, or None with the
last error message written to the callback.
"""
from __future__ import annotations

import os
import subprocess
import threading
from pathlib import Path
from typing import Callable, Optional


class CrossBuild:
    def __init__(self, repo_path: Path, target_triple: str = "",
                 features: Optional[list[str]] = None,
                 binary_name: str = "node") -> None:
        self.repo_path = Path(repo_path)
        self.target_triple = target_triple.strip()
        self.features = features or []
        self.binary_name = binary_name
        self._proc: Optional[subprocess.Popen] = None
        self._reader: Optional[threading.Thread] = None

    def _tool(self) -> str:
        # Host build (no triple) doesn't need a container -- only
        # actual cross-compilation goes through `cross`.
        return "cross" if self.target_triple else "cargo"

    def _cmd(self) -> list[str]:
        cmd = [self._tool(), "build", "--release"]
        if self.target_triple:
            cmd += ["--target", self.target_triple]
        if self.features:
            cmd += ["--features", ",".join(self.features)]
        cmd += ["--bin", self.binary_name]
        return cmd

    def expected_binary_path(self) -> Path:
        """Where cargo/cross will drop the binary."""
        base = self.repo_path / "target"
        if self.target_triple:
            base = base / self.target_triple
        return base / "release" / self.binary_name

    def is_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def cancel(self) -> None:
        if self.is_running():
            assert self._proc is not None
            self._proc.terminate()

    def start(self, on_line: Callable[[str], None],
              on_done: Callable[[int, Optional[Path]], None]) -> None:
        """Kick off the build; both callbacks are invoked from the worker."""
        if not self.repo_path.is_dir():
            on_line(f"[error] repo path not found: {self.repo_path}")
            on_done(-1, None)
            return

        cmd = self._cmd()
        env = os.environ.copy()
        env.setdefault("CARGO_TERM_COLOR", "always")  # our test-tab-style colouriser handles ANSI
        on_line(f"$ (cwd={self.repo_path}) " + " ".join(cmd))
        try:
            self._proc = subprocess.Popen(
                cmd, cwd=self.repo_path, env=env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1,
            )
        except OSError as e:
            hint = " (is `cross` installed? `cargo install cross --locked`, needs Docker/Podman running)" if self._tool() == "cross" else ""
            on_line(f"[error] {self._tool()} could not be started: {e}{hint}")
            on_done(-1, None)
            return

        def reader() -> None:
            assert self._proc and self._proc.stdout
            for line in self._proc.stdout:
                on_line(line.rstrip("\n"))
            rc = self._proc.wait()
            path: Optional[Path] = None
            if rc == 0:
                p = self.expected_binary_path()
                if p.exists():
                    path = p
                    on_line(f"[ok] built: {p}")
                else:
                    on_line(f"[warn] rc=0 but binary not found at {p}")
            else:
                on_line(f"[error] cargo exit rc={rc}")
            on_done(rc, path)

        self._reader = threading.Thread(target=reader, name="cargo-build", daemon=True)
        self._reader.start()
