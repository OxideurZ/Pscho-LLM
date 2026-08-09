# Milestone E — security exit report

**Spec:** `milestone-e-spec:v1.0`  
**Report status:** `NO-GO — validation remains open`  
**Validated branch:** `milestone-e`

## Delivered baseline

- Threat model: `psych-local-threat-model:v1.0`.
- SQLCipher (`sqlcipher3-wheels==0.5.7`) database adapter with WAL, foreign-key and integrity checks.
- Fail-closed plaintext-to-encrypted migration path with validation and atomic replacement.
- Windows user-scoped DPAPI `SecretStore`; database and local-session secret names are distinct.
- Encrypted, verified backups, conservative retention and periodic backup scheduling.
- HttpOnly/SameSite strict local session cookie, origin-gated bootstrap and protected sensitive API routes.
- Conversation content is kept in React memory only; no application `localStorage`, `sessionStorage`, IndexedDB or CacheStorage use.
- Sensitive API responses receive `Cache-Control: no-store`.
- `SecurityReadiness` projection and Settings display expose hard-check failures instead of advertising a false green state.

## Evidence collected

| Area | Evidence | Result |
|---|---|---|
| SQLCipher / migration / backup | encrypted DB, wrong-key, plaintext sqlite, migration and restore tests | PASS in elevated Windows test runs |
| Backend regression | `pytest`: 85 passed; DPAPI tests require the approved elevated temp-directory harness | CONDITIONAL |
| Frontend | 13 Vitest tests, TypeScript check and Vite build | PASS |
| Lint | `ruff check backend`, `ruff format --check backend`, `git diff --check` | PASS |
| Browser persistence | source audit; no Web Storage/IndexedDB/CacheStorage calls | PASS |
| API cache | middleware and route audit | PASS |
| Disk encryption | `manage-bde -status C:` denied by current non-admin execution context | UNKNOWN / hard gate not proven |
| Data-directory ACL | current inspected directory includes `Authenticated Users` full access and `Users` read/execute | FAIL for that directory |
| Other Windows account | not executed from a second standard account | UNVERIFIED |
| Offline workflow | not executed as a material end-to-end Windows run in this turn | UNVERIFIED |

## Hard-gate decision

`SECURITY_READINESS = NO-GO` until BitLocker/device-encryption state, production data-directory ACLs, second-user boundary, unauthorized localhost request, and offline workflow are executed on the reference workstation. The implementation deliberately reports `NOT_READY` for disabled security mode or unknown hard checks; it does not silently create a plaintext database or reset inaccessible data.

## Residual risks and exclusions

The threat model excludes same-user malware, administrator compromise, kernel compromise, live-memory extraction and physical attacks against an unlocked running device. Secure erasure from SSDs is not promised. These exclusions are documented in [`threat-model-v1.md`](../security/threat-model-v1.md).

## Next authorized validation

Run the material Windows attack matrix from the reference account and a separate standard account, then update this report with the command output and change the decision to GO only if every hard gate passes. No Milestone F authorization or Milestone E closure is claimed by this report.
