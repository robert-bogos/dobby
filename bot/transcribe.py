import os
import sys
import tempfile

import numpy as np
import scipy.io.wavfile as wav

from . import config


def transcribe(audio: np.ndarray) -> str:
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        print("❌  faster-whisper not installed. Run: pip install faster-whisper")
        sys.exit(1)

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp_path = tmp.name

    audio_int16 = (audio * 32767).astype(np.int16)
    wav.write(tmp_path, config.SAMPLE_RATE, audio_int16)

    print(f"\n🔄  Transcribing with Whisper ({config.WHISPER_MODEL})...")
    model = WhisperModel(config.WHISPER_MODEL, device="auto", compute_type="int8")
    segments, info = model.transcribe(tmp_path, beam_size=5)

    print(f"    Detected language: {info.language}")
    lines = [f"[{s.start:6.1f}s] {s.text.strip()}" for s in segments]
    os.unlink(tmp_path)

    print(f"✅  Transcription complete ({len(lines)} segments).")
    return "\n".join(lines)
