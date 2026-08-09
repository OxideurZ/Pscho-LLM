# Psych-local

## Windows Quick Start

Après avoir installé les prérequis documentés plus bas et placé le `llama-server` et le GGUF
validé à leurs emplacements configurés :

```powershell
git clone git@github.com:OxideurZ/Pscho-LLM.git
cd Pscho-LLM
.\setup.ps1
.\start.ps1
```

`start.ps1` vérifie la configuration, l’intégrité du modèle, les ports et une éventuelle instance
déjà saine. Il démarre uniquement `llama-server` et FastAPI, attend leurs health checks puis ouvre
`http://127.0.0.1:8000`. Aucun serveur Vite/Node n’est requis au runtime quotidien : FastAPI sert le
build frontend.

Fermer l’onglet du navigateur **n’arrête pas** le modèle. Pour libérer la VRAM après utilisation,
cliquer sur **Arrêter et libérer la VRAM** dans la barre latérale, ou exécuter `.\stop.ps1`. Ces deux
actions arrêtent le moteur local et FastAPI, sans supprimer les conversations SQLite. Il faut ensuite
relancer `.\start.ps1` pour reprendre. Il n’y a volontairement pas d’arrêt automatique sur inactivité,
afin de ne pas interrompre une longue génération ou dictée.

Psych-local est un environnement local de recherche conversationnelle. La Milestone B ajoute à la
tranche locale validée en A une histoire durable : **PERSIST · ORDER · RETRY · CHECKPOINT ·
RECONCILE · RESUME · MIGRATE · BACKUP**.

> Milestone B utilise uniquement des données artificielles ou non sensibles. Elle ne contient ni
> mémoire autobiographique globale, ni transcription vocale, ni fonctionnalité thérapeutique
> complète et n'autorise pas encore l'usage de données personnelles sensibles réelles.

Le navigateur envoie les messages à FastAPI, qui ajoute un prompt versionné puis délègue la
génération par HTTP à un processus `llama-server` indépendant. FastAPI ne charge jamais le GGUF.
Chaque tour réserve atomiquement en SQLite son USER, son `model_run` et son ASSISTANT avant le
premier appel réseau. Les réponses streamées sont checkpointées puis finalisées honnêtement.

```text
React/Vite ──POST SSE──> FastAPI ──LLMBackend──> llama-server ──> GGUF
                             │
                             ├── ConversationService + ContextBuilder
                             ├── SummaryService + RunRegistry
                             └── SQLite (WAL, migrations, backups)
```

## Prérequis

- Python 3.11 ou 3.12 ;
- Node.js 20+ et pnpm 10+ (npm fonctionne aussi) ;
- SQLite, inclus avec Python ;
- Windows x86_64 ou macOS Apple Silicon ;
- un build identifié de `llama.cpp` contenant `llama-server` ;
- environ 21 Go pour le GGUF de référence, plus la capacité RAM/VRAM requise au chargement.

Le modèle baseline canonique est
`ggml-org/Qwen3.6-35B-A3B-GGUF/Qwen3.6-35B-A3B-Q4_K_M.gguf`, avec le SHA256 de référence :

```text
671e47e0ec53c665d048b98c3ecbfd5236b5ca9c3e02ed19fc8f81f7b85140c7
```

Q4_K_M est un point de départ expérimental, pas un choix définitif.

## Installation

```console
python -m venv .venv
```

Windows :

```console
.venv\Scripts\python -m pip install -e ".[dev]"
cd frontend
pnpm install --frozen-lockfile
```

macOS :

```console
.venv/bin/python -m pip install -e '.[dev]'
cd frontend
pnpm install --frozen-lockfile
```

Copier `.env.example` vers `.env`. Tous les services sont liés à `127.0.0.1` par défaut ; ne pas
les exposer sur `0.0.0.0` pendant cette milestone.

À partir de Milestone B, la base et les artefacts runtime vivent hors du dépôt. Sous Windows, le
répertoire par défaut est `%LOCALAPPDATA%\PsychLocal\` et la base se trouve dans
`data\app.sqlite`. `DATA_DIRECTORY` et `DATABASE_PATH` permettent uniquement de rediriger ces
chemins, notamment vers un dossier temporaire pour les tests.

Les migrations SQL de `migrations/` sont appliquées dans l'ordre strict. Leur nom et leur SHA256
sont enregistrés dans `schema_migrations` ; modifier une migration déjà appliquée empêche le
backend de devenir ready. Toutes les connexions applicatives vérifient `foreign_keys=ON`, le mode
WAL et le `busy_timeout` configuré.

## Préparer llama.cpp et le modèle

La baseline runtime est épinglée sur `llama.cpp` **b9637**, commit
`aedb2a5e9ca3d4064148bbb919e0ddc0c1b70ab3`. Les noms et SHA256 des distributions Windows CUDA
12.4 et macOS arm64 officielles sont conservés dans `config/llama-cpp.lock`. Vérifier que cette
version fonctionne avec le modèle, puis recopier ces identifiants dans `LLAMA_CPP_VERSION` et
`LLAMA_CPP_BUILD`. Une valeur flottante comme `latest` ne peut pas produire un benchmark de
référence.

Placer le GGUF dans `models/` (ignoré par Git), puis vérifier son contenu en streaming depuis le
disque :

```console
python -m scripts.install_llama_cpp
python -m scripts.prepare_model
```

Ces téléchargements reprennent un fichier `.part` interrompu, valident la taille du modèle et
vérifient les SHA256 avant extraction ou utilisation. Pour vérifier un GGUF déjà présent :

```console
python -m scripts.verify_model models/Qwen3.6-35B-A3B-Q4_K_M.gguf --expected 671e47e0ec53c665d048b98c3ecbfd5236b5ca9c3e02ed19fc8f81f7b85140c7
```

Une divergence retourne un code non nul et bloque le runner de benchmark. Démarrer ensuite le
moteur manuellement, sur l’interface locale uniquement :

```console
llama-server -m models/Qwen3.6-35B-A3B-Q4_K_M.gguf --alias Qwen3.6-35B-A3B-Q4_K_M --host 127.0.0.1 --port 8080 -c 32768 --parallel 1 --jinja --reasoning off --metrics
```

Valider `/health`, `/v1/models` et `/v1/chat/completions` directement sur le moteur avant de tester
Psych-local. La gestion automatique de ce processus est volontairement hors périmètre A.

## Démarrer manuellement (développement)

Appliquer les migrations et lancer l’API :

```console
python -m scripts.migrate
python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000 --reload
```

Dans `frontend/` :

```console
pnpm run dev
```

Ouvrir `http://127.0.0.1:5173`. L'API A reste compatible et B ajoute les routes persistantes :

```text
GET  /v1/health
GET  /v1/models
POST /v1/chat
POST /v1/runs/{run_id}/cancel
POST /v1/runtime/offload

POST   /v1/conversations
GET    /v1/conversations
GET    /v1/conversations/{id}
PATCH  /v1/conversations/{id}
DELETE /v1/conversations/{id}
GET    /v1/conversations/{id}/messages
POST   /v1/conversations/{id}/turns
```

`/v1/health` reste disponible et passe à `degraded` si `llama-server` s’arrête. Son redémarrage est
détecté sans redémarrer FastAPI.

## Runtime C et diagnostic

La configuration suit : valeurs par défaut, `.env` local (ignoré par Git), puis variables
d’environnement. `setup.ps1` est idempotent ; il crée/répare l’environnement Python, installe les
dépendances, construit le frontend et applique les migrations. Il ne télécharge ni llama.cpp ni un
modèle. Un modèle absent ou dont le SHA ne correspond pas bloque le lancement avec l’emplacement,
le nom et le SHA attendus.

Les données runtime sont hors dépôt, par défaut sous `%LOCALAPPDATA%\PsychLocal\` : base SQLite,
backups et `runtime\instance.json`. Ce fichier est validé par PID, ports et health endpoint avant
d’être cru ; un fichier stale est supprimé automatiquement. Le launcher ne tue jamais de processus
tiers. Ses logs ne contiennent que des événements de lifecycle, jamais de contenu conversationnel.

Pour la validation logicielle sans benchmark matériel :

```powershell
.\scripts\check.ps1
```

## Persistance et recovery

Le frontend conserve le `conversation_id` courant dans le stockage local du navigateur et génère
un UUID `client_turn_id` par tour. Réenvoyer le même UUID retrouve le tour existant : il ne crée ni
nouveau USER, ni nouvel ASSISTANT, ni nouveau run. Une conversation refuse un autre tour actif avec
`409 CONVERSATION_BUSY`.

L'ordre logique dépend exclusivement de `sequence_no`. USER et ASSISTANT reçoivent deux positions
adjacentes dans une transaction `BEGIN IMMEDIATE`. Le streaming conserve un buffer RAM et écrit un
checkpoint au premier seuil atteint : une seconde ou 512 caractères par défaut. Il n'y a jamais une
écriture par token. Une annulation produit un ASSISTANT `interrupted`; une panne produit `failed` et
conserve le dernier contenu checkpointé.

Au démarrage, après les migrations et avant l'état DB `ready`, la reconciliation transforme tout
ancien run `starting`/`generating` et son ASSISTANT `streaming` en état `failed` avec
`PROCESS_INTERRUPTED`. Le texte checkpointé reste intact.

## Contexte et rolling summaries

`ContextBuilder` reconstruit uniquement la conversation demandée, en ordre de séquence, avec le
USER courant exactement une fois. Un contexte court utilise les messages bruts. Lorsque le budget
ne tient plus, il combine le prompt système, un rolling summary JSON validé et les messages récents.
Le compteur conservateur utilise les octets UTF-8 plus un coût de framing : c'est une borne haute
pour le tokenizer byte-fallback épinglé, pas une estimation présentée comme exacte.

Politique des messages partiels : ASSISTANT `interrupted` est inclus si son contenu existe;
ASSISTANT `failed` est inclus seulement avec un contenu non vide; `deleted`, `streaming` et tout
message `excluded_from_ai` sont exclus. Les messages bruts ne sont jamais supprimés par un résumé.
Chaque résumé possède un run `rolling_summary`, un prompt hashé, un parent et ses sources exactes.

## Backup et restore

`BackupService` utilise l'API de backup SQLite sur la base WAL active, jamais une copie brute du
fichier. Un restore écrit vers une destination absente et vérifie obligatoirement
`PRAGMA integrity_check` ainsi que `PRAGMA foreign_key_check` avant d'être accepté.

## Tests et qualité

```console
ruff check backend scripts benchmark
ruff format --check backend scripts benchmark
pytest -q
cd frontend
pnpm test
pnpm run build
```

Les tests utilisent principalement un faux backend : aucun modèle de 20,4 Go n’est nécessaire dans
la CI. La fermeture de B exige aussi des tests matériels avec le vrai `llama-server` couvrant la
génération, l'annulation upstream, le kill moteur, le kill/restart FastAPI, le contexte long, les
checkpoints et la reprise.

Avec les deux services lancés, les smoke tests matériels sont disponibles explicitement :

```console
python -m scripts.hardware_smoke cancel
python -m scripts.hardware_smoke crash --llama-pid 12345
```

Le second tue volontairement le PID fourni et doit être suivi d’un redémarrage manuel du moteur.

Pour la matrice B complète, laisser le runner lancer et arrêter ses propres processus locaux :

```console
python -m scripts.validate_milestone_b_hardware
```

Il utilise un data directory temporaire hors dépôt, vérifie le SHA du modèle, puis couvre en une
séquence contrôlée le chat normal, Stop, rolling summary réel, kill/restart FastAPI, reconciliation,
kill/restart moteur et reprise. Les résultats de référence sont dans
[`docs/reports/milestone-b-hardware.json`](docs/reports/milestone-b-hardware.json).

## Benchmarks

Les scénarios déterministes 5k/15k/30k, la méthode cold/warm et la grille qualitative sont décrits
dans [`benchmark/README.md`](benchmark/README.md). Exemple :

```console
python -m benchmark.scripts.run_technical --repetitions 3 --llama-pid 12345
```

Le runner refuse toute série dont le modèle n’a pas le SHA attendu ou dont les métadonnées
app/backend sont incomplètes. Les fichiers de résultats restent ignorés tant qu’un rapport contrôlé
n’est pas explicitement ajouté.

Le premier rapport matériel contrôlé est disponible dans
[`benchmark/results/milestone-a-2026-08-09.md`](benchmark/results/milestone-a-2026-08-09.md).

## Configuration principale

| Variable | Valeur par défaut | Rôle |
| --- | --- | --- |
| `PSYCH_LOCAL_HOST` / `PORT` | `127.0.0.1` / `8000` | écoute FastAPI |
| `LLAMA_SERVER_URL` | `http://127.0.0.1:8080` | moteur indépendant |
| `DATA_DIRECTORY` | chemin applicatif de l'OS | racine data/backups/models/runtime/logs |
| `DATABASE_PATH` | `<data>/data/app.sqlite` | conversations, messages, summaries et runs |
| `SQLITE_BUSY_TIMEOUT_MS` | `30000` | attente maximale d'un verrou SQLite |
| `SESSION_TIMEOUT_SECONDS` | `1800` | rotation temporelle des sessions |
| `STREAM_CHECKPOINT_SECONDS` / `CHARACTERS` | `1` / `512` | premier seuil de checkpoint |
| `CONTEXT_SAFETY_MARGIN_TOKENS` | `512` | marge réservée du contexte |
| `SUMMARY_BUDGET_TOKENS` | `4096` | budget de sortie du rolling summary |
| `RECENT_RAW_BUDGET_TOKENS` | `24000` | borne des messages bruts récents |
| `PROMPT_ID` / `VERSION` | `conversation_system` / `0.1.2` | prompt versionné et hashé |
| `MODEL_NAME` / `PATH` | baseline Qwen | identification locale |
| `MODEL_EXPECTED_SHA256` | hash canonique | garde-fou benchmark |
| `LLAMA_CPP_VERSION` / `BUILD` | `b9637` / commit épinglé | identité du backend |
| `LLAMA_CPP_REASONING` | `off` | mode de raisonnement de la baseline |
| `WHISPER_CPP_PATH` | binaire local `whisper-cli.exe` | STT local sélectionné pour la voix |
| `WHISPER_MODEL_PATH` / `EXPECTED_SHA256` | Whisper Large-v3-Turbo local | modèle STT vérifié au démarrage |
| `MAX_RECORDING_DURATION_SECONDS` | `900` | limite dure d’une dictée (15 minutes) |
| `DEFAULT_*` | voir `.env.example` | paramètres de génération |
| `CONTEXT_SIZE` | `32768` | fenêtre de contexte déclarée |

Le prompt complet, les messages et les réponses ne sont jamais inscrits dans `model_runs` ni dans
les logs applicatifs. Les contenus conversationnels sont stockés uniquement dans les tables dédiées;
les logs ne contiennent que les identifiants, hashes, états, durées et compteurs de tokens.

## Erreurs courantes

- `LLM_BACKEND_UNAVAILABLE` : vérifier le processus et `LLAMA_SERVER_URL` ; FastAPI peut rester
  lancé pendant son redémarrage.
- `LLM_BACKEND_TIMEOUT` : vérifier la charge, la taille du contexte et les délais du moteur.
- `LLM_BACKEND_PROTOCOL_ERROR` : vérifier que le build expose bien `/v1/chat/completions` en SSE.
- `CONVERSATION_BUSY` : attendre ou annuler le tour actif de cette conversation.
- `CONTEXT_BUDGET_EXCEEDED` : le USER courant ne tient pas dans le budget garanti.
- `PROCESS_INTERRUPTED` : le startup a réparé un tour non terminal laissé par un ancien processus.
- `MIGRATION_FAILED` / `SCHEMA_VERSION_INVALID` : ne pas supprimer la DB; restaurer les fichiers de
  migration attendus ou appliquer une nouvelle migration corrective.
- `SHA256 mismatch` : supprimer puis récupérer à nouveau le GGUF ; ne pas lancer de benchmark.
- santé `degraded` : lire séparément les états `database` et `llm` dans la réponse JSON.

## Limites de Milestone B

Il existe désormais des conversations, sessions, messages et summaries persistants, mais aucune
Memory v1 cross-conversation, aucun retrieval, embedding, vector store, microphone, Whisper, Tauri,
chiffrement final ou cloud. L’application n’est pas un dispositif médical et ne remplace pas un
professionnel ou un service d’urgence.
