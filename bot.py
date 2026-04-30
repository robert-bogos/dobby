#!/usr/bin/env python3
"""
Teams meeting notetaker — Playwright joins, captures WebRTC audio via
intercept.js, transcribes with faster-whisper, summarises with Ollama.

Usage:
    python bot.py --link "https://teams.microsoft.com/l/meetup-join/..."
"""

import argparse
import asyncio
import base64
import datetime
import json
import os
import pathlib
import re
import signal
import sys
import tempfile
from urllib.parse import parse_qs, unquote, urlencode, urlparse, urlunparse

import numpy as np
import scipy.io.wavfile as wav


# ── Config ────────────────────────────────────────────────────────────────────

SAMPLE_RATE   = 16000
CHANNELS      = 1
CHUNK_SIZE    = 4096
WHISPER_MODEL = "medium"
OLLAMA_MODEL  = "llama3"
OUTPUT_DIR    = str(pathlib.Path(__file__).parent / "meeting-notes")

BOT_EMAIL         = os.environ.get("TEAMS_BOT_EMAIL", "")
BOT_PASSWORD      = os.environ.get("TEAMS_BOT_PASSWORD", "")
BOT_DISPLAY_NAME  = os.environ.get("TEAMS_BOT_DISPLAY_NAME", "AI notetaker")
MEETING_PASSCODE  = os.environ.get("TEAMS_MEETING_PASSCODE", "")

INTERCEPT_JS      = pathlib.Path(__file__).parent / "intercept.js"
AUDIO_PROMPT_PATH = pathlib.Path(__file__).parent / "question-in-chat.mp3"
FAKE_VIDEO_PATH   = pathlib.Path(__file__).parent / "bot-feed.y4m"

# Stealth patches below compensate — Microsoft sometimes blocks headless Chromium.
HEADLESS_MODE = True

SILENCE_LIMIT = 48      # 48 * 5s = 4 minutes of silence before auto-leave
RMS_THRESHOLD = 0.005   # speech ~0.02+, room hiss ~0.001–0.005


# ── Prompts ───────────────────────────────────────────────────────────────────

SUMMARIZE_PROMPT = """You are a professional meeting notes assistant.

Given the following meeting transcript, produce concise plain-text meeting notes with these sections:

MEETING SUMMARY
A short 2-4 sentence overview of what was discussed.

KEY DECISIONS
Bullet list of decisions made during the meeting.

ACTION ITEMS
Bullet list of action items, with the responsible person if mentioned and a due date if mentioned.

IMPORTANT POINTS
Any other important points, risks, or topics raised.

Keep the notes factual and concise. If a section has nothing relevant, write "None noted."

TRANSCRIPT:
{transcript}
"""


# ── Audio capture ─────────────────────────────────────────────────────────────

class AudioReceiver:
    """PCM chunks from the browser via CDP binding — bypasses Teams' CSP."""

    def __init__(self):
        self.chunks: list[np.ndarray] = []
        self._connected = asyncio.Event()

    async def register(self, context):
        """Exposes __notetakerSendAudio on every page. Call once per context."""

        async def on_audio(b64_chunk: str):
            if not self._connected.is_set():
                self._connected.set()
                print("🔗  Browser audio stream connected (via CDP binding)")
            try:
                raw = base64.b64decode(b64_chunk)
                chunk = np.frombuffer(raw, dtype="<f4")
                self.chunks.append(chunk.copy())
                if len(self.chunks) % 100 == 0:
                    secs = len(self.chunks) * CHUNK_SIZE / SAMPLE_RATE
                    print(f"📊  Received {len(self.chunks)} chunks (~{secs:.1f}s audio)")
            except Exception as e:
                print(f"⚠️   Error decoding audio chunk: {e}")

        await context.expose_function("__notetakerSendAudio", on_audio)
        print("🎙  Audio CDP binding ready (window.__notetakerSendAudio)")

    async def wait_for_connection(self, timeout: float = 30.0):
        try:
            await asyncio.wait_for(self._connected.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            print("⚠️   No audio received after 30s — check browser console for [notetaker] logs.")

    def stop(self) -> np.ndarray:
        if not self.chunks:
            return np.array([], dtype=np.float32)
        audio = np.concatenate(self.chunks)
        print(f"⏹   Audio capture stopped — {len(audio) / SAMPLE_RATE:.1f}s received")
        return audio


# ── Browser setup ─────────────────────────────────────────────────────────────

def _fake_video_path() -> str:
    return str(FAKE_VIDEO_PATH) if FAKE_VIDEO_PATH.exists() else ""


async def _launch_browser(receiver) -> tuple:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("❌  playwright not installed.")
        print("    Run: pip install playwright && playwright install chromium")
        sys.exit(1)

    intercept_code = INTERCEPT_JS.read_text()

    print("\n🌐  Launching browser...")
    playwright = await async_playwright().start()

    # Reject msteams:// at prefs level — suppresses "Open in Teams?" dialog.
    user_data_dir = tempfile.mkdtemp(prefix="ai-notetaker-")
    prefs = {"protocol_handler": {"excluded_schemes": {"msteams": True}}}
    default_dir = os.path.join(user_data_dir, "Default")
    os.makedirs(default_dir, exist_ok=True)
    with open(os.path.join(default_dir, "Preferences"), "w") as f:
        json.dump(prefs, f)

    fake_video = _fake_video_path()
    fake_video_args = []
    if fake_video:
        fake_video_args = [
            "--use-fake-device-for-media-stream",
            f"--use-file-for-fake-video-capture={fake_video}",
        ]
        print(f"🎥  Using fake video feed: {fake_video}")

    context = await playwright.chromium.launch_persistent_context(
        user_data_dir,
        headless=HEADLESS_MODE,
        args=[
            "--mute-audio",
            "--use-fake-ui-for-media-stream",
            "--autoplay-policy=no-user-gesture-required",
            "--disable-web-security",
            "--disable-external-intent-requests",
            "--no-default-browser-check",
            "--disable-blink-features=AutomationControlled",
            "--disable-features=IsolateOrigins,site-per-process",
            "--window-size=1280,800",
            *fake_video_args,
        ],
        permissions=["microphone", "camera"],
        viewport={"width": 1280, "height": 800},
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
    )

    await context.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        Object.defineProperty(navigator, 'plugins',   { get: () => [1, 2, 3, 4, 5] });
        Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
        window.chrome = { runtime: {} };
        const origQuery = window.navigator.permissions.query;
        window.navigator.permissions.query = (p) =>
            p.name === 'notifications'
                ? Promise.resolve({ state: Notification.permission })
                : origQuery(p);
    """)

    # Must register before intercept.js — it needs window.__notetakerSendAudio.
    if receiver is not None:
        await receiver.register(context)
    await context.add_init_script(script=intercept_code)

    # Belt-and-suspenders block, redundant with the prefs-level rejection above.
    await context.route("msteams://**", lambda route: route.abort())

    page = await context.new_page()
    page.on(
        "console",
        lambda msg: print(f"  [browser] {msg.text}") if "[notetaker]" in msg.text else None,
    )

    return page, context, playwright


# ── Teams flow ────────────────────────────────────────────────────────────────

async def _login(page):
    print("🔑  Logging in...")
    await page.goto("https://login.microsoftonline.com/")
    await page.wait_for_load_state("networkidle")

    await page.fill('input[type="email"]', BOT_EMAIL)
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

    await page.fill('input[type="password"]', BOT_PASSWORD)

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
            setter.call(el, {BOT_DISPLAY_NAME!r});
            el.dispatchEvent(new Event('input',  {{ bubbles: true }}));
            el.dispatchEvent(new Event('change', {{ bubbles: true }}));
            return true;
        }}
    """)
    if filled:
        print(f"    Set display name to '{BOT_DISPLAY_NAME}'.")
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
        passcode = passcode_override or MEETING_PASSCODE or ""

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
    if not BOT_EMAIL or not BOT_PASSWORD:
        print("❌  Bot credentials not set.")
        print("    export TEAMS_BOT_EMAIL=... TEAMS_BOT_PASSWORD=...")
        sys.exit(1)

    if not INTERCEPT_JS.exists():
        print(f"❌  intercept.js not found at {INTERCEPT_JS}")
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


# ── Chat message posting ─────────────────────────────────────────────────────

async def _message_appears_in_chat(page, message: str) -> bool:
    """True iff the message text appears inside the chat pane or anywhere in the DOM."""
    return await page.evaluate(
        """
        (msg) => {
            const candidates = [
                document.querySelector('[data-tid="chat-pane-list"]'),
                document.querySelector('[data-tid*="chat-pane" i]'),
                document.querySelector('[aria-label*="Meeting chat" i]'),
                document.querySelector('[aria-label*="Chat pane" i]'),
                document.body,
            ].filter(Boolean);
            for (const el of candidates) {
                if ((el.innerText || '').includes(msg)) return true;
            }
            return false;
        }
        """,
        message,
    )


async def _screenshot(page, label: str):
    """Best-effort debug screenshot under /tmp; failures are non-fatal."""
    ts = datetime.datetime.now().strftime("%H-%M-%S")
    path = f"/tmp/chat-debug-{label}-{ts}.png"
    try:
        await page.screenshot(path=path, full_page=True)
        print(f"    📸 Saved: {path}")
    except Exception as e:
        print(f"    (screenshot failed: {e})")


async def _send_chat_message(page, message: str) -> bool:
    """
    Open the in-meeting chat pane, type the message, send, verify.

    Previous versions returned True the moment *any* selector matched, including
    fuzzy `data-tid*="send"` fallbacks that ended up clicking unrelated buttons.
    This version verifies the message actually appears in the chat DOM before
    reporting success, and logs every selector that matched for postmortem.
    """
    print(f"\n💬  Posting chat message: {message!r}")

    # ── 1. Click the chat-pane toggle ──────────────────────────────────────
    toggle = await page.evaluate("""
        () => {
            const tries = [
                ['tid:chat-button',        document.querySelector('button[data-tid="chat-button"]')],
                ['tid:call-chat-button',   document.querySelector('button[data-tid="call-chat-button"]')],
                ['id:chat-button',         document.querySelector('#chat-button')],
                ['aria:Show conversation', document.querySelector('button[aria-label*="Show conversation" i]')],
                ['aria:Open chat',         document.querySelector('button[aria-label*="Open chat" i]')],
                ['aria:Meeting chat',      document.querySelector('button[aria-label*="Meeting chat" i]')],
                ['aria:Conversation',      document.querySelector('button[aria-label="Conversation"]')],
                ['aria:Chat',              document.querySelector('button[aria-label="Chat"]')],
            ];
            for (const [name, el] of tries) {
                if (el) {
                    el.click();
                    return {
                        matched: name,
                        aria: el.getAttribute('aria-label') || '',
                        tid:  el.getAttribute('data-tid')   || '',
                        id:   el.id                         || '',
                    };
                }
            }
            return null;
        }
    """)
    if not toggle:
        print("    ⚠️  No chat-pane toggle found.")
        await _screenshot(page, "no-chat-toggle")
        return False
    print(f"    Clicked chat toggle: {toggle}")
    await page.wait_for_timeout(2500)

    # ── 2. Verify the chat pane is present ─────────────────────────────────
    pane_info = await page.evaluate("""
        () => {
            const cs = [
                document.querySelector('[data-tid="chat-pane-list"]'),
                document.querySelector('[data-tid*="chat-pane" i]'),
                document.querySelector('[aria-label*="Meeting chat" i]'),
                document.querySelector('[aria-label*="Chat pane" i]'),
            ].filter(Boolean);
            return cs.length
                ? { count: cs.length, aria: cs[0].getAttribute('aria-label') || '', tid: cs[0].getAttribute('data-tid') || '' }
                : null;
        }
    """)
    print(f"    Chat pane detected: {pane_info}")

    # ── 3. Locate the composer (may be inside an iframe) ───────────────────
    composer = None
    for frame in page.frames:
        for sel in (
            'div[data-tid="ckeditor"] div[contenteditable="true"]',
            'div[contenteditable="true"][aria-label*="message" i]',
            'div[contenteditable="true"][aria-label*="Type" i]',
            'div[role="textbox"][contenteditable="true"]',
        ):
            try:
                loc = frame.locator(sel).first
                if await loc.count():
                    composer = loc
                    info = await loc.evaluate(
                        "el => ({ aria: el.getAttribute('aria-label') || '', tid: el.getAttribute('data-tid') || '' })"
                    )
                    print(f"    Composer matched: frame={frame.url[:70]!r} sel={sel!r} info={info}")
                    break
            except Exception:
                continue
        if composer:
            break

    if not composer:
        print("    ⚠️  No composer element found in any frame.")
        await _screenshot(page, "no-composer")
        return False

    # ── 4. Focus and type via the keyboard (more reliable than execCommand) ─
    try:
        await composer.click()
    except Exception as e:
        print(f"    Could not focus composer: {e}")
        return False
    await page.wait_for_timeout(300)

    await page.keyboard.type(message, delay=20)
    await page.wait_for_timeout(500)

    # Teams sends on Enter by default (Shift+Enter for newline).
    await page.keyboard.press("Enter")
    await page.wait_for_timeout(2000)

    if await _message_appears_in_chat(page, message):
        print("✅  Chat message posted and verified in DOM.")
        return True

    # ── 5. Fallback: explicit send button, with strict selectors only ──────
    print("    Enter did not post the message — trying explicit Send button.")
    clicked = await page.evaluate("""
        () => {
            const tries = [
                ['tid:newMessageCommands-sendButton', document.querySelector('button[data-tid="newMessageCommands-sendButton"]')],
                ['tid:send-message-button',           document.querySelector('button[data-tid="send-message-button"]')],
                ['tid:sendMessageButton',             document.querySelector('button[data-tid="sendMessageButton"]')],
                ['aria:Send message',                 document.querySelector('button[aria-label="Send message"]:not([disabled])')],
                ['aria:Send',                         document.querySelector('button[aria-label="Send"]:not([disabled])')],
            ];
            for (const [name, el] of tries) {
                if (el && !el.disabled) {
                    el.click();
                    return {
                        matched: name,
                        aria: el.getAttribute('aria-label') || '',
                        tid:  el.getAttribute('data-tid')   || '',
                    };
                }
            }
            return null;
        }
    """)
    if not clicked:
        print("    ⚠️  No Send button matched.")
        await _screenshot(page, "no-send-button")
        return False
    print(f"    Clicked Send: {clicked}")
    await page.wait_for_timeout(2000)

    if await _message_appears_in_chat(page, message):
        print("✅  Chat message posted and verified in DOM.")
        return True

    print("    ⚠️  Message typed but does not appear in chat DOM — likely failed to send.")
    await _screenshot(page, "post-send-missing")
    return False


async def _toggle_meeting_mic(page, unmute: bool) -> bool:
    """Click the in-meeting mic button. Returns True if a match was clicked."""
    want = "Unmute" if unmute else "Mute"
    result = await page.evaluate(
        """
        (want) => {
            // Teams flips the aria-label between 'Unmute' and 'Mute' based on current state.
            // Match at start-of-label so 'Unmute all' etc. don't sneak in.
            const rx = new RegExp('^' + want + '(?:$|\\\\s|,|\\\\()', 'i');
            for (const btn of document.querySelectorAll('button')) {
                const aria = btn.getAttribute('aria-label') || '';
                if (rx.test(aria) && !btn.disabled) {
                    btn.click();
                    return { ok: true, aria: aria.slice(0, 60), tid: btn.getAttribute('data-tid') || '' };
                }
            }
            return { ok: false };
        }
        """,
        want,
    )
    print(f"    Mic toggle ({want}): {result}")
    return bool(result and result.get("ok"))


async def _play_audio_prompt(page, mp3_path: pathlib.Path):
    """Unmute → play MP3 through outbound WebRTC audio → re-mute. Best-effort."""
    if not mp3_path.exists():
        print(f"\n🔊  Skipping audio prompt — {mp3_path} not found.")
        return

    print(f"\n🔊  Playing audio prompt: {mp3_path.name}")

    try:
        raw = mp3_path.read_bytes()
    except Exception as e:
        print(f"    ⚠️  Could not read {mp3_path}: {e}")
        return
    data_url = "data:audio/mpeg;base64," + base64.b64encode(raw).decode("ascii")

    unmuted = await _toggle_meeting_mic(page, unmute=True)
    if not unmuted:
        print("    ⚠️  Could not find Unmute button — skipping playback.")
        return

    # Let Teams register the unmute before we swap the outbound track.
    await page.wait_for_timeout(500)

    try:
        result = await page.evaluate(
            "(url) => window.__notetakerPlayAudio"
            " ? window.__notetakerPlayAudio(url)"
            " : { ok: false, reason: 'function-not-exposed' }",
            data_url,
        )
        print(f"    Playback result: {result}")
    except Exception as e:
        print(f"    ⚠️  Playback raised: {e}")
    finally:
        # Always attempt to mute again, even if playback errored.
        await _toggle_meeting_mic(page, unmute=False)

    print("🔊  Audio prompt done — mic re-muted.")


async def schedule_chat_message(page, message: str, delay: int):
    try:
        print(f"📅  Chat message scheduled to post in {delay}s.")
        await asyncio.sleep(delay)
        sent = await _send_chat_message(page, message)
        if sent:
            await _play_audio_prompt(page, AUDIO_PROMPT_PATH)
    except asyncio.CancelledError:
        print("📅  Chat message cancelled (meeting ended before delay elapsed).")
        raise
    except Exception as e:
        print(f"⚠️   Chat message task crashed: {e}")


# ── Meeting-end detection ────────────────────────────────────────────────────

async def watch_for_meeting_end(receiver: AudioReceiver, stop_event: asyncio.Event):
    """Set stop_event after SILENCE_LIMIT consecutive silent polls (45s warmup first)."""
    await asyncio.sleep(45)

    consecutive_silent = 0
    last_chunk_count = len(receiver.chunks)
    print(f"👀  Silence watcher armed. Baseline chunk count: {last_chunk_count}")

    while not stop_event.is_set():
        current_count = len(receiver.chunks)

        if current_count == last_chunk_count:
            consecutive_silent += 1
        else:
            new_chunks = receiver.chunks[last_chunk_count:current_count]
            combined = np.concatenate(new_chunks) if new_chunks else np.array([])
            rms = float(np.sqrt(np.mean(combined ** 2))) if len(combined) else 0.0

            if rms < RMS_THRESHOLD:
                consecutive_silent += 1
                print(f"    🔇 Silent poll #{consecutive_silent} (rms={rms:.5f} < {RMS_THRESHOLD})")
            else:
                if consecutive_silent > 0:
                    print(f"    🔊 Speech detected (rms={rms:.5f}) — resetting counter")
                consecutive_silent = 0

        last_chunk_count = current_count

        if consecutive_silent >= SILENCE_LIMIT:
            elapsed = SILENCE_LIMIT * 5
            print(f"\n🏁  Meeting end detected: {elapsed}s of silence (no audible speech)")
            stop_event.set()
            return

        await asyncio.sleep(5)


# ── Transcription ─────────────────────────────────────────────────────────────

def transcribe(audio: np.ndarray) -> str:
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        print("❌  faster-whisper not installed. Run: pip install faster-whisper")
        sys.exit(1)

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp_path = tmp.name

    audio_int16 = (audio * 32767).astype(np.int16)
    wav.write(tmp_path, SAMPLE_RATE, audio_int16)

    print(f"\n🔄  Transcribing with Whisper ({WHISPER_MODEL})...")
    model = WhisperModel(WHISPER_MODEL, device="auto", compute_type="int8")
    segments, info = model.transcribe(tmp_path, beam_size=5)

    print(f"    Detected language: {info.language}")
    lines = [f"[{s.start:6.1f}s] {s.text.strip()}" for s in segments]
    os.unlink(tmp_path)

    print(f"✅  Transcription complete ({len(lines)} segments).")
    return "\n".join(lines)


# ── Summarisation ─────────────────────────────────────────────────────────────

def summarize(transcript: str) -> str:
    try:
        import ollama
    except ImportError:
        print("❌  ollama not installed. Run: pip install ollama")
        sys.exit(1)

    print(f"\n🤖  Summarising with Ollama ({OLLAMA_MODEL})...")
    try:
        response = ollama.chat(
            model=OLLAMA_MODEL,
            messages=[{"role": "user", "content": SUMMARIZE_PROMPT.format(transcript=transcript)}],
        )
        notes = response["message"]["content"]
    except Exception as e:
        print(f"❌  Ollama error: {e}")
        print("    Is Ollama running? Start it with: ollama serve")
        sys.exit(1)

    print("✅  Summary complete.")
    return notes


# ── Save ──────────────────────────────────────────────────────────────────────

def save_notes(transcript: str, notes: str, started_at: datetime.datetime) -> tuple[str, str]:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    ended_at = datetime.datetime.now()

    # Filename uses the START time so "the 7pm meeting" maps unambiguously.
    ts_start = started_at.strftime("%Y-%m-%d_%H-%M")
    base     = ts_start

    header = (
        f"Meeting Notes\n"
        f"Started: {started_at.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"Ended:   {ended_at.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"Duration: {int((ended_at - started_at).total_seconds() // 60)} minutes\n"
        f"{'=' * 60}\n\n"
    )

    notes_path = os.path.join(OUTPUT_DIR, f"{base}_notes.txt")
    with open(notes_path, "w") as f:
        f.write(header + notes + "\n")

    transcript_header = header.replace("Meeting Notes", "Full Transcript")
    transcript_path = os.path.join(OUTPUT_DIR, f"{base}_transcript.txt")
    with open(transcript_path, "w") as f:
        f.write(transcript_header + transcript + "\n")

    return notes_path, transcript_path


# ── Main ──────────────────────────────────────────────────────────────────────

async def run(args):
    global WHISPER_MODEL, OLLAMA_MODEL
    WHISPER_MODEL = args.whisper_model
    OLLAMA_MODEL  = args.ollama_model

    receiver = AudioReceiver()
    page, context, playwright = await join_teams_meeting(args.link, args.passcode, receiver)

    meeting_started_at = datetime.datetime.now()
    print(f"🕐  Meeting joined at {meeting_started_at.strftime('%Y-%m-%d %H:%M:%S')}")

    print("\n⏳  Waiting for audio stream...")
    await receiver.wait_for_connection(timeout=30.0)

    # Stop on: Ctrl+C, --duration timeout, or silence-watcher detection.
    stop_event = asyncio.Event()
    asyncio.get_event_loop().add_signal_handler(signal.SIGINT, stop_event.set)
    watcher_task = asyncio.create_task(watch_for_meeting_end(receiver, stop_event))

    chat_task = None
    if args.chat_message:
        chat_task = asyncio.create_task(
            schedule_chat_message(page, args.chat_message, args.chat_delay)
        )

    if args.duration:
        print(f"⏳  Recording for up to {args.duration}s — Ctrl+C or meeting end to stop early.\n")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=args.duration)
        except asyncio.TimeoutError:
            pass
    else:
        print("⏳  Recording — will auto-stop when the meeting ends, or press Ctrl+C.\n")
        await stop_event.wait()

    watcher_task.cancel()

    # Cancel before leave_meeting so the task can't race against context close.
    if chat_task and not chat_task.done():
        chat_task.cancel()
        try:
            await chat_task
        except asyncio.CancelledError:
            pass

    audio = receiver.stop()
    await leave_meeting(page, context, playwright)

    if len(audio) == 0:
        print("⚠️   No audio captured — check browser console for [notetaker] messages.")
        sys.exit(1)

    transcript = transcribe(audio)
    notes      = summarize(transcript)
    notes_path, transcript_path = save_notes(transcript, notes, meeting_started_at)

    print(f"\n📝  Notes:      {notes_path}")
    print(f"📜  Transcript: {transcript_path}")
    print(f"\n{'=' * 60}\n{notes}\n{'=' * 60}")


def main():
    parser = argparse.ArgumentParser(
        description="Teams Bot Notetaker — WebRTC audio, fully local"
    )
    parser.add_argument("--link",          required=True,         help="Teams meeting URL")
    parser.add_argument("--passcode",      default="",            help="Meeting passcode (overrides URL extraction)")
    parser.add_argument("--duration",      type=int,              help="Max recording duration in seconds")
    parser.add_argument("--whisper-model", default=WHISPER_MODEL, help=f"Whisper model (default: {WHISPER_MODEL})")
    parser.add_argument("--ollama-model",  default=OLLAMA_MODEL,  help=f"Ollama model (default: {OLLAMA_MODEL})")
    parser.add_argument("--chat-message",  default="",            help="Post this message in the meeting chat 3 minutes after joining")
    parser.add_argument("--chat-delay",    type=int, default=20, help="Delay in seconds before posting --chat-message (default: 180)")
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
