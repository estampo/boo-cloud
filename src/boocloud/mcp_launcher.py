"""Console-script launcher for boocloud-mcp.

Kept separate from ``mcp_server`` so users without the ``[mcp]`` extra get a
friendly install hint instead of a bare ``ModuleNotFoundError`` traceback.
"""

from __future__ import annotations

import sys


def main() -> None:
    try:
        from boocloud.mcp_server import main as _run
    except ImportError as exc:
        sys.stderr.write(
            f"boocloud-mcp requires the 'mcp' extra (missing: {exc.name or 'mcp/bambox'}).\n"
            "Install with:  pip install 'boo-cloud[mcp]'\n"
        )
        sys.exit(1)
    _run()
