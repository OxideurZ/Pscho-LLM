# Milestone C — Exit report

Spec: `milestone-c-spec:v1.0`  
Application commit validated: `450845d`  
Frontend serving mode: FastAPI static build (Vite is development-only)

## Configuration

- OS: Windows; local validation on the configured CUDA llama.cpp runtime.
- Python: 3.12.13; frontend build uses Node/Vite supplied by the developer environment.
- llama.cpp: `b9637` / `aedb2a5e9ca3d4064148bbb919e0ddc0c1b70ab3`.
- Model: `Qwen3.6-35B-A3B-Q4_K_M`, SHA256
  `671e47e0ec53c665d048b98c3ecbfd5236b5ca9c3e02ed19fc8f81f7b85140c7`.
- SQLite schema: 7.
- Default runtime data: `%LOCALAPPDATA%\PsychLocal` (not the repository).

## Startup and lifecycle

User path on Windows:

```powershell
.\setup.ps1
.\start.ps1
```

`setup.ps1` detects Python, Node and pnpm; creates/reuses `.venv`; installs the backend and
frontend dependencies; builds the production bundle; creates runtime directories; and invokes the
backend-owned migration command. It is idempotent and does not download llama.cpp or models.

`start.ps1` delegates to the intentionally narrow Python launcher. It validates model SHA and
binary paths, checks local ports, validates `runtime/instance.json` by PID command identity and
health endpoints, starts only missing owned services, waits on health endpoints, then opens the
local FastAPI URL. A second start detects the healthy instance and does not create a second stack.

`stop.ps1` only terminates processes whose recorded PID, command identity and matching local health
endpoint prove ownership. Stale, malformed and PID-reused state is discarded; third-party ports are
reported without a kill attempt. Launcher lifecycle logs contain metadata only, never prompts,
responses or summaries.

Real validation on 2026-08-09:

- `start.ps1 -NoBrowser`: PASS after model SHA verification and health polling.
- `/v1/health`: `healthy`, database `ready`, schema 7, WAL/FK/migrations/reconciliation current,
  and llama.cpp `model_loaded=true`.
- second `start.ps1 -NoBrowser`: PASS; existing stack reused.
- a synthetic `Reponds uniquement par OK.` conversation turn: PASS,
  `run_started → delta(OK) → metrics → done`.
- `stop.ps1`: PASS; launcher status returned `NOT_READY`.

## UX and state recovery

- Sidebar uses the paginated conversation API; active and archived views are distinct.
- Create selects and focuses a new empty conversation; rename and archive use backend `PATCH`.
- Conversation URLs are deep-linkable (`/conversations/{id}`); reload/navigation reconstructs messages
  and state from the backend. React retains only a streaming cache.
- The chat state machine is explicit: `idle`, `submitting`, `preparing`, `generating`, `complete`,
  `cancelled`, `error`. Stop calls the real run cancellation endpoint.
- Navigation remains enabled while a different conversation generates; each conversation retains its
  own temporary projection and is reconciled when selected.
- Interrupted output remains visible and says `Interrompu`; failed output remains visible with the
  safe user message. Technical codes are not shown in ordinary UI.
- Backend connectivity is `connected`, `reconnecting` or `unavailable`, with a bounded retry
  sequence (immediate, 350 ms, 900 ms). Engine degradation is separate: history stays usable while
  generation is disabled.
- Settings exposes read-only model/version/context/data-location and health facts, never content.

## Privacy

The README reiterates that only artificial or non-sensitive data may be used before the security
milestone. Database, backups and lifecycle state default outside the repository. The launcher sends
child stdout/stderr to `DEVNULL` and writes only lifecycle metadata, preventing prompt, response or
summary content from entering runtime logs.

## Software validation

```text
pytest                         67 passed
ruff check backend scripts     passed
ruff format --check            passed
vitest                         10 passed
tsc --noEmit                   passed
vite build                     passed
git diff --check               passed
```

## Gates

```text
USABILITY         = GO
STARTUP           = GO
PROCESS_LIFECYCLE = GO
STATE_RECOVERY    = GO
RECOVERY_UX       = GO
REGRESSION        = GO

MILESTONE C = CLOSED
MILESTONE D = AUTHORIZED
```
