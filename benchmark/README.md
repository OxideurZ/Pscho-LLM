# Benchmarks Milestone A

Les benchmarks passent exclusivement par l’API Psych-local. Ils n’envoient aucune donnée
personnelle et refusent de démarrer si le modèle, son SHA256, la version de l’application ou le
build `llama.cpp` ne sont pas identifiés.

## Contextes techniques

`contexts/manifest.json` définit trois contextes synthétiques déterministes d’environ 5k, 15k et
30k tokens. `context_factory.py` les génère à partir d’une seed stable, d’un ratio calibré sur le
tokenizer du GGUF épinglé, et termine chaque contexte par la même tâche. Le nombre exact de tokens
est celui rapporté par `llama-server` dans les métriques du run ; `target_context_tokens` est
uniquement la taille visée par le générateur.

Pour chaque taille, le premier passage est marqué `cold`, les suivants `warm`. Trois répétitions
sont effectuées par défaut. Avant la série, redémarrer `llama-server` afin que le premier run soit
réellement froid. Fournir son PID permet de capturer son RSS :

```console
python -m benchmark.scripts.run_technical --repetitions 3 --llama-pid 12345
```

Les résultats JSONL (ignorés par Git) incluent la configuration, le matériel, les identifiants du
modèle/backend, la RAM et les mesures TTFT/débit/durée. La VRAM doit être relevée avec l’outil du
constructeur et ajoutée au rapport de sortie lorsque l’accélérateur ne l’expose pas.

## Qualité conversationnelle

Utiliser les prompts synthétiques dans `scenarios/conversation.json`, conserver les sorties dans
un fichier de travail non versionné puis appliquer `scenarios/rubric.md`. Documenter la médiane,
les échecs critiques et la décision `GO`, `CHANGE MODEL/PROMPT` ou `NO-GO`.
