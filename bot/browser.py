import json
import os
import sys
import tempfile

from . import config


def _fake_video_path() -> str:
    return str(config.FAKE_VIDEO_PATH) if config.FAKE_VIDEO_PATH.exists() else ""


async def _launch_browser(receiver) -> tuple:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("❌  playwright not installed.")
        print("    Run: pip install playwright && playwright install chromium")
        sys.exit(1)

    intercept_code = config.INTERCEPT_JS.read_text()

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
        headless=config.HEADLESS_MODE,
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
