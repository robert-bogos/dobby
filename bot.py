#!/usr/bin/env python3
import argparse
import asyncio
from bot import config
from bot.runner import run


def main():
    parser = argparse.ArgumentParser(
        description="Teams Bot Notetaker — WebRTC audio, fully local"
    )
    parser.add_argument("--link",          required=True,                help="Teams meeting URL")
    parser.add_argument("--passcode",      default="",                   help="Meeting passcode (overrides URL extraction)")
    parser.add_argument("--duration",      type=int,                     help="Max recording duration in seconds")
    parser.add_argument("--whisper-model", default=config.WHISPER_MODEL, help=f"Whisper model (default: {config.WHISPER_MODEL})")
    parser.add_argument("--ollama-model",  default=config.OLLAMA_MODEL,  help=f"Ollama model (default: {config.OLLAMA_MODEL})")
    parser.add_argument("--chat-message",  default="",                   help="Post this message in the meeting chat after joining")
    parser.add_argument("--chat-delay",    type=int, default=20,         help="Delay in seconds before posting --chat-message (default: 20)")
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
