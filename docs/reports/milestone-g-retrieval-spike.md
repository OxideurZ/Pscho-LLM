# Milestone G — spike retrieval/runtime G0

Date : 2026-08-13 (Europe/Zurich)

Branche : `milestone-g`

Commit applicatif de départ : `7ff3035`

Runner : `scripts/run_g0_retrieval_spike.py`

## Décision G0

**GO pour poursuivre G1 avec la baseline canonique.**

Les deux modèles 0.6B fonctionnent localement sur CPU pendant que le modèle de chat reste chargé
sur le GPU. FTS5 fonctionne dans la build SQLCipher réelle. Aucun troisième modèle n'est justifié.
Cette décision autorise l'implémentation des interfaces ; elle ne ferme pas Milestone G.

## Matériel et runtime retenus

| Élément | Valeur |
| --- | --- |
| CPU | Intel Core i7-10750H, 6 cœurs / 12 threads |
| RAM | 31,84 GiB |
| GPU | NVIDIA GeForce RTX 2070 Max-Q, 8192 MiB, pilote 581.83 |
| Chat | Qwen3.6-35B-A3B-Q4_K_M via llama.cpp b9637 |
| Python | 3.12.13 |
| Retrieval | PyTorch 2.6.0 CPU-only + Transformers 4.51.3 |
| Device | `cpu` obligatoire pour embedding/reranking |
| Process model choisi | runtime CPU lazy dans le backend, appels lourds déportés via `asyncio.to_thread`, possédé et déchargé par le lifecycle applicatif |

Le worker devra charger les modèles hors du chemin interactif et les conserver prêts. Il sera
arrêté par le même lifecycle que l'application afin de libérer la RAM. Le chat garde la priorité :
aucune couche retrieval n'est placée sur le GPU de référence.

## Modèles épinglés

| Usage | Modèle | Révision | SHA-256 `model.safetensors` |
| --- | --- | --- | --- |
| Dense | `Qwen/Qwen3-Embedding-0.6B` | `97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3` | `0437e45c94563b09e13cb7a64478fc406947a93cb34a7e05870fc8dcd48e23fd` |
| Rerank | `Qwen/Qwen3-Reranker-0.6B` | `e61197ed45024b0ed8a2d74b80b4d909f1255473` | `27cd75a405b9c1b46b59abfd88aaa209e6fed2a1972cde9b70e7659537c5e65b` |

Les snapshots sont stockés hors Git sous
`%LOCALAPPDATA%\PsychLocal\models\retrieval`. Le setup vérifie leur présence. Les deux hashes ont
été recalculés localement et correspondent aux manifests Hugging Face épinglés.

Configuration dense retenue : dimension 1024, vecteurs normalisés `float32`. Les poids sont
convertis en `float32` sur CPU pour la compatibilité du poste de référence. Les instructions seront
versionnées séparément par G1/G6.

Sources amont : [Qwen3 Embedding](https://github.com/QwenLM/Qwen3-Embedding) et cartes officielles
[Embedding 0.6B](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B) /
[Reranker 0.6B](https://huggingface.co/Qwen/Qwen3-Reranker-0.6B).

## SQLCipher + FTS5

| Contrôle | Résultat |
| --- | --- |
| SQLite | 3.51.1 |
| SQLCipher | 4.12.0 community |
| `ENABLE_FTS5` | oui |
| création/insertion/requête BM25 | PASS |
| ouverture avec SQLite plaintext | refusée, attendu |

La baseline lexicale est donc FTS5 dans la même base SQLCipher. Aucun index lexical ou vectoriel
plaintext n'est autorisé.

## Mesure réelle

Run exploitable unique après deux corrections techniques du runner (masque d'attention puis handle
temporaire Windows). Trois mesures warm, corpus synthétique sans donnée personnelle : une requête
française et huit documents.

| Étape | Cold | Warm p50 | Warm p95 | Taille |
| --- | ---: | ---: | ---: | ---: |
| Chargement embedder | 3038,96 ms | — | — | 0.6B |
| Embedding query + 8 documents | 1472,08 ms | 1370,43 ms | 1395,94 ms | batch 9 |
| Chargement reranker | 1387,97 ms | — | — | 0.6B |
| Reranking | 3417,79 ms | 3294,87 ms | 3366,00 ms | K=8 |

Le document sémantique attendu est classé premier par l'embedder (`0,576665`) et le reranker
(`0,188998`). La paraphrase proche est seconde dans les deux cas. Les six documents non pertinents
reçoivent des scores de reranking compris entre `0,000025` et `0,001863`.

Le run embedding mesure volontairement un batch mixte conservateur. L'indexation des documents se
fera en background ; le chemin interactif n'embarquera que l'embedding de la requête puis le
reranking borné.

## Ressources et coexistence

| Contrôle | Résultat |
| --- | --- |
| RSS avant modèle retrieval | 205,94 MiB |
| RSS après chargement embedder | 2583,22 MiB |
| Pic observé dans le processus séquentiel | 2735,83 MiB |
| VRAM avant / pendant / après | 7053 / 7053 / 7053 MiB |
| Sonde chat avant retrieval | PASS, 435,56 ms |
| Sonde chat pendant embedder | PASS, 651,01 ms |
| Sonde chat pendant reranker | PASS, 580,67 ms |
| Whisper Large-v3-Turbo | `healthy`, disponible |

PyTorch conserve une partie de son allocateur CPU jusqu'à la fin du processus ; le runner termine
ensuite et ne laisse aucun worker modèle derrière lui. Seuls le backend applicatif et
`llama-server` restent actifs.

## Paramètres gelés par G0

```text
RETRIEVAL_DEVICE=cpu
RETRIEVAL_EMBEDDING_DIMENSIONS=1024
RETRIEVAL_EMBEDDING_DTYPE=float32
RETRIEVAL_RERANK_TOP_K=8
RETRIEVAL_INTERACTIVE_TIMEOUT_MS=6000
```

Le timeout de 6 secondes encadre le chemin warm mesuré (borne conservatrice d'environ 4,67 s pour
le batch embedding mesuré + reranking K=8). Le chargement cold doit être effectué à la readiness,
jamais payé silencieusement sur un tour utilisateur. En cas de dépassement, le chat continue via
résultat partiel sûr ou sans retrieval.

`LEXICAL_TOP_K`, `DENSE_TOP_K`, `RRF_TOP_K`, le seuil 0..N et une éventuelle diversité MMR ne sont
pas inventés dans G0 : ils seront fixés sur le corpus development puis vérifiés sur held-out,
conformément à la spec.

## Risques à porter vers G1–G7

- Le reranker domine la latence : conserver strictement K=8 tant que le corpus n'en exige pas plus.
- Le worker dédié doit être isolé du chat et annulable ; son indisponibilité doit tomber sur le
  mode hybrid sans reranker, puis lexical/no retrieval.
- Les deux modèles devront être chargés avant que la health retrieval annonce `healthy` ; le chat
  peut rester `usable` pendant cette readiness.
- Toute sélection finale devra être revalidée contre SQLCipher ; les index restent dérivés.

## Gate G0

```text
FTS5_SQLCIPHER          = GO
BASELINE_MODELS         = GO
MODEL_INTEGRITY         = GO
CPU_GPU_STRATEGY        = GO
QWEN_COEXISTENCE        = GO
STT_COEXISTENCE         = GO
LATENCY_BUDGET_FROZEN   = GO
THIRD_MODEL_REQUIRED    = NO
```
