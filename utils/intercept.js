(function () {
  const SAMPLE_RATE = 16000;
  const CHUNK_SIZE  = 4096;

  if (window.__notetakerInstalled) {
    console.log("[notetaker] Already installed on this page, skipping.");
    return;
  }
  window.__notetakerInstalled = true;

  // ── Module state ──────────────────────────────────────────────────────────
  let audioCtx       = null;
  let chunksSent     = 0;
  const hooked       = new Set();
  const activeTracks = new Map();
  const allPCs       = new Set();   // every RTCPeerConnection we've seen; used by __notetakerPlayAudio

  // ── Heartbeat ─────────────────────────────────────────────────────────────
  setInterval(() => {
    console.log(`[notetaker] Heartbeat: tracks=${activeTracks.size}, chunksSent=${chunksSent}, hasSendFn=${typeof window.__notetakerSendAudio === 'function'}`);
  }, 10000);

  // ── Audio context ─────────────────────────────────────────────────────────
  function getAudioCtx() {
    if (!audioCtx || audioCtx.state === "closed") {
      audioCtx = new AudioContext({ sampleRate: SAMPLE_RATE });
    }
    if (audioCtx.state === "suspended") audioCtx.resume();
    return audioCtx;
  }

  // ── PCM encoding + send ───────────────────────────────────────────────────
  // CDP bindings only accept strings, hence base64.
  function pcmToBase64(pcm) {
    const bytes = new Uint8Array(pcm.buffer, pcm.byteOffset, pcm.byteLength);
    let binary = "";
    for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
    return btoa(binary);
  }

  function sendPcmChunk(pcm) {
    const sendFn = window.__notetakerSendAudio;
    if (typeof sendFn !== "function") return;
    try {
      sendFn(pcmToBase64(pcm));
      chunksSent++;
    } catch {}
  }

  // ── Track capture ─────────────────────────────────────────────────────────
  function cleanupTrack(trackId) {
    const entry = activeTracks.get(trackId);
    if (entry) {
      try { entry.processor.disconnect(); } catch {}
      try { entry.source.disconnect();    } catch {}
      activeTracks.delete(trackId);
    }
    hooked.delete(trackId);
  }

  function captureTrack(track, streamId) {
    if (!track || track.kind !== "audio") return;
    if (hooked.has(track.id))              return;
    hooked.add(track.id);

    console.log(`[notetaker] Capturing audio track ${track.id} (stream ${streamId}, readyState=${track.readyState})`);

    try {
      const ctx       = getAudioCtx();
      const stream    = new MediaStream([track]);
      const source    = ctx.createMediaStreamSource(stream);
      const processor = ctx.createScriptProcessor(CHUNK_SIZE, 1, 1);

      processor.onaudioprocess = (e) => sendPcmChunk(e.inputBuffer.getChannelData(0));

      source.connect(processor);
      processor.connect(ctx.destination);

      activeTracks.set(track.id, { source, processor, track });

      track.addEventListener("ended", () => {
        console.log(`[notetaker] Track ${track.id} ended`);
        cleanupTrack(track.id);
      });
      track.addEventListener("mute",   () => console.log(`[notetaker] Track ${track.id} muted`));
      track.addEventListener("unmute", () => console.log(`[notetaker] Track ${track.id} unmuted`));
    } catch (err) {
      console.log(`[notetaker] Failed to capture track ${track.id}:`, err.message || err);
      hooked.delete(track.id);
    }
  }

  // ── RTCPeerConnection patch ───────────────────────────────────────────────
  const OrigRTCPC = window.RTCPeerConnection;

  function PatchedRTCPeerConnection(...args) {
    const pc = new OrigRTCPC(...args);

    allPCs.add(pc);
    pc.addEventListener("connectionstatechange", () => {
      if (pc.connectionState === "closed" || pc.connectionState === "failed") {
        allPCs.delete(pc);
      }
    });

    pc.addEventListener("track", (e) => {
      const track    = e.track;
      const streamId = e.streams?.[0]?.id ?? "unknown";
      captureTrack(track, streamId);

      for (const stream of (e.streams || [])) {
        stream.addEventListener?.("addtrack", (ev) => {
          if (ev.track) captureTrack(ev.track, stream.id);
        });
      }
    });

    return pc;
  }

  PatchedRTCPeerConnection.prototype = OrigRTCPC.prototype;
  Object.setPrototypeOf(PatchedRTCPeerConnection, OrigRTCPC);
  Object.assign(PatchedRTCPeerConnection, OrigRTCPC);
  window.RTCPeerConnection = PatchedRTCPeerConnection;

  // ── Outbound audio injection ───────────────────────────────────────────────
  // Decode an audio URL (data: or http) and route it into every outbound audio
  // RTCRtpSender via replaceTrack(). When playback ends, restores originals.
  // Returns a serialisable result object so page.evaluate() can report back.
  window.__notetakerPlayAudio = async function (url) {
    const senders = [];
    for (const pc of allPCs) {
      try {
        for (const s of pc.getSenders()) {
          if (s.track && s.track.kind === "audio") senders.push(s);
        }
      } catch {}
    }
    console.log(`[notetaker] PlayAudio: ${senders.length} outbound audio sender(s)`);
    if (senders.length === 0) return { ok: false, reason: "no-outbound-audio-sender" };

    let ctx = null;
    const originals = [];
    try {
      ctx = new AudioContext();
      if (ctx.state === "suspended") await ctx.resume();

      const buffer = await fetch(url)
        .then((r) => r.arrayBuffer())
        .then((b) => ctx.decodeAudioData(b));

      const dest = ctx.createMediaStreamDestination();
      const src  = ctx.createBufferSource();
      src.buffer = buffer;
      src.connect(dest);

      const playTrack = dest.stream.getAudioTracks()[0];

      for (const s of senders) {
        originals.push({ s, orig: s.track });
        try { await s.replaceTrack(playTrack); }
        catch (e) { console.log(`[notetaker] replaceTrack failed:`, e && e.message || e); }
      }

      console.log(`[notetaker] Playing ${buffer.duration.toFixed(1)}s of audio`);
      await new Promise((resolve) => {
        src.onended = resolve;
        src.start();
      });
      console.log(`[notetaker] Playback finished`);

      return { ok: true, duration: buffer.duration, senders: senders.length };
    } catch (e) {
      console.log(`[notetaker] PlayAudio error:`, e && e.message || e);
      return { ok: false, reason: String(e && e.message || e) };
    } finally {
      // Always try to restore the original tracks, whatever happened above.
      for (const { s, orig } of originals) {
        try { await s.replaceTrack(orig); }
        catch (e) { console.log(`[notetaker] restoreTrack failed:`, e && e.message || e); }
      }
      try { ctx && ctx.close(); } catch {}
    }
  };

  console.log("[notetaker] RTCPeerConnection patched — waiting for tracks...");
})();
