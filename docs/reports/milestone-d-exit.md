# Milestone D - Exit report (validation in progress)

Spec: `milestone-d-spec:v1.0`  
Application commit validated: `34fc675`  
Closure status: **PENDING explicit real-microphone validation**

## Configuration

- OS: Windows reference workstation.
- CPU / RAM: Intel Core i7-10750H; 31.8 GiB RAM.
- GPU / VRAM: NVIDIA RTX 2070 Max-Q; 8,192 MiB.
- llama.cpp / Qwen: b9637; `Qwen3.6-35B-A3B-Q4_K_M`, SHA256
  `671e47e0ec53c665d048b98c3ecbfd5236b5ca9c3e02ed19fc8f81f7b85140c7`.
- STT: `whisper.cpp` v1.9.2 CUDA 12.4, Whisper Large-v3-Turbo
  (`ggml-large-v3-turbo.bin`, SHA256
  `1fc70f774d38eb169993ac391eea357ef47c88757ef72ee5943879b7e8e2bc69`).
- Process model: one cancellable `whisper-cli.exe` child per ephemeral VoiceJob.
- `MAX_RECORDING_DURATION`: 900 s (15 min).
- `auto_send_voice`: ON by default; `VOICE_AUTO_SEND_GRACE_MS=1500`.

## D0 selection

The mandated two-backend comparison is recorded in
[`milestone-d-stt-spike.md`](milestone-d-stt-spike.md). No third backend or model was compared.
`whisper.cpp` was selected for its stable CUDA coexistence with Qwen, cheap per-job process model
and 3-minute RTF of 0.0378 (versus 0.0905 for faster-whisper).

## Audio and lifecycle

- Browser: `MediaRecorder` captures local chunks; stop is explicit and silence never stops capture.
- Normalization: browser Web Audio decode followed by offline mono 16 kHz PCM WAV rendering.
- Transport: bounded WAV chunks (1 MiB) to an ephemeral backend directory.
- The backend accepts one active transcription; competing STT work is cleanly rejected.
- Audio is deleted at transcript completion, cancellation, error and backend shutdown. It is never
  stored in SQLite, backup, logs or Git.
- Voice identity is frozen at recording start: `voice_input_id`, `conversation_id`,
  `client_turn_id`. A later navigation cannot redirect its turn.
- `transcript_ready` provides an editable preview. Auto-send waits 1.5 seconds and is cancelled by
  any edit; the setting can be switched off. Cancel removes the preview and creates no turn.

## Real selected-backend measurements

Qwen was loaded for every measurement below. VRAM is adapter-total under Windows WDDM (per-process
attribution is unavailable), so it includes Qwen.

| Fixture | Audio | STT time | RTF | Runner RAM | GPU total | Result |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| French short | 31.6 s | 53.389 s | 1.6895 | 21 MiB | 7,807 MiB | complete |
| French 3 min | 179.1 s | 6.773 s | 0.0378 | 22 MiB | 7,763 MiB | complete |
| French long (>10 min) | 665.9 s | 17.398 s | 0.0261 | 21 MiB | 7,787 MiB | complete |

The long fixture exceeds the required ten-minute class and remains below the 8,192 MiB adapter
capacity. The hard pipeline gate was also run against the live stack:

```text
STT transcript_ready -> existing /conversations/{id}/turns input_type=voice
-> Qwen response -> SQLite USER(input_type=voice) + complete ASSISTANT
```

Result: PASS. No parallel conversation pipeline exists.

## Safety, failures and privacy

- Unit coverage rejects silence, low ambient noise, breath-level signal and isolated click input
  before STT output can become a USER turn.
- Unit coverage verifies recording cancellation, STT cancellation, late-result rejection, one-job
  serialization and abandoned-job cleanup during backend shutdown.
- The launcher checks the local STT binary and model SHA before start and now force-terminates only
  its verified Windows process tree on stop.
- Privacy audit: `runtime/voice-jobs` was empty after validation; no audio files were found there;
  the technical log scan returned no `ULTRA_SECRET_VOICE_CARIBOU_2026` match. Remaining WAV files
  are only D0 synthetic fixtures outside the repository.
- Selected-backend integration was run locally with no network dependency after installation.

## Software regression

```text
pytest                         76 passed
ruff check / format --check    passed
vitest                         10 passed
tsc --noEmit                   passed
vite build                     passed
git diff --check               passed
```

## Remaining hard-gate evidence

The only uncollected mandatory evidence is a consented **real browser microphone** recording on this
workstation (short natural French speech), including its permission/denial path. It is intentionally
not simulated and has not been activated without the user’s consent. Once that run is accepted, the
remaining report verdicts can be issued without another backend comparison.

```text
STT_RELIABILITY       = PROVISIONAL GO (synthetic 30 s, 3 min, >10 min)
PIPELINE_REUSE        = GO
VOICE_STATE_RECOVERY  = GO (automated lifecycle coverage)
NO_SPEECH_SAFETY      = GO (automated safety corpus)
PRIVACY_BASELINE      = GO
RESOURCE_COEXISTENCE  = GO
REGRESSION            = GO
REAL_MICROPHONE       = PENDING CONSENTED RUN

MILESTONE D = NOT CLOSED YET
```
