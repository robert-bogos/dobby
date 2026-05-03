import datetime
import os

from . import config


def save_notes(transcript: str, notes: str, started_at: datetime.datetime) -> tuple[str, str]:
    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
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

    notes_path = os.path.join(config.OUTPUT_DIR, f"{base}_notes.txt")
    with open(notes_path, "w") as f:
        f.write(header + notes + "\n")

    transcript_header = header.replace("Meeting Notes", "Full Transcript")
    transcript_path = os.path.join(config.OUTPUT_DIR, f"{base}_transcript.txt")
    with open(transcript_path, "w") as f:
        f.write(transcript_header + transcript + "\n")

    return notes_path, transcript_path
