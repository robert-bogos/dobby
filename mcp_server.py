#!/usr/bin/env python3
import os
import pathlib
import subprocess
import sys
import threading

HERE = pathlib.Path(__file__).parent
sys.path.insert(0, str(HERE))

RELEASE_ASSETS = {
    HERE / "utils" / "bot-feed.y4m": (
        "https://github.com/robert-bogos/dobby/releases/latest/download/bot-feed.y4m"
    ),
}


def _auto_update():
    try:
        result = subprocess.run(
            ["git", "pull", "--ff-only", "--quiet"],
            cwd=HERE,
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode == 0 and result.stdout.strip():
            # New code was pulled — restart so mcp_server.py itself reloads if it changed.
            os.execv(sys.executable, [sys.executable] + sys.argv)
    except Exception:
        pass  # Never block startup due to a failed update.


def _ensure_assets():
    """Download missing release assets in a background thread — never blocks startup."""
    def _download():
        for path, url in RELEASE_ASSETS.items():
            if path.exists():
                continue
            print(f"[dobby] Downloading {path.name} in background...", file=sys.stderr, flush=True)
            try:
                path.parent.mkdir(exist_ok=True)
                # curl uses the system certificate store, avoiding SSL issues with urllib on macOS.
                result = subprocess.run(
                    ["curl", "-L", "--silent", "--show-error", "-o", str(path), url],
                    capture_output=True, text=True, timeout=300,
                )
                if result.returncode == 0:
                    print(f"[dobby] {path.name} ready.", file=sys.stderr, flush=True)
                else:
                    print(f"[dobby] Warning: curl failed for {path.name}: {result.stderr}", file=sys.stderr, flush=True)
                    path.unlink(missing_ok=True)  # remove partial file
            except Exception as e:
                print(f"[dobby] Warning: could not download {path.name}: {e}", file=sys.stderr, flush=True)

    threading.Thread(target=_download, daemon=True).start()


_auto_update()
_ensure_assets()

import asyncio
from server.main import main

if __name__ == "__main__":
    asyncio.run(main())
