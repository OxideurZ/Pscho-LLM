# Milestone G — rapport de sortie

Date : 2026-08-13  
Branche : `milestone-g`  
Commit applicatif évalué : `9e02ad805c0173339afe85cd5e6c61227bafaa7f`  
Décision : **GO**

## Configuration gelée

| Élément | Valeur |
|---|---|
| Schema SQLCipher | 15 |
| Chat | `Qwen3.6-35B-A3B-Q4_K_M`, SHA `671e47e0ec53c665d048b98c3ecbfd5236b5ca9c3e02ed19fc8f81f7b85140c7` |
| Prompt chat | `conversation_system:v0.1.2` |
| Embedder | `Qwen/Qwen3-Embedding-0.6B`, révision `97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3`, SHA `0437e45c94563b09e13cb7a64478fc406947a93cb34a7e05870fc8dcd48e23fd` |
| Embeddings | Transformers 4.51.3 / torch 2.6 CPU, 1024D float32 |
| Reranker | `Qwen/Qwen3-Reranker-0.6B`, révision `e61197ed45024b0ed8a2d74b80b4d909f1255473`, SHA `27cd75a405b9c1b46b59abfd88aaa209e6fed2a1972cde9b70e7659537c5e65b` |
| Profile | `retrieval_profile:v1.0`; K lexical/dense/RRF/rerank = 12/12/16/8; max items 4 |
| Sélection | seuil reranker `0.205352`; override multi-détails top-2 avec accord lexical+dense |
| Stockage | FTS5 `unicode61 remove_diacritics 2` + vecteurs float32 dans SQLCipher |
| Budget / timeout | 4096 tokens retrieval / 6000 ms |
| Runtime | CPU lazy dans FastAPI, calcul bloquant via `asyncio.to_thread`, fermeture explicite au shutdown |

Les modèles et leurs hashes sont vérifiés avant chargement avec `local_files_only=True`. Aucun
sidecar vectoriel plaintext n’existe. SQLCipher reste la source de stockage, tandis que les tables
FTS et embeddings sont entièrement dérivées de `messages` et `memory_items`.

## Index et durabilité

Le corpus réel isolé contient 14 documents éligibles : 10 memories actives/superseded et 4 messages
USER bruts. La memory disabled n’est jamais indexée. Le rebuild produit 14 lignes FTS et 14 vecteurs,
soit 57 344 octets de payload vectoriel float32 hors métadonnées. Stale docs : 0. Failed index jobs :
0 dans les fixtures. La durée de rebuild n’a pas été instrumentée séparément ; elle est incluse dans
la passe réelle G11 et ne participe pas au chemin interactif.

Les triggers durables couvrent création, édition, exclusion, soft-delete et changement de statut.
Le worker revalide systématiquement l’éligibilité depuis la source SQLCipher avant écriture. Les
tests de crash/reprise, invalidation et suppression/rebuild démontrent que la corruption d’un index
ne modifie jamais la source.

## Évaluation retrieval

Corpus : 22 cas longitudinaux, 10 `development` et 12 `heldout`. Les paramètres sont calibrés sur
`development`; le heldout ne sert pas au tuning. Résultats complets :
`docs/reports/milestone-g-retrieval-corpus.json`.

| Pipeline — heldout | Recall@K | MRR | nDCG@K | Precision injectée | Violations interdites |
|---|---:|---:|---:|---:|---:|
| Lexical | 0,750 | 0,688 | 0,704 | 0,135 | 3 |
| Dense | 1,000 | 0,875 | 0,908 | 0,054 | 4 |
| RRF | 1,000 | 0,810 | 0,854 | 0,054 | 4 |
| RRF + reranker | 1,000 | 1,000 | 1,000 | 0,225 | 2 |
| Pipeline final | **1,000** | **1,000** | **1,000** | **1,000** | **0** |

La valeur dense est démontrée par les paraphrases absentes du lexical. RRF conserve la couverture
complémentaire dense/lexicale. Le reranker stabilise l’ordre à MRR/nDCG 1,0 ; la sélection calibrée
retire ensuite les candidats faibles, disabled/deleted, stale, ambigus ou dupliqués. Les 7 cas
attendant zéro contexte injectent exactement zéro. Distribution finale : 11 memory-only, 3 raw-only,
1 mixte et 7 tours sans contexte. La memory structurée gagne sur sa copie brute, tandis que le cas
multi-détails conserve un raw complémentaire.

## Latence et coexistence

| Étape réelle CPU | p50 | p95 |
|---|---:|---:|
| FTS5 | 15,5 ms | 16,0 ms |
| Embedding query | 313,0 ms | 329,0 ms |
| Scan vectoriel SQLCipher | 16,0 ms | 31,0 ms |
| Reranker K≤8 | 4 390,5 ms | 5 140,0 ms |
| Retrieval total | **4 726,0 ms** | **5 484,0 ms** |

Le p95 reste sous la borne G0 de 6000 ms. Les tours manifestement non personnels court-circuitent
avant reranking. Le spike G0 a mesuré le chargement cold embedding à 3039 ms, batch embedding cold à
1472 ms, reranker cold K8 à 3418 ms, warm reranker p95 à 3366 ms. Qwen est resté sain pendant la
coexistence ; le retrieval est CPU-only et n’a pas augmenté la VRAM chat observée (~7053 MiB).

## Vrai Qwen end-to-end

`docs/reports/milestone-g-qwen-e2e.json` : **8/8** cas passent via sélection G11 →
`RetrievalContextAssembler` → `ContextBuilder` → vrai Qwen. Couverture : préférence, ancien événement,
raw fallback, croyance correctement attribuée à l’utilisateur, contradiction courante, fait historique
superseded, requête générale sans retrieval et injection historique traitée comme donnée. Deux cas
comparent explicitement Qwen avec/sans retrieval. Aucune réponse n’est tronquée sur les sept cas
personnels ; le seul `hit_max_tokens` concerne l’explication générale sans retrieval et n’affecte
aucun gate retrieval.

## Fallbacks, annulation et transparence

- reranker down : seuls les candidats avec accord lexical+dense subsistent ;
- embedder/hybrid down : fallback lexical conservateur ;
- index/retrieval down ou timeout : résultat vide valide, chat utilisable ;
- Stop pendant retrieval : tâche annulée, résultat tardif écarté, génération LLM non démarrée ;
- contexte : USER courant une fois, recent context et rolling summary prioritaires, retrieval sacrifié
  en premier sous pression ;
- audit : `retrieval_runs` et `retrieval_run_items` conservent hashes, ranks, scores et décision
  d’injection, jamais la requête ou le contenu ; endpoint authentifié `/v1/runs/{id}/retrieval`.

## Sécurité, offline et régressions

- migration réelle de la DB utilisateur : schema 12 → 15, runtime `READY` ;
- SQLCipher/DPAPI : 2/2 tests DPAPI et tests de lecture plaintext refusée ;
- aucun vector store externe, aucun appel réseau modèle (`local_files_only=True`) ;
- API locale authentifiée, réponses sensibles `no-store`, requête non bootstrapée refusée dans le
  navigateur ;
- scan frontend : aucune utilisation de localStorage/sessionStorage/IndexedDB/Cache Storage ;
- scan logs : aucun marqueur ou contenu retrieval ;
- backup/restore, offline, text, voix, Memory F et offload restent verts.

Validation finale : 163 tests backend passés + 1 skip plateforme, 2/2 DPAPI, 13/13 Vitest,
`ruff check`, `ruff format --check`, `tsc --noEmit`, build Vite et diff check passent.

## Matrice T1–T50

| Tests | Preuve | Résultat |
|---|---|---|
| T1–T7 | indexer, triggers, exclusions, invalidation, rebuild et crash/reprise | PASS |
| T8–T15 | exact/rare/date, dense, RRF déterministe, reranker et fallbacks | PASS |
| T16–T23 | zéro résultat, greeting, raw fallback, source priority, historique/current state | PASS |
| T24–T31 | contradiction USER, labels épistémiques, identité, déduplication, budget, injection | PASS |
| T32–T36 | Stop, résultat tardif, timeout et audit sans contenu dupliqué | PASS |
| T37–T42 | auth, browser/log scans, SQLCipher, backup et offline | PASS |
| T43–T47 | voix, Memory F, absence de leak F, texte et workflow voix | PASS |
| T48–T50 | latence workstation, coexistence et corpus vrai Qwen 8/8 | PASS |

## Hard gates

`INDEX_DURABILITY`, `LEXICAL_RETRIEVAL`, `DENSE_RETRIEVAL`, `HYBRID_RETRIEVAL`, `RERANKING`,
`RAW_HISTORY_FALLBACK`, `SOURCE_PRIORITY`, `CURRENT_USER_PRIORITY`, `CONTEXT_SAFETY`,
`NO_RESULT_PRECISION`, `RETRIEVAL_LATENCY`, `TRANSPARENCY`, `SECURITY_REGRESSION` et `REGRESSION`
sont tous **GO**.

Milestone G est close. Le prochain checkpoint produit est l’usage réel du MVP ; aucune Milestone H
n’est autorisée par cette clôture.
