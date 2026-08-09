# Milestone E0 — encrypted database and secret-store spike

Spec: `milestone-e-spec:v1.0`  
Status: **GO for implementation baseline**

## Question

Can the existing Python/SQLite stack keep its transaction, migration, foreign-key, WAL and backup
semantics while encrypting the database and protecting keys with the Windows user boundary?

## Tested baseline

- SQLCipher binding: `sqlcipher3-wheels==0.5.7` (Windows CPython 3.12 wheel, zlib/libpng license).
- SQLCipher engine reported: `3.51.1`.
- Key setup: SQLCipher `PRAGMA key` with a random key represented as a hex literal; no password-derived
  application key and no key in the test database.
- Secret store baseline: Windows DPAPI `CryptProtectData` / `CryptUnprotectData` with user scope.

## E0 invariants

| Invariant | Result |
| --- | --- |
| create encrypted DB | PASS |
| reopen with correct key | PASS; marker recovered |
| open with standard `sqlite3` | PASS gate; `DatabaseError` |
| open with wrong key | PASS gate; `DatabaseError` |
| WAL | PASS; `journal_mode=wal` |
| DPAPI protected blob | PASS; 258-byte blob contained no test plaintext |
| DPAPI round trip | PASS; artificial secret recovered under the same Windows user |
| migrations/repositories | implementation gate; preserve existing SQL/migration layer in E2 |
| backup/restore | implementation gate; must use encrypted destination and verify with SQLCipher in E6 |

The SQLCipher build's `cipher_integrity_check` pragma returned no row in this binding, so E2/E6 must
use the supported SQLCipher integrity/export behavior and treat an unavailable integrity result as a
failure, never as a pass. The first failed harness run left no project artifact; the corrected run
closed every connection and completed the table above.

## Decision

```text
ENCRYPTED_DB_BACKEND = SQLCipher via sqlcipher3-wheels==0.5.7
SECRET_STORE         = Windows user-scoped DPAPI protected blobs
MIGRATION_STRATEGY   = encrypted destination, validate counts/integrity, atomic swap, fail closed
KEY_SEPARATION       = distinct database-encryption and local-access secrets
```

Do not compare another database backend unless this baseline creates a demonstrated integration,
licensing or packaging blocker. The rest of Psych-local keeps its migrations, repositories,
transactions, foreign keys, WAL and backup service abstractions.
