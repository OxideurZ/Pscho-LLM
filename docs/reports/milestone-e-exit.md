# Milestone E — security exit report

**Spec:** `milestone-e-spec:v1.0`

**Report status:** `GO — HOME SECURITY BASELINE`

**Full milestone-e-spec:v1.0 status:** `PARTIAL — material hardening checks waived by owner`

**Validated branch:** `milestone-e`

## Delivered baseline

- Threat model: `psych-local-threat-model:v1.0`.
- SQLCipher (`sqlcipher3-wheels==0.5.7`) database adapter with WAL, foreign-key and integrity checks.
- Fail-closed plaintext-to-encrypted migration path with validation and atomic replacement.
- Windows user-scoped DPAPI `SecretStore`; database and local-session secret names are distinct.
- Encrypted, verified backups, conservative retention and periodic backup scheduling.
- HttpOnly/SameSite strict local session cookie, origin-gated bootstrap bound to a distinct DPAPI secret, and protected sensitive API routes.
- Conversation content is kept in React memory only; no application `localStorage`, `sessionStorage`, IndexedDB or CacheStorage use.
- Sensitive API responses receive `Cache-Control: no-store`.
- `SecurityReadiness` projection and Settings display expose hard-check failures instead of advertising a false green state.
- Secure mode is now enabled by default; compatibility tests explicitly opt into the insecure test mode.
- Secure startup detects a v7 SQLite header and performs the validated fail-closed plaintext-to-SQLCipher migration before opening the application database.

## Evidence collected

| Area | Evidence | Result |
|---|---|---|
| SQLCipher / migration / backup | encrypted DB, wrong-key, plaintext sqlite, migration and restore tests | PASS in elevated Windows test runs |
| Secure startup migration | `test_secure_startup_migrates_existing_plaintext_database` | PASS in elevated Windows test run |
| Frontend | 13 Vitest tests, TypeScript check and Vite build | PASS |
| Lint | `ruff check backend`, `ruff format --check backend`, `git diff --check` | PASS |
| Browser persistence | source audit; no Web Storage/IndexedDB/CacheStorage calls | PASS |
| API cache | middleware and route audit | PASS |
| Local API session | elevated integration test: no cookie `401`, missing token `403`, evil Origin `403`, DPAPI-bound fragment bootstrap then conversation access `200` | PASS |
| Offline / network policy | loopback and no-telemetry source tests | PASS (policy); full offline workstation run remains unverified |
| Backend regression | full elevated Windows harness after final bootstrap fix: `93 passed` | PASS |
| Disk encryption | `manage-bde -status C:` denied by current non-admin execution context | UNKNOWN / hard gate not proven |
| Data-directory ACL | default `%LOCALAPPDATA%\\PsychLocal` ACL is owner/System/Administrators only; repository test directory is intentionally not the production data directory | PASS for default path; repository path not a readiness target |
| Other Windows account | not executed from a second standard account | UNVERIFIED |
| Offline workflow | not executed as a material end-to-end Windows run in this turn | UNVERIFIED |

## Product decision

`HOME SECURITY BASELINE = GO`. The owner accepts operation without a verified BitLocker state, a second-account material test, or a full offline workstation run. The software baseline is enabled by default and validated: encrypted database and backups, user-bound DPAPI secrets, authenticated loopback API, restrictive default data-directory ACL, browser leak prevention, and fail-closed migration.

This decision does not redefine the canonical specification: `milestone-e-spec:v1.0` remains only partially proven because its material hardening gates were explicitly waived. The application continues to report unknown hard checks honestly instead of presenting them as PASS.

## Residual risks and exclusions

The threat model excludes same-user malware, administrator compromise, kernel compromise, live-memory extraction and physical attacks against an unlocked running device. Secure erasure from SSDs is not promised. These exclusions are documented in [`threat-model-v1.md`](../security/threat-model-v1.md).

## Closure

```text
MILESTONE E — HOME SECURITY BASELINE = CLOSED / GO
PERSONAL HOME USE = AUTHORIZED UNDER THREAT MODEL v1.0
FULL HARDENING VALIDATION = NOT CLAIMED
```

Optional future hardening remains available through `scripts/validate_milestone_e_security.py`; it is not required for the owner’s selected home-use baseline.
