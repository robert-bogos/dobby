#!/usr/bin/env python3
"""
MCP server wrapping bot.py for Claude Desktop.

Tools: join_meeting, meeting_status, leave_meeting, list_saved_notes,
get_last_meeting_notes, read_notes. One bot at a time; notes under ./meeting-notes/.
"""

import asyncio
import datetime
import os
import pathlib
import subprocess
import sys

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent


# ── Paths ────────────────────────────────────────────────────────────────────

HERE       = pathlib.Path(__file__).parent.resolve()
BOT_SCRIPT = HERE / "bot.py"
NOTES_DIR  = HERE / "meeting-notes"
LOG_DIR    = HERE / "bot-logs"
LOG_DIR.mkdir(exist_ok=True)


# ── State ────────────────────────────────────────────────────────────────────

class BotState:
    def __init__(self):
        self.process: subprocess.Popen | None = None
        self.meeting_link: str | None = None
        self.started_at: datetime.datetime | None = None
        self.log_path: pathlib.Path | None = None
        # get_last_meeting_notes uses this to wait for a file newer than the leave.
        self.leave_requested_at: datetime.datetime | None = None

    def is_running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def clear(self):
        self.process = None
        self.meeting_link = None
        self.started_at = None
        self.log_path = None
        # Keep leave_requested_at — post-leave notes lookups still need it.


state = BotState()


# ── Helpers ──────────────────────────────────────────────────────────────────

def _recent_notes_files(limit: int | None = None) -> list[pathlib.Path]:
    if not NOTES_DIR.exists():
        return []
    files = sorted(NOTES_DIR.glob("*_notes.txt"), reverse=True)
    return files[:limit] if limit else files


def _text(msg: str) -> list[TextContent]:
    return [TextContent(type="text", text=msg)]


# ── Tool schemas ─────────────────────────────────────────────────────────────
# Descriptions are prompt engineering — Claude Desktop reads them to frame responses.

JOIN_DESCRIPTION = (
    "Have Claude join a Microsoft Teams meeting on the user's behalf to "
    "take notes. When the user asks to join or attend a meeting, Claude "
    "SHOULD frame this in the first person — 'I'll join the meeting now' "
    "rather than 'I'll send the bot' or 'I'll start the notetaker'. From "
    "the user's perspective, Claude IS the notetaker. Do not mention "
    "the bot, the notetaker, Whisper, or any implementation details "
    "unless the user explicitly asks about them. If the user has not "
    "provided a meeting link, ask for it directly (e.g. 'What's the "
    "meeting link?'). "
    "\n\n"
    "CHAT MESSAGE: Before calling this tool, Claude SHOULD ask the user "
    "whether they want Claude to post a message in the meeting chat "
    "after joining — e.g. 'Want me to post a message in the chat once "
    "I'm in? (like a heads-up that you're running late, or a quick "
    "intro?)'. If the user provides a message, pass it as the "
    "`chat_message` parameter. If the user declines or doesn't mention "
    "it, omit the parameter. The message will be posted ~3 minutes "
    "after Claude has joined the meeting. "
    "\n\n"
    "Only one meeting at a time — if Claude is already in a meeting "
    "this will return an error."
)

STATUS_DESCRIPTION = (
    "Check whether Claude is currently in a meeting, how long it has "
    "been there, and recent activity. Claude SHOULD frame responses in "
    "the first person — 'Yes, I'm still in the meeting, been about 15 "
    "minutes' rather than 'The bot is in the meeting'. Use this when "
    "the user asks whether Claude is still in the meeting, how long "
    "it's been, or what's happening."
)

LEAVE_DESCRIPTION = (
    "Leave the current meeting now. Returns immediately — transcription "
    "and summarisation continue in the background after this returns. "
    "Claude SHOULD frame this in the first person ('I've left the "
    "meeting — I'm writing up my notes now, it'll take a minute. Want "
    "me to share them when they're ready?') rather than 'I'll stop the "
    "bot'. Claude SHOULD ask the user if they want to see the notes "
    "rather than returning them unprompted. When the user says yes, "
    "call get_last_meeting_notes (which will wait for notes to finish "
    "if they're still processing)."
)

LIST_DESCRIPTION = (
    "List past meetings Claude has attended, sorted newest first. "
    "Each entry includes the start timestamp and a short preview. "
    "Claude SHOULD frame responses in the first person — 'Here are "
    "the meetings I've attended recently' rather than 'Here are the "
    "saved notes from the bot'. "
    "\n\n"
    "CRITICAL: Claude MUST call this tool whenever the user asks "
    "about past meetings and Claude is not sure which specific "
    "meeting they mean. Do NOT guess or answer from memory about "
    "the content or date of past meetings. Always look them up."
)

GET_LAST_DESCRIPTION = (
    "Fetch the notes from the most recent meeting Claude attended. "
    "If the bot is still transcribing/summarising, this tool WAITS "
    "until the notes are ready (up to 5 minutes) and then returns "
    "them. If the notes are already saved, it returns them instantly. "
    "Use this when the user asks to see the notes from the meeting "
    "Claude just left, or asks 'what did I miss' right after Claude "
    "has left a meeting."
)

READ_DESCRIPTION = (
    "Retrieve the full notes and/or transcript from a past meeting. "
    "Claude SHOULD speak about these as its own observations — 'Here's "
    "what I captured from that meeting' rather than 'The bot's notes "
    "say...'. "
    "\n\n"
    "CRITICAL — NO HALLUCINATION: Claude MUST call this tool BEFORE "
    "answering ANY question about the content, topics, participants, "
    "decisions, or details of a past meeting. Never answer from memory. "
    "Never describe what a meeting was about without first reading the "
    "notes. If the user asks 'what was the meeting at 7pm about' or "
    "'what did we discuss in the Monday call', Claude MUST call this "
    "tool first with the appropriate timestamp (prefix matching is "
    "supported, e.g. '2026-04-20' or '2026-04-20_19' for 7pm on that "
    "day). Only after reading the actual notes may Claude answer. "
    "If the timestamp doesn't match any saved meeting, say so "
    "honestly — do NOT invent content."
)

TOOLS = [
    Tool(
        name="join_meeting",
        description=JOIN_DESCRIPTION,
        inputSchema={
            "type": "object",
            "properties": {
                "meeting_link": {
                    "type": "string",
                    "description": "The full Microsoft Teams meeting URL, including the passcode parameter if present.",
                },
                "chat_message": {
                    "type": "string",
                    "description": (
                        "Optional message to post in the meeting chat ~3 minutes "
                        "after Claude joins. Omit if the user doesn't want a chat "
                        "message posted. Ask the user before passing this — don't "
                        "invent a message on your own."
                    ),
                },
            },
            "required": ["meeting_link"],
        },
    ),
    Tool(
        name="meeting_status",
        description=STATUS_DESCRIPTION,
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="leave_meeting",
        description=LEAVE_DESCRIPTION,
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="list_saved_notes",
        description=LIST_DESCRIPTION,
        inputSchema={
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Max number of recent meetings to return. Defaults to 10.",
                }
            },
        },
    ),
    Tool(
        name="get_last_meeting_notes",
        description=GET_LAST_DESCRIPTION,
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="read_notes",
        description=READ_DESCRIPTION,
        inputSchema={
            "type": "object",
            "properties": {
                "timestamp": {
                    "type": "string",
                    "description": (
                        "Meeting START timestamp in YYYY-MM-DD_HH-MM format. "
                        "Prefix matching is supported: '2026-04-20' matches "
                        "any meeting that day, '2026-04-20_19' matches 7pm "
                        "that day. If omitted, returns the most recent meeting."
                    ),
                },
                "include_transcript": {
                    "type": "boolean",
                    "description": (
                        "Also include the full transcript. Defaults to false "
                        "(summary only). Set to true when the user asks for "
                        "quotes, specific wording, or details not in the summary."
                    ),
                },
            },
        },
    ),
]


# ── Tool handlers: live meeting ──────────────────────────────────────────────

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
            cwd=str(HERE),
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


# ── Tool handlers: saved notes ───────────────────────────────────────────────

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


# ── Server wiring ────────────────────────────────────────────────────────────

server = Server("ai-notetaker")

HANDLERS = {
    "join_meeting":           lambda args: handle_join(
                                  args.get("meeting_link", ""),
                                  args.get("chat_message", ""),
                              ),
    "meeting_status":         lambda args: handle_status(),
    "leave_meeting":          lambda args: handle_leave(),
    "list_saved_notes":       lambda args: handle_list_notes(args.get("limit", 10)),
    "get_last_meeting_notes": lambda args: handle_get_last_notes(),
    "read_notes":             lambda args: handle_read_notes(
                                  args.get("timestamp", ""),
                                  args.get("include_transcript", False),
                              ),
}


@server.list_tools()
async def list_tools() -> list[Tool]:
    return TOOLS


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    handler = HANDLERS.get(name)
    if handler is None:
        return _text(f"Unknown tool: {name}")
    return await handler(arguments)


# ── Entry point ──────────────────────────────────────────────────────────────

async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())