#!/usr/bin/env python3
"""Automates Microsoft Outlook account creation and saves credentials to claude_desktop_config.json"""

import asyncio
import json
import pathlib
import random
import string
import subprocess

from patchright.async_api import async_playwright

HERE = pathlib.Path(__file__).parent
MCP_CONFIG = (
    pathlib.Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
)


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


async def create_account() -> None:
    local = _random_local()
    email = f"{local}@outlook.com"
    password = _random_password()
    print(f"[setup] Creating account: {email}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        ctx = await browser.new_context()
        page = await ctx.new_page()

        # ── Page 1: Outlook marketing page ───────────────────────────────────
        await page.goto("https://www.microsoft.com/en-us/microsoft-365/outlook/log-in")
        await page.get_by_role("link", name="Sign in").first.click()

        # ── Page 2: Microsoft login — click "Create one!" ────────────────────
        await page.wait_for_selector('a#signup', timeout=20000)
        await page.click('a#signup')

        # ── Page 3: email entry ───────────────────────────────────────────────
        # Domain dropdown already shows @outlook.com; fill only the local part
        await page.wait_for_selector('input[name="email"]', timeout=20000)
        await page.fill('input[name="email"]', local)
        await page.click('button[data-testid="primaryButton"]')

        # ── Page 4: password ──────────────────────────────────────────────────
        await page.wait_for_selector('input[type="password"]', timeout=20000)
        await page.fill('input[type="password"]', password)
        await page.click('button[data-testid="primaryButton"]')

        # ── Page 5: birthday ──────────────────────────────────────────────────
        await page.wait_for_selector('#BirthMonthDropdown', timeout=20000)

        await page.click('label[for="BirthMonthDropdown"]')
        await page.wait_for_selector('[role="listbox"]', state="visible", timeout=5000)
        await page.wait_for_timeout(800)
        await page.evaluate('document.querySelector(\'[role="listbox"] [role="option"]\').click()')
        await page.wait_for_selector('[role="listbox"]', state="hidden", timeout=5000)
        await page.wait_for_timeout(400)

        await page.click('label[for="BirthDayDropdown"]')
        await page.wait_for_selector('[role="listbox"]', state="visible", timeout=5000)
        await page.wait_for_timeout(800)
        await page.evaluate('document.querySelector(\'[role="listbox"] [role="option"]\').click()')
        await page.wait_for_selector('[role="listbox"]', state="hidden", timeout=5000)
        await page.wait_for_timeout(400)

        await page.fill('input[name="BirthYear"]', "1996")
        await page.click('button[data-testid="primaryButton"]')
        await page.wait_for_load_state('networkidle', timeout=15000)

        # ── Page 6: name ──────────────────────────────────────────────────────
        await page.wait_for_selector('#firstNameInput', timeout=60000)
        await page.fill('#firstNameInput', 'Milo')
        await page.fill('#lastNameInput', 'Core')
        await page.click('button[data-testid="primaryButton"]')

        # Microsoft shows a CAPTCHA here. Wait until the page leaves signup.live.com,
        # which happens only after the CAPTCHA is solved and the account is created.
        print("[setup] -------------------------------------------------------")
        print("[setup] ACTION REQUIRED: solve the CAPTCHA in the browser window")
        print("[setup] The script will save credentials automatically once done.")
        print("[setup] -------------------------------------------------------")
        # Poll until the page leaves signup.live.com (after CAPTCHA is solved).
        # wait_for_function / eval is blocked by the page's CSP, so we poll instead.
        deadline = asyncio.get_event_loop().time() + 300
        while asyncio.get_event_loop().time() < deadline:
            if "signup.live.com" not in page.url:
                break
            await asyncio.sleep(2)
        else:
            raise TimeoutError("Timed out waiting for CAPTCHA to be solved")
        await ctx.close()
        await browser.close()

    _update_mcp_config(email, password)
    print(f"[setup] Done — credentials saved to {MCP_CONFIG}")
    print(f"[setup]   TEAMS_BOT_EMAIL={email}")
    print(f"[setup]   TEAMS_BOT_PASSWORD={password}")


if __name__ == "__main__":
    asyncio.run(create_account())
