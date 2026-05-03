import asyncio

import numpy as np

from . import config


class AudioReceiver:
    """PCM chunks from the browser via CDP binding — bypasses Teams' CSP."""

    def __init__(self):
        self.chunks: list[np.ndarray] = []
        self._connected = asyncio.Event()

    async def register(self, context):
        """Exposes __notetakerSendAudio on every page. Call once per context."""

        async def on_audio(b64_chunk: str):
            import base64
            if not self._connected.is_set():
                self._connected.set()
                print("🔗  Browser audio stream connected (via CDP binding)")
            try:
                raw = base64.b64decode(b64_chunk)
                chunk = np.frombuffer(raw, dtype="<f4")
                self.chunks.append(chunk.copy())
                if len(self.chunks) % 100 == 0:
                    secs = len(self.chunks) * config.CHUNK_SIZE / config.SAMPLE_RATE
                    print(f"📊  Received {len(self.chunks)} chunks (~{secs:.1f}s audio)")
            except Exception as e:
                print(f"⚠️   Error decoding audio chunk: {e}")

        await context.expose_function("__notetakerSendAudio", on_audio)
        print("🎙  Audio CDP binding ready (window.__notetakerSendAudio)")

    async def wait_for_connection(self, timeout: float = 30.0):
        try:
            await asyncio.wait_for(self._connected.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            print("⚠️   No audio received after 30s — check browser console for [notetaker] logs.")

    def stop(self) -> np.ndarray:
        if not self.chunks:
            return np.array([], dtype=np.float32)
        audio = np.concatenate(self.chunks)
        print(f"⏹   Audio capture stopped — {len(audio) / config.SAMPLE_RATE:.1f}s received")
        return audio


async def watch_for_meeting_end(receiver: AudioReceiver, stop_event: asyncio.Event):
    """Set stop_event after SILENCE_LIMIT consecutive silent polls (45s warmup first)."""
    await asyncio.sleep(45)

    consecutive_silent = 0
    last_chunk_count = len(receiver.chunks)
    print(f"👀  Silence watcher armed. Baseline chunk count: {last_chunk_count}")

    while not stop_event.is_set():
        current_count = len(receiver.chunks)

        if current_count == last_chunk_count:
            consecutive_silent += 1
        else:
            new_chunks = receiver.chunks[last_chunk_count:current_count]
            combined = np.concatenate(new_chunks) if new_chunks else np.array([])
            rms = float(np.sqrt(np.mean(combined ** 2))) if len(combined) else 0.0

            if rms < config.RMS_THRESHOLD:
                consecutive_silent += 1
                print(f"    🔇 Silent poll #{consecutive_silent} (rms={rms:.5f} < {config.RMS_THRESHOLD})")
            else:
                if consecutive_silent > 0:
                    print(f"    🔊 Speech detected (rms={rms:.5f}) — resetting counter")
                consecutive_silent = 0

        last_chunk_count = current_count

        if consecutive_silent >= config.SILENCE_LIMIT:
            elapsed = config.SILENCE_LIMIT * 5
            print(f"\n🏁  Meeting end detected: {elapsed}s of silence (no audible speech)")
            stop_event.set()
            return

        await asyncio.sleep(5)
