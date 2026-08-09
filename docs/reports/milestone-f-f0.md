# Milestone F0 — structured extraction and single-slot cache impact

Date: 2026-08-09  
Decision: **GO for F1/F2 with a 120-second idle delay and mandatory preemption**

## Scope

F0 answers one narrow question from `milestone-f-spec:v1.0`: does a memory-model call between two
interactive turns significantly disturb llama.cpp's single-slot KV reuse? It also checks that the
reference Qwen model can produce a bounded memory proposal and that background cancellation is
architecturally feasible. This is not a new general engine benchmark.

The two cache cases were run once each from a cold llama-server start. Failed attempts that did not
produce a valid structured extraction were rejected and are described below; they were not counted
as measurements. The server and backend were stopped and verified `NOT_READY` after the final run.

## Configuration

| Item | Value |
| --- | --- |
| Case A app commit | `de92b9e4c6789cc35d9c2f11213728ae28098bf4` |
| Case B app commit | `2abd9e7e3a40af159725913e68a69250a86a26c9` |
| Model | `Qwen3.6-35B-A3B-Q4_K_M` |
| Model SHA-256 | `671e47e0ec53c665d048b98c3ecbfd5236b5ca9c3e02ed19fc8f81f7b85140c7` |
| llama.cpp | `b9637` / `aedb2a5e9ca3d4064148bbb919e0ddc0c1b70ab3` |
| Slot policy | one slot, `id_slot=0`, prompt cache enabled |
| Conversation prompt | `conversation_system:v0.1.2` / `5a23b62014bf5fed816007d28391e3a6318e344bf28bb79984fb387036a161c6` |
| Successful memory prompt | `memory_extraction:v0.1.2` / `7d24079b3fb622e8c05fd5fe5eceee9592c21c707e474d9f5a7634706853954f` |

## Result

| Second interactive turn | Case A: chat → chat | Case B: chat → memory → chat |
| --- | ---: | ---: |
| Input tokens | 555 | 555 |
| Evaluated prompt tokens | 162 | 555 |
| Reused prompt tokens | 393 | 0 |
| Reuse ratio | 70.8% | 0% |
| Prompt evaluation / TTFT | 1,663.673 ms | 3,262.845 ms |
| Total generation latency | 6,680.289 ms | 7,522.251 ms |
| Output tokens | 96 | 84 |
| Finish reason | `length` | `stop` |

The memory call removed all observable KV reuse for the following interactive turn. Evaluated prompt
tokens increased by 393 (3.43× total evaluated tokens), and prompt evaluation time increased by
96.1%. Total generation latency is not directly comparable because the outputs have different
lengths and finish reasons, but it also increased in this sample.

The successful structured extraction took 18,035 ms and produced two bounded candidates:

- `goal / stated`;
- `preference / stated`.

Both candidates included the target USER message. Both quotes occurred exactly once in the source.
Qwen's proposed end offsets required deterministic canonicalization; the resulting offsets and exact
quotes passed validation.

## Structured-output findings

The prompt versions remain immutable after use:

- v0.1.0 produced a valid envelope but used an inclusive end offset (`-1`);
- v0.1.1 clarified exclusive offsets but once omitted the required root envelope;
- v0.1.2 fixed the envelope, while offset arithmetic still varied (`+1` in one rejected attempt).

Prompt iteration cannot make model arithmetic trustworthy. F therefore treats the exact quote as the
model proposal and resolves it deterministically:

1. an already exact span is accepted;
2. a quote with exactly one occurrence receives canonical local offsets;
3. a missing quote is `SOURCE_SPAN_MISMATCH`;
4. a repeated quote without an exact proposed span is `SOURCE_SPAN_AMBIGUOUS`;
5. an absent target message is `TARGET_NOT_INCLUDED`.

No fuzzy matching or semantic repair is allowed.

## F0 policy decision

`MEMORY_BACKGROUND_IDLE_SECONDS` is fixed initially at **120 seconds**. A memory-model call is long
enough (18 seconds in this smoke) and disruptive enough to the single-slot cache that it must not run
between normal conversational turns.

F1/F2 must enforce:

- one active memory-model job at a time;
- no memory work before 120 seconds of interactive inactivity;
- chat, STT and interactive summaries always take priority;
- setting the background job's cancellation event immediately on interactive activity;
- invalidating the job generation before waiting for upstream cancellation;
- discarding every late result from a preempted generation;
- `BACKGROUND_PREEMPTED` does not consume a normal retry attempt.

The existing `LLMBackend.generate_structured(..., cancel_event)` already cancels the HTTP request and
propagates `CancelledError`, so the mechanism is feasible. Real coordinator preemption and late-result
rejection remain required in F2 and in the final real-hardware validation.

## Gate

```text
STRUCTURED_EXTRACTION_SMOKE = GO
QWEN_OUTPUT_VALIDATION      = GO (strict parse + deterministic exact-quote grounding)
KV_CACHE_IMPACT             = SIGNIFICANT
IDLE_POLICY                 = 120 seconds
PREEMPTION_FEASIBILITY      = GO
F0                          = GO
```

This decision authorizes the durable jobs and background coordinator work. It does not authorize
memory retrieval or injection into chat.
