import datetime
import pathlib
import subprocess


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
