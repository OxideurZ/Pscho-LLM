# Milestone D0 - STT hardware spike

Spec: `milestone-d-spec:v1.0`  
Date: 2026-08-09  
Decision: **SELECT `whisper.cpp` + Whisper Large-v3-Turbo, one cancellable child process per VoiceJob.**

## Scope and method

This is the mandatory, bounded comparison of the two specified candidates only:

1. `faster-whisper` 1.2.1 / CTranslate2 4.8.1, model
   `deepdml/faster-whisper-large-v3-turbo-ct2`;
2. `whisper.cpp` v1.9.2 CUDA 12.4 binary, model
   `ggerganov/whisper.cpp:ggml-large-v3-turbo.bin`.

No other backend or model was measured. The test computer is the configured Windows reference
machine (RTX 2070 Max-Q, 8 GiB VRAM), with the existing Qwen llama.cpp service already loaded and
healthy. Fixtures are artificial French speech created locally, stored outside the repository, and
never logged. The runner emits operational counts and timings only: it does not persist a transcript
or raw audio.

`RTF = transcription wall time / audio duration`; lower is better. RAM is the runner process peak.
Under Windows WDDM, `nvidia-smi` cannot reliably attribute memory to an individual CUDA process, so
the recorded VRAM column is the peak GPU-total during each run, including Qwen. The pre-run Qwen
baseline was 6,504 MiB.

## Results

| Backend | Audio | Wall time | RTF | Runner RAM peak | GPU-total peak | Completion |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| faster-whisper | 31.6 s | 15.494 s | 0.4903 | 1,910 MiB | unavailable* | complete |
| whisper.cpp | 31.6 s | 53.389 s | 1.6895 | 21 MiB | 7,807 MiB | complete |
| faster-whisper | 179.1 s | 16.214 s | 0.0905 | 1,799 MiB | 7,136 MiB | complete |
| whisper.cpp | 179.1 s | 6.773 s | 0.0378 | 22 MiB | 7,763 MiB | complete |

\* The first faster-whisper measurement was taken before the runner's WDDM-compatible
GPU-total probe was corrected. It completed with Qwen resident; the later three-minute measurement
provides the comparable coexistence figure. No measurement was repeated solely to fill this field.

Both backends produced non-empty recognition output on both fixtures; output text is intentionally
not retained. Neither fixture was truncated. The 7,807 MiB maximum stays below the 8,192 MiB adapter
capacity, but leaves little headroom. The runtime must therefore admit only one STT job, release the
STT child before generation, and surface a safe resource error rather than contend with Qwen.

## Decision and process model

`whisper.cpp` is selected because it has the best long-audio result by a material margin (RTF 0.0378
versus 0.0905), its measured GPU-total stays within capacity with Qwen loaded, and it naturally fits
the required cancellation semantics: each VoiceJob owns one `whisper-cli.exe` child process. The
backend can terminate that recorded child on cancel, verify the exit, remove all ephemeral audio and
working files, and then emit exactly one terminal voice state.

The selected initial model is **Whisper Large-v3-Turbo** (`ggml-large-v3-turbo.bin`). It is not a
browser or client-side STT implementation. Browser audio remains ephemeral input to the local backend;
after successful transcription, the backend will call the existing `create_turn(...,
input_type="voice")` path rather than build a parallel conversation pipeline.

## D0 exit

```text
Comparison scope (exactly two backends) = PASS
30-second French fixture                = PASS
3-minute French fixture                 = PASS
Qwen coexistence below adapter capacity = PASS (tight headroom; serialized jobs required)
Selected backend/model/process model    = whisper.cpp / Large-v3-Turbo / per-VoiceJob child

D0 = CLOSED
```
