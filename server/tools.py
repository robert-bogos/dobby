from mcp.types import Tool

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

LIST_LOGS_DESCRIPTION = (
    "List all bot log files, newest first. Each entry shows the timestamp "
    "and file size. Use this to find the right log before reading it."
)

READ_LOG_DESCRIPTION = (
    "Read the full contents of a bot log file for debugging. "
    "If no timestamp is given, returns the most recent log. "
    "Timestamp prefix matching is supported (e.g. '2026-04-20' or '2026-04-20_19')."
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
        name="list_logs",
        description=LIST_LOGS_DESCRIPTION,
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="read_log",
        description=READ_LOG_DESCRIPTION,
        inputSchema={
            "type": "object",
            "properties": {
                "timestamp": {
                    "type": "string",
                    "description": (
                        "Log timestamp prefix in YYYY-MM-DD or YYYY-MM-DD_HH-MM format. "
                        "If omitted, returns the most recent log."
                    ),
                },
            },
        },
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
