import os
import pathlib

SAMPLE_RATE   = 16000
CHANNELS      = 1
CHUNK_SIZE    = 4096
WHISPER_MODEL = "medium"
LLM_MODEL  = "gemma4:e4b"
OUTPUT_DIR    = str(pathlib.Path(__file__).parent.parent / "meeting-notes")

BOT_EMAIL        = os.environ.get("TEAMS_BOT_EMAIL", "")
BOT_PASSWORD     = os.environ.get("TEAMS_BOT_PASSWORD", "")
BOT_DISPLAY_NAME = os.environ.get("TEAMS_BOT_DISPLAY_NAME", "AI notetaker")
MEETING_PASSCODE = os.environ.get("TEAMS_MEETING_PASSCODE", "")

INTERCEPT_JS      = pathlib.Path(__file__).parent.parent / "utils" / "intercept.js"
AUDIO_PROMPT_PATH = pathlib.Path(__file__).parent.parent / "utils" / "question-in-chat.mp3"
FAKE_VIDEO_PATH   = pathlib.Path(__file__).parent.parent / "utils" / "bot-feed.y4m"

# Stealth patches below compensate — Microsoft sometimes blocks headless Chromium.
HEADLESS_MODE = True

SILENCE_LIMIT = 48      # 48 * 5s = 4 minutes of silence before auto-leave
RMS_THRESHOLD = 0.005   # speech ~0.02+, room hiss ~0.001–0.005
