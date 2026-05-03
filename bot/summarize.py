import sys

from . import config

SUMMARIZE_PROMPT = """You are a professional meeting notes assistant.

Given the following meeting transcript, produce concise plain-text meeting notes with these sections:

MEETING SUMMARY
A short 2-4 sentence overview of what was discussed.

KEY DECISIONS
Bullet list of decisions made during the meeting.

ACTION ITEMS
Bullet list of action items, with the responsible person if mentioned and a due date if mentioned.

IMPORTANT POINTS
Any other important points, risks, or topics raised.

Keep the notes factual and concise. If a section has nothing relevant, write "None noted."

TRANSCRIPT:
{transcript}
"""


def summarize(transcript: str) -> str:
    try:
        import ollama
    except ImportError:
        print("❌  ollama not installed. Run: pip install ollama")
        sys.exit(1)

    print(f"\n🤖  Summarising with Ollama ({config.OLLAMA_MODEL})...")
    try:
        response = ollama.chat(
            model=config.OLLAMA_MODEL,
            messages=[{"role": "user", "content": SUMMARIZE_PROMPT.format(transcript=transcript)}],
        )
        notes = response["message"]["content"]
    except Exception as e:
        print(f"❌  Ollama error: {e}")
        print("    Is Ollama running? Start it with: ollama serve")
        sys.exit(1)

    print("✅  Summary complete.")
    return notes
