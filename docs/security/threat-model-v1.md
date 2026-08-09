# Psych-local threat model v1.0

Version: `psych-local-threat-model:v1.0`  
Milestone: E  
Status: design baseline; sensitive personal use remains **NOT AUTHORIZED** until the E exit gates pass.

## Security objective

Psych-local is a local-first application whose conversation history, summaries, voice transcripts,
temporary audio and backups must not be exposed by a trivial copy, forgotten artifact, permissive
local API or wrong configuration. Milestone E claims only the protections demonstrated by its
documented threat model and tests.

## Assets

- USER and ASSISTANT conversation content, including future memory and rolling summaries;
- SQLite database, WAL/SHM files and encrypted backups;
- voice transcripts and temporary raw audio;
- database-encryption key and local API/session secret;
- migration/backup metadata and security-readiness configuration.

## In-scope threats

| ID | Threat | Required boundary |
| --- | --- | --- |
| M1 | powered-off or stolen workstation | disk/application encryption and disk-encryption readiness |
| M2 | removed disk or offline inspection | copied DB and backup remain unreadable without authorized key material |
| M3 | `app.sqlite` copied alone | SQLCipher authentication fails closed |
| M4 | backup copied alone | backup is encrypted and independently verified |
| M5 | unrelated standard Windows account | DPAPI key boundary, ACLs and API session reject ordinary access |
| M6 | unauthorized localhost client | conversation routes require a local authenticated session |
| M7 | abandoned temporary file | startup/shutdown cleanup and temp-file audit |
| M8 | content in logs or exception strings | structured technical logs contain metadata only |
| M9 | secret accidentally committed | Git/current-tree and targeted secret scans |
| M10 | old plaintext database | explicit plaintext-to-encrypted migration; no silent fallback |
| M11 | failed security migration | original encrypted state remains recoverable and readiness fails closed |
| M12 | crash during DB write | SQLite/WAL integrity and reconciliation remain valid |
| M13 | crash during backup | incomplete backup is never considered valid |
| M14/M15 | missing, corrupt or wrong key | `DATABASE_LOCKED` / `SECURITY_NOT_READY`; no blank DB or plaintext downgrade |
| M16 | accidental network exposure | loopback-only bind, host/origin policy and no permissive CORS |
| M17 | browser durable plaintext copy | no conversation data in local/session storage, IndexedDB or caches |
| M18 | normal offline operation | unlock, history, voice, STT, Qwen and backup work without Internet |

## Explicit exclusions

This milestone does not protect against malware running as the same user, administrator malware,
kernel compromise, a debugger attached to an authorized process, arbitrary live-memory extraction,
keyloggers, malicious screen capture, a camera pointed at the display, or sophisticated physical
attacks against a running unlocked machine. These actors can observe data after the authorized user
has unlocked and opened it; defending against them requires OS/endpoint or hardware controls outside
this application boundary.

## Security assumptions to verify

- Windows user accounts and DPAPI user scope are enforced by the OS;
- the data volume's BitLocker/Device Encryption state is checked, never silently assumed;
- the application remains bound to `127.0.0.1` and does not add LAN sharing;
- ACL checks distinguish the current user from unrelated standard users;
- SQLCipher and the OS secret store are established dependencies, not home-grown cryptography.

## Defense in depth

```text
BitLocker / Device Encryption
        + SQLCipher application database
        + DPAPI user-bound key blobs
        + data-directory ACLs
        + authenticated loopback API
        + encrypted verified backups
        + browser/log/temp/Git leak prevention
```

No individual layer is treated as sufficient. A failed hard check must produce `NOT_READY`, not a
remembered or optimistic green status.

## Residual risks

Milestone E does not promise forensic SSD erasure of pre-E plaintext files, portable cross-machine
backup, password/biometric unlock, key rotation, cloud sync, multi-user profiles, macOS security
implementation, or protection from same-user/administrator malware. Pre-E data is assumed artificial
or non-sensitive; plaintext cleanup is therefore best-effort only.
