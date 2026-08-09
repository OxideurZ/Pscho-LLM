import { useCallback, useEffect, useRef, useState } from "react";
import {
  appendVoiceChunk,
  cancelVoiceJob,
  createVoiceJob,
  finalizeVoiceJob,
  loadVoiceJob,
  VoiceJob,
} from "../api/chat";

export type VoiceState = "idle" | "requesting_permission" | "recording" | "transcribing" | "transcript_ready" | "cancel_requested" | "cancelled" | "error";

type VoiceBinding = { voiceInputId: string; clientTurnId: string; conversationId: string };
const MAX_RECORDING_MS = 15 * 60 * 1000;

export function useVoiceRecorder(onSend: (text: string, binding: VoiceBinding) => Promise<void>) {
  const [state, setState] = useState<VoiceState>("idle");
  const [preview, setPreview] = useState("");
  const [error, setError] = useState<string | null>(null);
  const recorder = useRef<MediaRecorder | null>(null);
  const stream = useRef<MediaStream | null>(null);
  const parts = useRef<Blob[]>([]);
  const binding = useRef<VoiceBinding | null>(null);
  const timeout = useRef<number | null>(null);

  const release = useCallback(() => {
    if (timeout.current !== null) window.clearTimeout(timeout.current);
    timeout.current = null;
    stream.current?.getTracks().forEach((track) => track.stop());
    stream.current = null;
    recorder.current = null;
    parts.current = [];
  }, []);

  const poll = useCallback(async (voiceInputId: string) => {
    for (;;) {
      const job = await loadVoiceJob(voiceInputId);
      if (job.status === "transcript_ready") {
        setPreview(job.transcript ?? "");
        setState("transcript_ready");
        return;
      }
      if (job.status === "cancelled") { setState("cancelled"); return; }
      if (job.status === "failed") { setError(job.error_code ?? "STT_FAILED"); setState("error"); return; }
      await new Promise((resolve) => window.setTimeout(resolve, 350));
    }
  }, []);

  const start = useCallback(async (conversationId: string) => {
    if (state !== "idle" && state !== "cancelled" && state !== "error") return;
    setError(null); setPreview(""); setState("requesting_permission");
    const frozen: VoiceBinding = { voiceInputId: crypto.randomUUID(), clientTurnId: crypto.randomUUID(), conversationId };
    binding.current = frozen;
    try {
      stream.current = await navigator.mediaDevices.getUserMedia({ audio: true });
      recorder.current = new MediaRecorder(stream.current, { mimeType: "audio/webm" });
      parts.current = [];
      recorder.current.ondataavailable = (event) => { if (event.data.size) parts.current.push(event.data); };
      recorder.current.onstop = () => { void uploadAndTranscribe(frozen); };
      recorder.current.start(1_000);
      setState("recording");
      timeout.current = window.setTimeout(() => { void stop(); }, MAX_RECORDING_MS);
    } catch {
      release(); setError("MICROPHONE_UNAVAILABLE"); setState("error");
    }
  // stop and uploadAndTranscribe only read stable refs and are intentionally declared below.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [release, state]);

  const uploadAndTranscribe = useCallback(async (frozen: VoiceBinding) => {
    try {
      const wav = await mediaPartsToWav(parts.current);
      release();
      await createVoiceJob(frozen.voiceInputId, frozen.conversationId, frozen.clientTurnId);
      await appendVoiceChunk(frozen.voiceInputId, wav);
      await finalizeVoiceJob(frozen.voiceInputId);
      setState("transcribing");
      await poll(frozen.voiceInputId);
    } catch {
      release(); setError("STT_FAILED"); setState("error");
    }
  }, [poll, release]);

  const stop = useCallback(async () => {
    if (state === "recording" && recorder.current?.state === "recording") recorder.current.stop();
  }, [state]);

  const cancel = useCallback(async () => {
    const frozen = binding.current;
    if (state === "recording") { recorder.current?.stop(); release(); setState("cancelled"); return; }
    if (!frozen || state !== "transcribing") return;
    setState("cancel_requested");
    try { await cancelVoiceJob(frozen.voiceInputId); setState("cancelled"); }
    catch { setError("STT_CANCEL_FAILED"); setState("error"); }
  }, [release, state]);

  const send = useCallback(async () => {
    const frozen = binding.current;
    if (!frozen || !preview.trim() || state !== "transcript_ready") return;
    await onSend(preview.trim(), frozen);
    setPreview(""); binding.current = null; setState("idle");
  }, [onSend, preview, state]);

  useEffect(() => () => release(), [release]);
  return { state, preview, setPreview, error, start, stop, cancel, send };
}

async function mediaPartsToWav(parts: Blob[]): Promise<Blob> {
  const source = new Blob(parts, { type: "audio/webm" });
  const context = new AudioContext();
  try {
    const decoded = await context.decodeAudioData(await source.arrayBuffer());
    const samples = decoded.getChannelData(0);
    const output = new ArrayBuffer(44 + samples.length * 2);
    const view = new DataView(output);
    writeWavHeader(view, decoded.sampleRate, samples.length);
    for (let index = 0; index < samples.length; index += 1) view.setInt16(44 + index * 2, Math.max(-1, Math.min(1, samples[index])) * 0x7fff, true);
    return new Blob([output], { type: "audio/wav" });
  } finally { await context.close(); }
}

function writeWavHeader(view: DataView, sampleRate: number, sampleCount: number) {
  const text = (offset: number, value: string) => [...value].forEach((char, index) => view.setUint8(offset + index, char.charCodeAt(0)));
  text(0, "RIFF"); view.setUint32(4, 36 + sampleCount * 2, true); text(8, "WAVEfmt "); view.setUint32(16, 16, true);
  view.setUint16(20, 1, true); view.setUint16(22, 1, true); view.setUint32(24, sampleRate, true); view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true); view.setUint16(34, 16, true); text(36, "data"); view.setUint32(40, sampleCount * 2, true);
}
