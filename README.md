# Psych-local

Psych-local est un environnement local de recherche conversationnelle. La Milestone A construit
uniquement la tranche verticale **CHAT · STREAM · STOP · CRASH · RECOVER · MEASURE · EVALUATE**.

> Milestone A ne contient aucune mémoire persistante, aucune transcription vocale et aucune
> fonctionnalité thérapeutique complète.

## État

Le bootstrap contient une API FastAPI liée à `127.0.0.1`, une migration SQLite minimale et une
application React/Vite. L’intégration `llama-server`, le streaming et les benchmarks sont développés
sur la branche `milestone-a`.

## Démarrage de développement

Prérequis : Python 3.11+, Node.js 20+ et un `llama-server` compatible OpenAI lancé localement.

```console
python -m venv .venv
.venv/Scripts/pip install -e ".[dev]"  # Windows
python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

Dans un second terminal :

```console
cd frontend
npm install
npm run dev
```

Copier `.env.example` vers `.env` pour personnaliser la configuration. Les instructions complètes
du moteur, des tests et des benchmarks seront maintenues ici au fur et à mesure de Milestone A.

