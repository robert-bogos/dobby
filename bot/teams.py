import re
import sys
from urllib.parse import parse_qs, unquote, urlencode, urlparse, urlunparse

from . import config
from .browser import _fake_video_path, _launch_browser


async def _login(page):
    print("🔑  Logging in...")
    await page.goto("https://login.microsoftonline.com/")
    await page.wait_for_load_state("networkidle")

    await page.fill('input[type="email"]', config.BOT_EMAIL)
    await page.click('input[type="submit"]')
    await page.wait_for_timeout(2000)

    # Microsoft may show a passkey/FIDO2 prompt instead of the password field.
    for selector in [
        'text="Use your password"',
        'text="Use your password instead"',
        'text="Use a different sign-in option"',
        'text="Other ways to sign in"',
        'text="Sign in another way"',
        '[data-testid="signInAnotherWay"]',
        'a[href*="UseADifferentSignInOption"]',
        'a[href*="useADifferentSignInOption"]',
    ]:
        try:
            btn = page.locator(selector)
            if await btn.is_visible(timeout=2000):
                await btn.click()
                print("    Bypassed passkey prompt — switching to password.")
                await page.wait_for_timeout(2000)
                break
        except Exception:
            pass

    for selector in [
        'text="Password"',
        '[data-value="Password"]',
        'input[type="submit"][value="Password"]',
    ]:
        try:
            btn = page.locator(selector)
            if await btn.is_visible(timeout=2000):
                await btn.click()
                print("    Selected password method.")
                await page.wait_for_timeout(2000)
                break
        except Exception:
            pass

    await page.fill('input[type="password"]', config.BOT_PASSWORD)

    for selector in [
        'button:has-text("Next")',
        'input[type="submit"][value="Next"]',
        'input[type="submit"][value="Sign in"]',
        'input[type="submit"]',
    ]:
        try:
            btn = page.locator(selector)
            if await btn.is_visible(timeout=2000):
                await btn.click()
                break
        except Exception:
            pass

    await page.wait_for_timeout(3000)

    try:
        btn = page.locator('input[type="submit"][value="Yes"]')
        if await btn.is_visible(timeout=3000):
            await btn.click()
    except Exception:
        pass

    print("✅  Logged in.")


async def _navigate_to_meeting(page, meeting_link: str):
    print("📞  Navigating to meeting...")

    # Teams links sometimes have ?p\=PASSCODE which breaks parse_qs.
    cleaned_link = meeting_link.replace("\\=", "=").replace("%5C=", "=")
    parsed = urlparse(cleaned_link)

    # Unwrap Microsoft SafeLinks (safelinks.protection.outlook.com?url=<actual url>).
    if "safelinks.protection.outlook.com" in parsed.netloc:
        params = parse_qs(parsed.query)
        inner = params.get("url", [None])[0]
        if inner:
            cleaned_link = unquote(inner).replace("\\=", "=").replace("%5C=", "=")
            parsed = urlparse(cleaned_link)

    if "dl/launcher" in parsed.path:
        params = parse_qs(parsed.query)
        inner = params.get("url", [None])[0]
        if inner:
            cleaned_link = unquote(inner).replace("\\=", "=").replace("%5C=", "=")
            parsed = urlparse(cleaned_link)

    params = parse_qs(parsed.query)
    params["directDl"]    = ["true"]
    params["launchAgent"] = ["false"]
    web_url = urlunparse(parsed._replace(query=urlencode(params, doseq=True)))

    print(f"    Navigating to: {web_url[:80]}...")

    # CDP session lets us auto-reject native dialogs without waiting on the user.
    cdp = await page.context.new_cdp_session(page)
    await cdp.send("Page.enable")

    async def dismiss_dialog(_):
        try:
            await cdp.send("Page.handleJavaScriptDialog", {"accept": False})
        except Exception:
            pass
    cdp.on("Page.javascriptDialogOpening", dismiss_dialog)

    try:
        await page.goto(web_url, wait_until="domcontentloaded", timeout=20000)
    except Exception as e:
        print(f"    Navigation note: {e}")

    await page.wait_for_timeout(4000)

    # Direct JS click — bypasses any native dialog overlay.
    clicked = await page.evaluate("""
        () => {
            const candidates = Array.from(document.querySelectorAll('button, a'));
            const target = candidates.find(el =>
                /continue on this browser|join on the web/i.test(el.textContent || '')
            );
            if (target) {
                target.click();
                return target.textContent.trim();
            }
            return null;
        }
    """)

    if clicked:
        print(f"    Clicked button via JS: '{clicked}'")
        await page.wait_for_timeout(3000)
    else:
        print("    Could not find 'Continue on this browser' button via JS.")


async def _fill_prejoin(page):
    print("⏳  Waiting for pre-join screen to render...")
    name_input_ready = False
    for attempt in range(12):   # up to 60s
        try:
            visible = await page.evaluate("""
                () => {
                    const el = document.querySelector('input[placeholder="Type your name"]')
                        || document.querySelector('input[data-tid="prejoin-display-name-input"]')
                        || document.querySelector('input[aria-label*="name" i]');
                    return !!el;
                }
            """)
            if visible:
                name_input_ready = True
                print(f"    Pre-join screen ready after {attempt * 5}s.")
                break
        except Exception:
            pass
        await page.wait_for_timeout(5000)

    if not name_input_ready:
        try:
            await page.screenshot(path="/tmp/prejoin-failed.png", full_page=True)
            url_now = page.url
            body_preview = await page.evaluate(
                "() => (document.body.innerText || '').slice(0, 500)"
            )
            print(f"    ⚠️  Pre-join screen never appeared.")
            print(f"        URL: {url_now}")
            print(f"        Page text preview:\n{body_preview}")
            print(f"        Screenshot saved to /tmp/prejoin-failed.png")
        except Exception:
            pass

    # Fill via JS so React picks up the change.
    filled = await page.evaluate(f"""
        () => {{
            const el = document.querySelector('input[placeholder="Type your name"]')
                    || document.querySelector('input[data-tid="prejoin-display-name-input"]')
                    || document.querySelector('input[aria-label*="name" i]');
            if (!el) return false;
            const setter = Object.getOwnPropertyDescriptor(
                window.HTMLInputElement.prototype, 'value'
            ).set;
            setter.call(el, {config.BOT_DISPLAY_NAME!r});
            el.dispatchEvent(new Event('input',  {{ bubbles: true }}));
            el.dispatchEvent(new Event('change', {{ bubbles: true }}));
            return true;
        }}
    """)
    if filled:
        print(f"    Set display name to '{config.BOT_DISPLAY_NAME}'.")
    else:
        print(f"    Could not find display name input.")

    # Mic always off (bot only listens). Camera only on with a fake video feed.
    want_camera_on = bool(_fake_video_path())

    toggles = [
        ("toggle-video", "camera",     want_camera_on),
        ("toggle-mute",  "microphone", False),
    ]
    for tid, label, want_on in toggles:
        try:
            result = await page.evaluate(f"""
                () => {{
                    const el = document.querySelector('input[data-tid="{tid}"]');
                    if (!el) return 'not-found';
                    const currentlyOn = el.checked;
                    const wantOn = {str(want_on).lower()};
                    if (currentlyOn !== wantOn) {{
                        el.click();
                        return wantOn ? 'turned-on' : 'turned-off';
                    }}
                    return wantOn ? 'already-on' : 'already-off';
                }}
            """)
            print(f"    {label.capitalize()}: {result}")
        except Exception as e:
            print(f"    Could not toggle {label}: {e}")

    await page.wait_for_timeout(1000)


async def _click_join(page):
    clicked = await page.evaluate("""
        () => {
            const btn = document.querySelector('#prejoin-join-button')
                     || document.querySelector('[data-tid="prejoin-join-button"]')
                     || Array.from(document.querySelectorAll('button')).find(
                            b => /join now/i.test(b.textContent || '')
                        );
            if (btn) {
                btn.click();
                return true;
            }
            return false;
        }
    """)

    if clicked:
        print("✅  Clicked 'Join now' — entering meeting.")
        await page.wait_for_timeout(5000)
    else:
        print("❌  Could not find Join now button.")


async def _handle_passcode_retry(page, meeting_link: str, passcode_override: str):
    """No-op if the meeting joined directly; else fills passcode and clicks Rejoin."""
    try:
        passcode_input = page.locator('[data-tid="meeting-passcode-input"]')
        if not await passcode_input.is_visible(timeout=3000):
            return
        print("    Detected passcode retry screen.")

        # Resolution order: CLI/env override → URL regex fallback.
        passcode = passcode_override or config.MEETING_PASSCODE or ""

        if not passcode:
            for key in ("p", "passcode", "password"):
                m = re.search(rf"[?&]{key}\\?=([^&]+)", meeting_link)
                if m:
                    passcode = unquote(m.group(1))
                    print(f"    Extracted passcode from URL parameter '{key}'.")
                    break
        else:
            print(f"    Using passcode from env var / CLI.")

        if not passcode:
            print("⚠️   No passcode found in meeting URL (?p=...)")
            return

        await page.evaluate(f"""
            () => {{
                const el = document.querySelector('[data-tid="meeting-passcode-input"]');
                if (el) {{
                    const setter = Object.getOwnPropertyDescriptor(
                        window.HTMLInputElement.prototype, 'value'
                    ).set;
                    setter.call(el, '{passcode}');
                    el.dispatchEvent(new Event('input',  {{ bubbles: true }}));
                    el.dispatchEvent(new Event('change', {{ bubbles: true }}));
                }}
            }}
        """)
        print(f"    Filled passcode field.")
        await page.wait_for_timeout(1000)

        rejoined = await page.evaluate("""
            () => {
                const btn = document.querySelector('[data-tid="calling-retry-rejoinbutton"]');
                if (btn && !btn.disabled) {
                    btn.click();
                    return true;
                }
                return false;
            }
        """)
        if rejoined:
            print("✅  Clicked 'Rejoin call'.")
            await page.wait_for_timeout(5000)
        else:
            print("❌  Rejoin button still disabled — passcode may not have registered.")
    except Exception:
        pass


async def join_teams_meeting(meeting_link: str, passcode_override: str = "", receiver=None) -> tuple:
    if not config.BOT_EMAIL or not config.BOT_PASSWORD:
        print("❌  Bot credentials not set.")
        print("    export TEAMS_BOT_EMAIL=... TEAMS_BOT_PASSWORD=...")
        sys.exit(1)

    if not config.INTERCEPT_JS.exists():
        print(f"❌  intercept.js not found at {config.INTERCEPT_JS}")
        sys.exit(1)

    page, context, playwright = await _launch_browser(receiver)
    await _login(page)
    await _navigate_to_meeting(page, meeting_link)
    await _fill_prejoin(page)
    await _click_join(page)
    await _handle_passcode_retry(page, meeting_link, passcode_override)

    return page, context, playwright


async def leave_meeting(page, context, playwright):
    print("\n📴  Leaving meeting...")

    # Direct JS click — bypasses Playwright strict-click on aria-hidden containers.
    clicked = False
    try:
        clicked = await page.evaluate("""
            () => {
                const candidates = [
                    document.querySelector('[data-tid="hangup-main-btn"]'),
                    document.querySelector('button[aria-label*="Leave" i]'),
                    document.querySelector('button[aria-label*="hang up" i]'),
                    ...Array.from(document.querySelectorAll('button')).filter(
                        b => /^leave$/i.test((b.textContent || '').trim())
                    ),
                ].filter(Boolean);

                if (candidates.length === 0) return false;
                candidates[0].click();
                return true;
            }
        """)
    except Exception as e:
        print(f"    Could not click Leave button: {e}")

    if clicked:
        print("    Clicked 'Leave' button — waiting for Teams to propagate...")
        # 4s margin so other participants see the disconnect before teardown.
        await page.wait_for_timeout(4000)
    else:
        print("    Leave button not found — closing browser directly.")

    try:
        await context.close()
    except Exception:
        pass
    try:
        await playwright.stop()
    except Exception:
        pass
    print("✅  Browser closed.")
