#!/usr/bin/env python3
"""Automates Microsoft Outlook account creation and saves credentials to .env"""

import asyncio
import pathlib
import random
import re
import string

from patchright.async_api import async_playwright

HERE = pathlib.Path(__file__).parent
ENV_FILE = HERE / ".env"


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


def _save_env(email: str, password: str) -> None:
    lines = ENV_FILE.read_text().splitlines() if ENV_FILE.exists() else []
    replacements = {"TEAMS_BOT_EMAIL": email, "TEAMS_BOT_PASSWORD": password}
    updated: set[str] = set()
    new_lines = []
    for line in lines:
        m = re.match(r"^(TEAMS_BOT_EMAIL|TEAMS_BOT_PASSWORD)\s*=", line)
        if m:
            key = m.group(1)
            new_lines.append(f"{key}={replacements[key]}")
            updated.add(key)
        else:
            new_lines.append(line)
    for key, val in replacements.items():
        if key not in updated:
            new_lines.append(f"{key}={val}")
    ENV_FILE.write_text("\n".join(new_lines) + "\n")


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
        await page.wait_for_selector('[role="listbox"]', state="attached", timeout=5000)
        await page.wait_for_timeout(800)
        await page.locator('[role="option"]').first.click(force=True)

        await page.click('label[for="BirthDayDropdown"]')
        await page.wait_for_selector('[role="listbox"]', state="attached", timeout=5000)
        await page.wait_for_timeout(800)
        await page.locator('[role="option"]').first.click(force=True)

        await page.fill('input[name="BirthYear"]', "1996")
        await page.click('button[data-testid="primaryButton"]')

        # ── Page 6: name ──────────────────────────────────────────────────────
        await page.wait_for_selector('#firstNameInput', timeout=20000)
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

    _save_env(email, password)
    print(f"[setup] Done — credentials saved to {ENV_FILE}")
    print(f"[setup]   TEAMS_BOT_EMAIL={email}")
    print(f"[setup]   TEAMS_BOT_PASSWORD={password}")


if __name__ == "__main__":
    asyncio.run(create_account())
