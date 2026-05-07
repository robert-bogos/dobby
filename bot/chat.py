import asyncio
import base64
import datetime
import pathlib

from . import config


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

    # ── 4. Dismiss any modal overlays, then focus the composer ──────────────
    overlay = await page.query_selector('.ui-dialog__overlay, [data-slot-name\\:rp\\:="root"]')
    if overlay:
        print("    Dialog overlay detected — dismissing with Escape.")
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(500)

    try:
        await composer.click()
    except Exception:
        # Overlay may still be present — force the click and focus via JS.
        try:
            await composer.click(force=True)
            await composer.evaluate("el => el.focus()")
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
            await _play_audio_prompt(page, config.AUDIO_PROMPT_PATH)
    except asyncio.CancelledError:
        print("📅  Chat message cancelled (meeting ended before delay elapsed).")
        raise
    except Exception as e:
        print(f"⚠️   Chat message task crashed: {e}")
