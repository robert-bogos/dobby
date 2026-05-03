import asyncio
import datetime
import signal
import sys

from . import config
from .audio import AudioReceiver, watch_for_meeting_end
from .chat import schedule_chat_message
from .notes import save_notes
from .summarize import summarize
from .teams import join_teams_meeting, leave_meeting
from .transcribe import transcribe


async def run(args):
    config.WHISPER_MODEL = args.whisper_model
    config.OLLAMA_MODEL  = args.ollama_model

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
