#!/usr/bin/env python3
"""Saves bot credentials to claude_desktop_config.json"""

import json
import pathlib
import random
import string
import subprocess

HERE = pathlib.Path(__file__).parent
MCP_CONFIG = (
    pathlib.Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
)

SIGNUP_URL = "https://www.microsoft.com/en-us/microsoft-365/outlook/log-in"


def _random_local() -> str:
    prefix = "".join(random.choices(string.ascii_lowercase, k=5))
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=5))
    return f"{prefix}{suffix}"


def _random_password() -> str:
    parts = (
        random.choices(string.ascii_uppercase, k=2)
        + random.choices(string.ascii_lowercase, k=4)
        + random.choices(string.digits, k=2)
        + random.choices("!@#$%^&*", k=2)
    )
    random.shuffle(parts)
    return "".join(parts)


def _update_mcp_config(email: str, password: str) -> None:
    try:
        full_name = subprocess.check_output(["id", "-F"], text=True).strip()
        first_name = full_name.split()[0] if full_name else ""
    except Exception:
        first_name = ""
    display_name = f"{first_name}'s AI notetaker" if first_name else "AI notetaker"

    cfg = json.loads(MCP_CONFIG.read_text()) if MCP_CONFIG.exists() else {}
    cfg.setdefault("mcpServers", {})["dobby"] = {
        "command": str(HERE / ".venv" / "bin" / "python"),
        "args": [str(HERE / "mcp_server.py")],
        "env": {
            "TEAMS_BOT_EMAIL": email,
            "TEAMS_BOT_PASSWORD": password,
            "TEAMS_BOT_DISPLAY_NAME": display_name,
            "FAKE_VIDEO_PATH": str(HERE / "utils" / "bot-feed.y4m"),
        },
    }
    MCP_CONFIG.write_text(json.dumps(cfg, indent=2) + "\n")
    print(f"[setup] MCP config updated: {MCP_CONFIG}")


def main() -> None:
    suggested_email = f"{_random_local()}@outlook.com"
    suggested_password = _random_password()

    print()
    print("=" * 56)
    print("  Bot account setup")
    print("=" * 56)
    print()
    print("  Dobby needs a dedicated Microsoft Outlook account.")
    print("  Please create a free one at outlook.com.")
    print()
    print("  Suggested credentials (feel free to use these):")
    print(f"    Email:    {suggested_email}")
    print(f"    Password: {suggested_password}")
    print()
    print("  Opening an incognito Chrome window to get you started...")
    print()

    try:
        subprocess.Popen([
            "open", "-a", "Google Chrome",
            "--args", "--incognito", SIGNUP_URL,
        ])
    except Exception as e:
        print(f"  (Could not open Chrome automatically: {e})")
        print(f"  Please open this URL in an incognito window: {SIGNUP_URL}")

    print("  Once the account is created, come back here and enter")
    print("  the credentials below.")
    print()

    email = input("  Email you used: ").strip()
    password = input("  Password you used: ").strip()

    if not email or not password:
        print("[setup] No credentials entered — aborting.")
        return

    _update_mcp_config(email, password)
    print()
    print(f"[setup] Done — credentials saved.")
    print(f"[setup]   TEAMS_BOT_EMAIL={email}")
    print(f"[setup]   TEAMS_BOT_PASSWORD={password}")


if __name__ == "__main__":
    main()
