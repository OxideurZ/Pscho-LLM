import { useCallback, useEffect, useRef, useState } from "react";
import {
  appendVoiceChunk,
  cancelVoiceJob,
  createVoiceJob,
  finalizeVoiceJob,
  loadVoiceJob,
} from "../api/chat";

export type VoiceState =
  | "idle"
  | "requesting_permission"
  | "recording"
  | "transcribing"
  | "transcript_ready"
  | "cancel_requested"
  | "cancelled"
  | "error";

export type VoiceBinding = {
  voiceInputId: string;
  clientTurnId: string;
  conversationId: string;
};

export const VOICE_AUTO_SEND_GRACE_MS = 1_500;
const MAX_RECORDING_MS = 15 * 60 * 1_000;
const NORMALIZED_SAMPLE_RATE = 16_000;
const UPLOAD_CHUNK_BYTES = 1_024 * 1_024;

export function useVoiceRecorder(
  onSend: (text: string, binding: VoiceBinding) => Promise<void>,
  autoSend: boolean,
) {
  const [state, setState] = useState<VoiceState>("idle");
  const [preview, setPreview] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [levels, setLevels] = useState<number[]>(() => Array.from({ length: 20 }, () => 0.08));
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const recorder = useRef<MediaRecorder | null>(null);
  const stream = useRef<MediaStream | null>(null);
  const parts = useRef<Blob[]>([]);
  const binding = useRef<VoiceBinding | null>(null);
  const timeout = useRef<number | null>(null);
  const elapsedTimer = useRef<number | null>(null);
  const animationFrame = useRef<number | null>(null);
  const visualContext = useRef<AudioContext | null>(null);
  const discardRecording = useRef(false);
  const previewEdited = useRef(false);

  const release = useCallback(() => {
    if (timeout.current !== null) window.clearTimeout(timeout.current);
    if (elapsedTimer.current !== null) window.clearInterval(elapsedTimer.current);
    if (animationFrame.current !== null) window.cancelAnimationFrame(animationFrame.current);
    timeout.current = null;
    elapsedTimer.current = null;
    animationFrame.current = null;
    if (visualContext.current) void visualContext.current.close();
    visualContext.current = null;
    stream.current?.getTracks().forEach((track) => track.stop());
    stream.current = null;
    recorder.current = null;
    parts.current = [];
    setLevels(Array.from({ length: 20 }, () => 0.08));
  }, []);

  const poll = useCallback(async (voiceInputId: string) => {
    for (;;) {
      const job = await loadVoiceJob(voiceInputId);
      if (job.status === "transcript_ready") {
        previewEdited.current = false;
        setPreview(job.transcript ?? "");
        setState("transcript_ready");
        return;
      }
      if (job.status === "cancelled") {
        setState("cancelled");
        return;
      }
      if (job.status === "failed") {
        setError(job.error_code ?? "STT_FAILED");
        setState("error");
        return;
      }
      await new Promise((resolve) => window.setTimeout(resolve, 350));
    }
  }, []);

  const send = useCallback(async () => {
    const frozen = binding.current;
    if (!frozen || !preview.trim() || state !== "transcript_ready") return;
    await onSend(preview.trim(), frozen);
    try {
      await cancelVoiceJob(frozen.voiceInputId);
    } catch {
      // The conversation commit succeeded; the backend still abandons VoiceJobs on restart.
    }
    setPreview("");
    binding.current = null;
    setState("idle");
  }, [onSend, preview, state]);

  useEffect(() => {
    if (state !== "transcript_ready" || !autoSend || previewEdited.current || !preview.trim()) {
      return;
    }
    const timer = window.setTimeout(() => {
      void send().catch(() => {
        setError("VOICE_AUTO_SEND_FAILED");
        setState("error");
      });
    }, VOICE_AUTO_SEND_GRACE_MS);
    return () => window.clearTimeout(timer);
  }, [autoSend, preview, send, state]);

  const uploadAndTranscribe = useCallback(
    async (frozen: VoiceBinding) => {
      try {
        const wav = await mediaPartsToWav(parts.current);
        release();
        await createVoiceJob(frozen.voiceInputId, frozen.conversationId, frozen.clientTurnId);
        for (let offset = 0; offset < wav.size; offset += UPLOAD_CHUNK_BYTES) {
          await appendVoiceChunk(
            frozen.voiceInputId,
            wav.slice(offset, Math.min(offset + UPLOAD_CHUNK_BYTES, wav.size)),
          );
        }
        await finalizeVoiceJob(frozen.voiceInputId);
        setState("transcribing");
        await poll(frozen.voiceInputId);
      } catch (caught) {
        release();
        setError(errorCode(caught, "STT_FAILED"));
        setState("error");
      }
    },
    [poll, release],
  );

  const stop = useCallback(() => {
    if (recorder.current?.state === "recording") recorder.current.stop();
  }, []);

  const start = useCallback(
    async (conversationId: string) => {
      if (state !== "idle" && state !== "cancelled" && state !== "error") return;
      setError(null);
      setPreview("");
      discardRecording.current = false;
      previewEdited.current = false;
      setState("requesting_permission");
      const frozen: VoiceBinding = {
        voiceInputId: crypto.randomUUID(),
        clientTurnId: crypto.randomUUID(),
        conversationId,
      };
      binding.current = frozen;
      try {
        stream.current = await navigator.mediaDevices.getUserMedia({
          audio: { autoGainControl: true, echoCancellation: true, noiseSuppression: true },
        });
        startVisualiser(stream.current, visualContext, animationFrame, setLevels);
        const preferredMime = "audio/webm;codecs=opus";
        recorder.current = MediaRecorder.isTypeSupported(preferredMime)
          ? new MediaRecorder(stream.current, { mimeType: preferredMime })
          : new MediaRecorder(stream.current);
        parts.current = [];
        recorder.current.ondataavailable = (event) => {
          if (event.data.size) parts.current.push(event.data);
        };
        recorder.current.onstop = () => {
          if (discardRecording.current) {
            release();
            return;
          }
          void uploadAndTranscribe(frozen);
        };
        recorder.current.start(1_000);
        setState("recording");
        const startedAt = Date.now();
        setElapsedSeconds(0);
        elapsedTimer.current = window.setInterval(
          () => setElapsedSeconds(Math.floor((Date.now() - startedAt) / 1_000)),
          1_000,
        );
        timeout.current = window.setTimeout(stop, MAX_RECORDING_MS);
      } catch (caught) {
        release();
        setError(errorCode(caught, "MICROPHONE_UNAVAILABLE"));
        setState("error");
      }
    },
    [release, state, stop, uploadAndTranscribe],
  );

  const updatePreview = useCallback((value: string) => {
    previewEdited.current = true;
    setPreview(value);
  }, []);

  const cancel = useCallback(async () => {
    const frozen = binding.current;
    if (state === "recording" || state === "requesting_permission") {
      discardRecording.current = true;
      recorder.current?.stop();
      release();
      binding.current = null;
      setState("cancelled");
      return;
    }
    if (!frozen || (state !== "transcribing" && state !== "transcript_ready")) return;
    setState("cancel_requested");
    try {
      await cancelVoiceJob(frozen.voiceInputId);
      setPreview("");
      binding.current = null;
      setState("cancelled");
    } catch {
      setError("STT_CANCEL_FAILED");
      setState("error");
    }
  }, [release, state]);

  useEffect(() => () => release(), [release]);
  useEffect(() => {
    const abandon = () => {
      const frozen = binding.current;
      if (frozen && (state === "transcribing" || state === "transcript_ready")) {
        const endpoint = `/v1/stt/jobs/${encodeURIComponent(frozen.voiceInputId)}/cancel`;
        if (!navigator.sendBeacon?.(endpoint, new Blob())) {
          void cancelVoiceJob(frozen.voiceInputId);
        }
      }
      discardRecording.current = true;
      recorder.current?.stop();
      release();
    };
    window.addEventListener("pagehide", abandon);
    return () => window.removeEventListener("pagehide", abandon);
  }, [release, state]);
  return {
    state,
    preview,
    setPreview: updatePreview,
    error,
    levels,
    elapsedSeconds,
    start,
    stop,
    cancel,
    send,
  };
}

function startVisualiser(
  mediaStream: MediaStream,
  contextRef: { current: AudioContext | null },
  frameRef: { current: number | null },
  setLevels: (levels: number[]) => void,
) {
  const context = new AudioContext();
  const analyser = context.createAnalyser();
  analyser.fftSize = 64;
  context.createMediaStreamSource(mediaStream).connect(analyser);
  const data = new Uint8Array(analyser.frequencyBinCount);
  contextRef.current = context;
  const paint = () => {
    analyser.getByteFrequencyData(data);
    const bars = Array.from({ length: 20 }, (_unused, index) => {
      const start = Math.floor((index * data.length) / 20);
      const end = Math.max(start + 1, Math.floor(((index + 1) * data.length) / 20));
      const average = data.slice(start, end).reduce((sum, value) => sum + value, 0) / (end - start);
      return Math.max(0.08, Math.min(1, average / 180));
    });
    setLevels(bars);
    frameRef.current = window.requestAnimationFrame(paint);
  };
  paint();
}

function errorCode(error: unknown, fallback: string): string {
  if (error instanceof DOMException) {
    if (error.name === "NotAllowedError") return "MIC_PERMISSION_DENIED";
    if (error.name === "NotFoundError") return "MIC_UNAVAILABLE";
  }
  if (typeof error === "object" && error !== null && "code" in error) {
    const code = (error as { code?: unknown }).code;
    if (typeof code === "string") return code;
  }
  return fallback;
}

async function mediaPartsToWav(parts: Blob[]): Promise<Blob> {
  const source = new Blob(parts, { type: "audio/webm" });
  const context = new AudioContext();
  try {
    const decoded = await context.decodeAudioData(await source.arrayBuffer());
    const offline = new OfflineAudioContext(
      1,
      Math.ceil(decoded.duration * NORMALIZED_SAMPLE_RATE),
      NORMALIZED_SAMPLE_RATE,
    );
    const input = offline.createBufferSource();
    input.buffer = decoded;
    input.connect(offline.destination);
    input.start();
    const rendered = await offline.startRendering();
    const samples = rendered.getChannelData(0);
    const output = new ArrayBuffer(44 + samples.length * 2);
    const view = new DataView(output);
    writeWavHeader(view, NORMALIZED_SAMPLE_RATE, samples.length);
    for (let index = 0; index < samples.length; index += 1) {
      view.setInt16(
        44 + index * 2,
        Math.max(-1, Math.min(1, samples[index])) * 0x7fff,
        true,
      );
    }
    return new Blob([output], { type: "audio/wav" });
  } finally {
    await context.close();
  }
}

function writeWavHeader(view: DataView, sampleRate: number, sampleCount: number) {
  const text = (offset: number, value: string) =>
    [...value].forEach((char, index) => view.setUint8(offset + index, char.charCodeAt(0)));
  text(0, "RIFF");
  view.setUint32(4, 36 + sampleCount * 2, true);
  text(8, "WAVEfmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  text(36, "data");
  view.setUint32(40, sampleCount * 2, true);
}
