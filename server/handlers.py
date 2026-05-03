import asyncio
import datetime
import os
import pathlib
import subprocess
import sys

from mcp.types import TextContent

from .state import state

HERE      = pathlib.Path(__file__).parent
BOT_SCRIPT = HERE.parent / "bot.py"
NOTES_DIR  = HERE.parent / "meeting-notes"
LOG_DIR    = HERE.parent / "bot-logs"
LOG_DIR.mkdir(exist_ok=True)


def _recent_notes_files(limit: int | None = None) -> list[pathlib.Path]:
    if not NOTES_DIR.exists():
        return []
    files = sorted(NOTES_DIR.glob("*_notes.txt"), reverse=True)
    return files[:limit] if limit else files


def _text(msg: str) -> list[TextContent]:
    return [TextContent(type="text", text=msg)]


async def handle_join(meeting_link: str, chat_message: str = "") -> list[TextContent]:
    if not meeting_link:
        return _text("Missing meeting_link.")

    if "teams.microsoft.com" not in meeting_link and "teams.live.com" not in meeting_link:
        return _text(f"That doesn't look like a Teams meeting link: {meeting_link}")

    if state.is_running():
        elapsed = int((datetime.datetime.now() - state.started_at).total_seconds())
        return _text(
            f"A bot is already running (joined {elapsed}s ago).\n"
            f"Meeting link: {state.meeting_link}\n"
            f"Ask me to stop it first with leave_meeting if you want to start a new one."
        )

    # Bot inherits env vars from Claude Desktop's shell (usually ~/.zshrc).
    missing = [v for v in ("TEAMS_BOT_EMAIL", "TEAMS_BOT_PASSWORD") if not os.environ.get(v)]
    if missing:
        return _text(
            f"Cannot start the bot — these env vars are not set: {', '.join(missing)}.\n"
            f"They are normally set in ~/.zshrc. After editing, restart Claude Desktop so it picks them up."
        )

    ts = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    log_path = LOG_DIR / f"{ts}.log"
    log_file = open(log_path, "w")

    cmd = [sys.executable, str(BOT_SCRIPT), "--link", meeting_link]
    if chat_message:
        cmd += ["--chat-message", chat_message]

    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,   # don't let the child touch the MCP stdio pipe
            stdout=log_file,
            stderr=subprocess.STDOUT,
            cwd=str(HERE.parent),
            env=os.environ.copy(),
            start_new_session=True,
        )
    except Exception as e:
        return _text(f"Failed to start bot: {e}")
    finally:
        log_file.close()   # child has its own dup'd FD; release the parent's

    state.process      = proc
    state.meeting_link = meeting_link
    state.started_at   = datetime.datetime.now()
    state.log_path     = log_path

    chat_note = ""
    if chat_message:
        chat_note = f'\nI\'ll post this in the meeting chat ~3 minutes after joining: "{chat_message}"'

    return _text(
        f"Bot is launching and joining the meeting.\n"
        f"Log file: {log_path}\n"
        f"The bot will auto-leave after 4 minutes of silence or when everyone else leaves. "
        f"Notes will be saved to {NOTES_DIR}/ when the meeting ends."
        f"{chat_note}"
    )


async def handle_status() -> list[TextContent]:
    if not state.is_running():
        recent = _recent_notes_files(limit=1)
        if recent:
            mtime = datetime.datetime.fromtimestamp(recent[0].stat().st_mtime)
            ago = int((datetime.datetime.now() - mtime).total_seconds())
            return _text(
                f"No bot is running right now.\n"
                f"Most recent notes: {recent[0].name} (saved {ago}s ago)\n"
                f"Full path: {recent[0]}"
            )
        return _text("No bot is running and no saved notes found.")

    elapsed = int((datetime.datetime.now() - state.started_at).total_seconds())

    tail = ""
    if state.log_path and state.log_path.exists():
        try:
            lines = state.log_path.read_text().splitlines()
            tail = "\n".join(lines[-15:])
        except Exception:
            pass

    return _text(
        f"Bot is in a meeting.\n"
        f"Joined: {state.started_at.strftime('%H:%M:%S')} ({elapsed}s ago)\n"
        f"Meeting link: {state.meeting_link}\n"
        f"\nRecent bot log output:\n"
        f"{'─' * 50}\n"
        f"{tail or '(no log output yet)'}"
    )


async def handle_leave() -> list[TextContent]:
    if not state.is_running():
        state.clear()
        return _text("No bot is currently running.")

    state.leave_requested_at = datetime.datetime.now()

    # SIGINT → bot's graceful shutdown (leave, transcribe, save). The detached
    # subprocess survives this MCP server exiting.
    try:
        state.process.send_signal(2)
    except Exception as e:
        return _text(f"Failed to signal bot: {e}")

    bot_pid = state.process.pid
    state.clear()
    state.leave_requested_at = datetime.datetime.now()

    return _text(
        f"Left the meeting. Transcription and summary are now being generated "
        f"in the background (takes 1-3 minutes depending on meeting length). "
        f"The notes will be saved to {NOTES_DIR.name}/ when ready. "
        f"(Background process PID: {bot_pid})"
    )


async def handle_get_last_notes() -> list[TextContent]:
    deadline = datetime.datetime.now() + datetime.timedelta(minutes=5)
    # If a leave was requested, only accept a notes file newer than that moment.
    leave_time = state.leave_requested_at

    while True:
        recent = _recent_notes_files(limit=1)
        if recent:
            latest = recent[0]
            latest_mtime = datetime.datetime.fromtimestamp(latest.stat().st_mtime)
            ready = (leave_time is None) or (latest_mtime >= leave_time)
            if ready:
                try:
                    notes_text = latest.read_text()
                except Exception as e:
                    return _text(f"Could not read notes: {e}")
                return _text(f"═══ NOTES ({latest.name}) ═══\n\n{notes_text}")

        if datetime.datetime.now() > deadline:
            return _text(
                "Notes aren't ready yet (timed out after 5 minutes). "
                "Transcription may still be running — try again in a moment, "
                "or check the bot-logs folder for progress."
            )

        await asyncio.sleep(3)


async def handle_list_notes(limit: int) -> list[TextContent]:
    if not NOTES_DIR.exists():
        return _text("No meeting notes have been saved yet.")

    files = _recent_notes_files(limit=max(1, limit))
    if not files:
        return _text("No meeting notes found.")

    lines = [f"Found {len(files)} saved meeting(s):\n"]
    for nf in files:
        timestamp = nf.stem.replace("_notes", "")
        try:
            text = nf.read_text()
            preview = ""
            if "MEETING SUMMARY" in text:
                after = text.split("MEETING SUMMARY", 1)[1].lstrip("\n")
                preview = "\n".join(l for l in after.split("\n")[:3] if l.strip())[:250]
            else:
                preview = text[:250].strip()
            lines.append(f"• {timestamp}\n  {preview}\n")
        except Exception as e:
            lines.append(f"• {timestamp}  (could not read: {e})\n")

    return _text("\n".join(lines))


async def handle_read_notes(timestamp: str, include_transcript: bool) -> list[TextContent]:
    if not NOTES_DIR.exists():
        return _text("No meeting notes have been saved yet.")

    if timestamp:
        # Prefix match: "2026-04-20" matches any meeting that day.
        candidates = sorted(NOTES_DIR.glob(f"{timestamp}*_notes.txt"), reverse=True)
        if not candidates:
            return _text(f"No notes found matching timestamp '{timestamp}'.")
        notes_file = candidates[0]
    else:
        candidates = _recent_notes_files(limit=1)
        if not candidates:
            return _text("No meeting notes found.")
        notes_file = candidates[0]

    try:
        notes_text = notes_file.read_text()
    except Exception as e:
        return _text(f"Could not read notes file: {e}")

    output = f"═══ NOTES ({notes_file.name}) ═══\n\n{notes_text}"

    if include_transcript:
        transcript_file = notes_file.with_name(notes_file.name.replace("_notes.txt", "_transcript.txt"))
        if transcript_file.exists():
            try:
                transcript_text = transcript_file.read_text()
                output += f"\n\n═══ FULL TRANSCRIPT ═══\n\n{transcript_text}"
            except Exception as e:
                output += f"\n\n(Could not read transcript: {e})"
        else:
            output += "\n\n(No transcript file found for this meeting.)"

    return _text(output)
