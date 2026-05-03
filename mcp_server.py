#!/usr/bin/env python3
"""MCP server entry point for Claude Desktop.

Tools: join_meeting, meeting_status, leave_meeting,
       list_saved_notes, get_last_meeting_notes, read_notes.
"""

import asyncio

from server.main import main

if __name__ == "__main__":
    asyncio.run(main())
