import asyncio

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from .handlers import (
    handle_get_last_notes,
    handle_join,
    handle_leave,
    handle_list_logs,
    handle_list_notes,
    handle_read_log,
    handle_read_notes,
    handle_status,
)
from .tools import TOOLS

server = Server("ai-notetaker")

HANDLERS = {
    "join_meeting":           lambda args: handle_join(
                                  args.get("meeting_link", ""),
                                  args.get("chat_message", ""),
                              ),
    "meeting_status":         lambda args: handle_status(),
    "leave_meeting":          lambda args: handle_leave(),
    "list_logs":              lambda args: handle_list_logs(),
    "read_log":               lambda args: handle_read_log(args.get("timestamp", "")),
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
        return [TextContent(type="text", text=f"Unknown tool: {name}")]
    return await handler(arguments)


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())
