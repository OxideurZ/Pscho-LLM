# Psych-local

Psych-local est un environnement local de recherche conversationnelle. La Milestone A construit
une seule tranche verticale : **CHAT · STREAM · STOP · CRASH · RECOVER · MEASURE · EVALUATE**.

> Milestone A ne contient aucune mémoire persistante, aucune transcription vocale et aucune
> fonctionnalité thérapeutique complète.

Le navigateur envoie les messages à FastAPI, qui ajoute un prompt versionné puis délègue la
génération par HTTP à un processus `llama-server` indépendant. FastAPI ne charge jamais le GGUF.
Chaque tentative produit avant l’appel réseau un run technique SQLite sans conserver le texte de
la conversation.

```text
React/Vite ──POST SSE──> FastAPI ──LLMBackend──> llama-server ──> GGUF
                             │
                             └── RunRegistry + SQLite model_runs
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
python scripts/install_llama_cpp.py
python scripts/prepare_model.py
```

Ces téléchargements reprennent un fichier `.part` interrompu, valident la taille du modèle et
vérifient les SHA256 avant extraction ou utilisation. Pour vérifier un GGUF déjà présent :

```console
python scripts/verify_model.py models/Qwen3.6-35B-A3B-Q4_K_M.gguf --expected 671e47e0ec53c665d048b98c3ecbfd5236b5ca9c3e02ed19fc8f81f7b85140c7
```

Une divergence retourne un code non nul et bloque le runner de benchmark. Démarrer ensuite le
moteur manuellement, sur l’interface locale uniquement :

```console
llama-server -m models/Qwen3.6-35B-A3B-Q4_K_M.gguf --host 127.0.0.1 --port 8080 -c 32768
```

Valider `/health`, `/v1/models` et `/v1/chat/completions` directement sur le moteur avant de tester
Psych-local. La gestion automatique de ce processus est volontairement hors périmètre A.

## Démarrer Psych-local

Appliquer les migrations et lancer l’API :

```console
python scripts/migrate.py
python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000 --reload
```

Dans `frontend/` :

```console
pnpm run dev
```

Ouvrir `http://127.0.0.1:5173`. Les quatre seules routes publiques de A sont :

```text
GET  /v1/health
GET  /v1/models
POST /v1/chat
POST /v1/runs/{run_id}/cancel
```

`/v1/health` reste disponible et passe à `degraded` si `llama-server` s’arrête. Son redémarrage est
détecté sans redémarrer FastAPI.

## Tests et qualité

```console
ruff check backend scripts benchmark
ruff format --check backend scripts benchmark
pytest -q
cd frontend
pnpm test
pnpm run build
```

Les tests utilisent un faux backend : aucun modèle de 20,4 Go n’est nécessaire dans la CI. Les
tests matériels d’annulation réelle, de crash/recovery et de libération du slot doivent toutefois
être exécutés avec `llama-server` avant la décision de sortie.

## Benchmarks

Les scénarios déterministes 5k/15k/30k, la méthode cold/warm et la grille qualitative sont décrits
dans [`benchmark/README.md`](benchmark/README.md). Exemple :

```console
python -m benchmark.scripts.run_technical --repetitions 3 --llama-pid 12345
```

Le runner refuse toute série dont le modèle n’a pas le SHA attendu ou dont les métadonnées
app/backend sont incomplètes. Les fichiers de résultats restent ignorés tant qu’un rapport contrôlé
n’est pas explicitement ajouté.

## Configuration principale

| Variable | Valeur par défaut | Rôle |
| --- | --- | --- |
| `PSYCH_LOCAL_HOST` / `PORT` | `127.0.0.1` / `8000` | écoute FastAPI |
| `LLAMA_SERVER_URL` | `http://127.0.0.1:8080` | moteur indépendant |
| `DATABASE_PATH` | `psych-local.db` | runs techniques uniquement |
| `PROMPT_ID` / `VERSION` | `conversation_system` / `0.1.0` | prompt versionné et hashé |
| `MODEL_NAME` / `PATH` | baseline Qwen | identification locale |
| `MODEL_EXPECTED_SHA256` | hash canonique | garde-fou benchmark |
| `LLAMA_CPP_VERSION` / `BUILD` | `unknown` / `unverified` | identité du backend |
| `DEFAULT_*` | voir `.env.example` | paramètres de génération |
| `CONTEXT_SIZE` | `32768` | fenêtre de contexte déclarée |

Le prompt complet, les messages et les réponses ne sont jamais inscrits dans `model_runs` ni dans
les logs applicatifs. Seuls les identifiants, hashes, états, durées et compteurs de tokens le sont.

## Erreurs courantes

- `LLM_BACKEND_UNAVAILABLE` : vérifier le processus et `LLAMA_SERVER_URL` ; FastAPI peut rester
  lancé pendant son redémarrage.
- `LLM_BACKEND_TIMEOUT` : vérifier la charge, la taille du contexte et les délais du moteur.
- `LLM_BACKEND_PROTOCOL_ERROR` : vérifier que le build expose bien `/v1/chat/completions` en SSE.
- `SHA256 mismatch` : supprimer puis récupérer à nouveau le GGUF ; ne pas lancer de benchmark.
- santé `degraded` : lire séparément les états `database` et `llm` dans la réponse JSON.

## Limites de Milestone A

Il n’existe volontairement ni conversations/sessions persistantes, ni messages en base, ni mémoire,
retrieval, embeddings, microphone, Whisper, Tauri ou cloud. L’application n’est pas un dispositif
médical et ne remplace pas un professionnel ou un service d’urgence.
