"""Entry Point: `uv run diag-tool` bzw. `python -m diag_tool`."""

from __future__ import annotations

import sys

from diag_tool.app import run


def main() -> int:
    return run(sys.argv)


if __name__ == "__main__":
    raise SystemExit(main())
