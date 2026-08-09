# Rapport de sortie — Milestone B

Date : 2026-08-09  
Spec canonique : `milestone-b-spec:v1.0`  
Branche : `milestone-b`  
Commit applicatif validé : `d4446b5`

## Décision

```text
PERSISTENCE = GO
RECOVERY     = GO
MIGRATIONS   = GO
CONTEXT      = GO
SUMMARY      = GO
BACKUP       = GO

MILESTONE B = CLOSED
MILESTONE C = AUTHORIZED
```

Les conversations survivent aux retries, redémarrages et crashs sans doublon, réordonnancement,
contamination inter-conversations ni faux état `streaming`. SQLite reste la source de vérité et le
startup ramène automatiquement tout état interrompu vers une représentation terminale honnête.

## Configuration

- Windows 11 `10.0.26200`, Intel Core i7-10750H, 31,84 Gio RAM ;
- NVIDIA GeForce RTX 2070 Max-Q, 8 Gio VRAM, CUDA 12.4 ;
- llama.cpp b9637, commit `aedb2a5e9ca3d4064148bbb919e0ddc0c1b70ab3` ;
- Qwen3.6-35B-A3B Q4_K_M, SHA256
  `671e47e0ec53c665d048b98c3ecbfd5236b5ca9c3e02ed19fc8f81f7b85140c7` ;
- prompt conversation `conversation_system:v0.1.2`, SHA256
  `5a23b62014bf5fed816007d28391e3a6318e344bf28bb79984fb387036a161c6` ;
- prompt summary final `rolling_summary:v0.1.1`, SHA256
  `1b6978e4591fced7141f884dec75c0f3b6f1f7df8c965f324bbb98b24d1fe324` ;
- schema SQLite : version 7 ;
- runtime matériel : contexte moteur 32768, contexte applicatif volontairement réduit à 8192 pour
  déclencher une condensation réelle, seed 42, données artificielles uniquement.

Le smoke matériel a été exécuté sur le tree backend non commité, puis ce contenu backend identique
a été figé dans `ed32891`. Les commits applicatifs ultérieurs ne changent pas les résultats
matériels : `cbfaeb6` propage Stop à la requête structurée de summary et `d4446b5` expose le statut
informatif du retry idempotent; tous deux possèdent leurs tests dédiés.

Artifact détaillé : [milestone-b-hardware.json](milestone-b-hardware.json).

## Persistence

La DB par défaut vit hors dépôt : `%LOCALAPPDATA%\PsychLocal\data\app.sqlite` sous Windows et
`~/Library/Application Support/PsychLocal/data/app.sqlite` sur macOS. Le chemin est configurable
pour les tests.

La création d'un tour utilise une transaction `BEGIN IMMEDIATE` :

```text
lookup client_turn_id
→ resolve session
→ USER complete
→ model_run chat starting
→ ASSISTANT streaming
→ reserve two adjacent sequence numbers
→ update session/conversation
→ COMMIT
→ build context
→ call LLMBackend
```

Les injections d'échec avant et après la création du run prouvent le résultat `all or nothing`.
Deux requêtes concurrentes avec le même `client_turn_id` produisent exactement un USER, un
ASSISTANT et un run. Un ID différent pendant un tour actif retourne `409 CONVERSATION_BUSY`.

Résultats logiciels :

- 20 tours successifs : 40 messages, séquences exactes `1..40`, aucun zombie ;
- restart complet de l'application puis tour 21 : 42 messages, séquences `1..42` ;
- le payload du tour 21 contient 21 USER et 20 ASSISTANT, avec le USER courant une seule fois ;
- mêmes timestamps ou frontières de session n'influencent jamais l'ordre ;
- pagination par `after_sequence_no`, CRUD, archive et soft-delete testés.

Résultats matériels : 4 conversations, 11 tours artificiels persistés, 22 messages et 12 runs dans
la DB finale. Huit tours ont réellement utilisé Qwen; trois couples ont été injectés via le
repository pour constituer le fixture long sans benchmark modèle superflu.

## Crash safety et recovery

### Cancellation

Le run réel `run_669e57f17da94e9f9b6f57bc691d921b` a été annulé pendant le stream :

- upstream fermé et slot libéré ;
- ASSISTANT `interrupted`, 66 caractères conservés ;
- run `cancelled`, aucun `done` ;
- génération suivante `complete`.

Stop est également propagé pendant un rolling summary : la requête structurée est annulée, le run
interne devient `cancelled`, le tour parent devient `interrupted` et aucun run interne zombie ne
reste.

### Kill FastAPI

FastAPI a été tué brutalement après au moins 96 caractères émis. Le dernier checkpoint contenait
88 caractères. Le harnais a comparé les deux valeurs avant de continuer et a vérifié une perte
comprise entre 8 et 63 caractères, donc inférieure au seuil configuré de 64 caractères. Le nombre
exact n'a pas été conservé après un timeout ultérieur du harnais; la borne et le checkpoint durable
sont conservés dans l'artifact.

Au restart, avant l'état DB `ready` :

```text
ASSISTANT streaming → failed (88 caractères conservés)
model_run generating → failed / PROCESS_INTERRUPTED
```

La conversation a ensuite accepté un nouveau tour `complete`. Les tests logiciels couvrent aussi
les orphelins `starting`, `generating`, `streaming` et confirment zéro état non terminal ancien.

### Kill llama-server

Le kill moteur pendant génération a produit un ASSISTANT `failed` avec 65 caractères et un run
`failed`; FastAPI est resté vivant. Avec le même processus FastAPI :

```text
engine absent    → /health degraded
engine restarted → /health healthy
new turn         → run_started → delta → metrics → done ("OK")
```

## Sessions

Le timeout est configurable, 1800 secondes par défaut. Un tour dans la fenêtre réutilise la session;
après expiration, l'ancienne reçoit `ended_at` et une nouvelle est créée. La session est uniquement
temporelle : ContextBuilder produit le même historique conversationnel pertinent à travers cette
frontière.

## Context

Budget par défaut :

```text
max_context_tokens       = 32768
reserved_output_tokens   = max_tokens du run (800 par défaut)
safety_margin_tokens     = 512
summary_budget_tokens    = 4096
recent_raw_budget_tokens = 24000
```

Invariant vérifié avant tout appel modèle :

```text
input upper bound + reserved output + safety margin <= model context
```

Le compteur est volontairement conservateur : octets UTF-8 plus 32 unités de framing par message.
Pour le tokenizer byte-fallback épinglé, cette valeur constitue une borne haute; aucune fausse
précision de tokenisation n'est revendiquée.

- contexte court : SYSTEM + historique brut, aucun summary ;
- contexte long matériel : environ 12,2 k caractères bruts, condensation déclenchée sous budget
  applicatif 8192, summary réel puis chat `complete` avec 666 tokens d'entrée rapportés ;
- USER courant présent exactement une fois ;
- test CARIBOU : le payload direct de Conversation B ne contient aucune donnée de Conversation A ;
- dépassement impossible à garantir : `CONTEXT_BUDGET_EXCEEDED` avant appel modèle.

Politique des messages : USER `complete` inclus; ASSISTANT `complete` et `interrupted` inclus;
ASSISTANT `failed` inclus uniquement si son contenu partiel est non vide; `streaming`, `deleted` et
`excluded_from_ai` exclus. Cette politique est testée explicitement.

## Rolling summaries

Le schema JSON strict `1.0` possède sept listes :

```text
conversation_progression
user_stated_facts
user_interpretations
assistant_proposals
topics_discussed
open_questions
decisions_or_actions
```

Chaque tentative est un `model_run(run_kind=rolling_summary)` avec prompt/version/SHA. Chaque
summary persiste `parent_summary_id` et ses `summary_sources`; une requête récursive reconstruit la
lignée. Les messages bruts ne sont jamais supprimés.

Le premier essai matériel avec v0.1.0 a produit deux JSON structurés rejetés par le garde-fou
épistémique. v0.1.0 est resté intact. v0.1.1 demande de reprendre les mots-clés USER et la validation
rejette tout terme factuel provenant seulement d'un ASSISTANT ou insuffisamment supporté par les
sources USER.

Le run réel v0.1.1 a persisté un JSON de 889 octets avec six sources :

- `user_stated_facts` : `TEST 0/1/2 CARIBOU` ;
- `assistant_proposals` : `PROPOSITION 0/1/2 PROPOSITION` ;
- aucune proposition assistant transformée en fait utilisateur.

Les tests négatifs rejettent explicitement « X se désintéresse » lorsque seul l'assistant l'a
proposé après le fait USER « X n'a pas répondu ».

## SQLite et migrations

- migrations strictement contiguës 1 à 7 ;
- `schema_migrations(version, name, checksum, applied_at)` ;
- checksums et noms de toutes les migrations appliquées relus au startup ;
- migration inconnue, trou de version ou checksum modifié : startup refusé ;
- script de migration et enregistrement de version dans une transaction explicite ;
- DB A représentative migrée sans perdre les anciens `model_runs`, attribués à `run_kind=chat` ;
- chaque connexion vérifie `foreign_keys=ON`, `journal_mode=WAL`, `busy_timeout=30000` ;
- une lecture reste disponible pendant une transaction de checkpoint non commitée.

Le health check distingue reachability, version courante, pragmas, migration, reconciliation et
état LLM. Le backend ne devient DB `ready` qu'après migrations et reconciliation.

## Backup

`BackupService` utilise `sqlite3.Connection.backup` sur la DB WAL active. Il ne copie jamais
directement `app.sqlite`. Le test écrit une conversation, crée un snapshot pendant l'activité,
modifie l'original, restaure vers une destination distincte et retrouve uniquement l'état du
snapshot.

Les contrôles du snapshot et du restore donnent :

```text
PRAGMA integrity_check  = ok
PRAGMA foreign_key_check = 0 violation
```

Les destinations existantes et snapshots corrompus sont refusés avec des erreurs dédiées.

## Privacy

- runtime et backups hors dépôt par défaut ;
- données matérielles exclusivement artificielles ;
- aucun USER, ASSISTANT, summary, system prompt ou payload SQL dans les logs normaux ;
- scans `ULTRA_SECRET_CARIBOU_7391` et `ULTRA_SECRET_SUMMARY_CARIBOU_7391` réussis ;
- erreurs API réduites à un code stable et un booléen `retryable` ;
- UUID invalide : `INVALID_CLIENT_TURN_ID`, sans refléter le contenu ;
- FastAPI et llama-server liés à `127.0.0.1`.

## Matrice T1–T30

| Tests | Preuve |
|---|---|
| T1–T3 | fresh DB v7, migration DB A, checksum/filename/échec refusés |
| T4–T9 | tour normal, sequence, retry, concurrence busy, rollback tôt et tard |
| T10–T14 | checkpoint borné, cancel, kills réels, reconciliation, zéro orphelin |
| T15–T17 | USER une fois, CARIBOU isolé, reuse/rotation session |
| T18–T22 | chemins court/long, lineage, safety épistémique, raw conservé |
| T23 | lecture WAL pendant écriture de checkpoint non commitée |
| T24–T25 | backup DB active, restore, integrity et foreign keys |
| T26–T27 | data hors repo, scans logs messages et summaries |
| T28–T29 | 20 tours, restart, reload, tour 21 avec historique complet |
| T30 | flux A, cancel, backend absent/crash/recovery, logs et prompts toujours verts |

## Validation logicielle finale

```text
pytest backend/tests: 61 passed
ruff check backend benchmark scripts: passed
ruff format --check backend benchmark scripts: passed
vitest: 5 passed
TypeScript --noEmit: passed
Vite production build: passed
git diff --check: passed
model SHA256 verification: passed
```

L'unique avertissement pytest est la dépréciation transitive de `fastapi.testclient` vers `httpx2`;
il ne concerne aucun invariant B.

## Réponses aux gates

### 1. DATA — GO

Les données survivent aux retries, restarts et crashs sans mentir sur les états.

### 2. CONTEXT — GO

Les conversations courtes et longues sont reconstruites localement, sous budget et sans
contamination.

### 3. SUMMARY — GO

Le JSON est validé, sourcé et lignable; les propositions assistant restent distinctes des faits
USER.

### 4. RECOVERY — GO

Le pire crash testé laisse une DB automatiquement réconciliable avec le dernier checkpoint.

### 5. MIGRATIONS — GO

Le schema évolue désormais sans destruction de données et refuse toute dérive silencieuse.

### 6. BACKUP — GO

Un snapshot WAL cohérent peut être produit, vérifié et restauré séparément.

## Conclusion

Aucun bug connu ne peut actuellement produire un doublon, un réordonnancement, une contamination
inter-conversations, un faux `streaming` permanent ou une corruption DB silencieuse. Le frontend
reste volontairement minimal et l'usage sensible reste interdit; ces limites n'affectent aucun hard
gate B.

Milestone B rend l'histoire durable, ordonnée, idempotente, checkpointée, réconciliable et
restaurable. Les hard gates vers C sont satisfaits.
