"""
Open a local path (file or folder) in the desktop's default handler.

Linux-only by design, same as the rest of this tool (see README.md) --
this is a one-line wrapper around `xdg-open`, no cross-platform
branching. `xdg-open` on a file opens it in whatever's associated with
that type; on a directory it opens the file manager there.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


class OpenError(RuntimeError):
    pass


def open_path(path: Path) -> None:
    """Fire-and-forget: hand `path` to xdg-open. Raises OpenError if
    xdg-open isn't available or the path doesn't exist -- callers
    should catch this and show it in the GUI rather than let it crash
    the click handler."""
    if not path.exists():
        raise OpenError(f"path does not exist: {path}")
    if not shutil.which("xdg-open"):
        raise OpenError(
            "xdg-open not found on PATH -- install xdg-utils "
            "(e.g. `apt install xdg-utils`) to use 'open' buttons."
        )
    try:
        subprocess.Popen(
            ["xdg-open", str(path)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except OSError as e:
        raise OpenError(f"could not start xdg-open: {e}") from e
