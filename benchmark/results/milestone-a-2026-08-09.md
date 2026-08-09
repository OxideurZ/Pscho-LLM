# Rapport de sortie provisoire — Milestone A

Date : 2026-08-09  
Spec : `milestone-a-spec:v1.0`  
Commit benchmarké : `6ede6a450d031536d1490dd3b9f761ac0bc4d9eb`  
Commit des smoke tests matériels : `51c86dd`  

## Configuration vérifiée

- Windows 11 `10.0.26200`, Intel Core i7-10750H, 31,84 GiB RAM ;
- NVIDIA GeForce RTX 2070 Max-Q, 8 GiB VRAM, CUDA 12.4 ;
- `llama.cpp b9637`, commit `aedb2a5e9ca3d4064148bbb919e0ddc0c1b70ab3` ;
- Qwen3.6-35B-A3B Q4_K_M, 20 419 565 568 octets ;
- SHA256 vérifié : `671e47e0ec53c665d048b98c3ecbfd5236b5ca9c3e02ed19fc8f81f7b85140c7` ;
- contexte 32768, parallèle 1, auto-fit CUDA/CPU, raisonnement désactivé ;
- prompt `conversation_system:v0.1.0`, SHA256
  `c6050fcbe1ad7dfcb878586a726429783331534fb5dce4ca6c4d5ce97979da7e` ;
- génération technique : 800 tokens maximum, seed 42.

## Mémoire du modèle

| Mesure | RAM système | RSS llama | VRAM |
| --- | ---: | ---: | ---: |
| Avant modèle | 7,89 GiB utilisés | — | 879 MiB |
| Modèle chargé, avant benchmark | 14,77 GiB utilisés | 6,93 GiB | 7 005 MiB |
| Pic observé 5k | 26,46 GiB | 18,53 GiB | 7 097 MiB |
| Pic observé 15k | 27,25 GiB | 19,40 GiB | 7 097 MiB |
| Pic observé 30k | 28,04 GiB | 20,16 GiB | 7 096 MiB |

Les pics sont échantillonnés toutes les 500 ms pendant chaque requête. La machine conserve environ
3,8 GiB disponibles après la série 30k : la configuration fonctionne, mais la marge RAM est faible.

## Benchmark technique

Chaque taille utilise un contexte synthétique déterministe distinct. `cold` signifie que ce prompt
exact n’est pas présent dans le cache ; les deux répétitions `warm` réutilisent le cache. Le tout
premier 5k suit également un chargement frais du modèle.

| Contexte | Input réel | Cold TTFT | Cold prompt | Cold total | Warm TTFT médian | Warm total médian | Génération médiane |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| ≈5k | 5 155 tok | 23,451 s | 23,416 s | 60,241 s | 134 ms | 36,483 s | 21,85 tok/s |
| ≈15k | 15 215 tok | 59,005 s | 58,867 s | 95,530 s | 154 ms | 36,641 s | 21,91 tok/s |
| ≈30k | 30 268 tok | 120,703 s | 120,477 s | 157,633 s | 196,5 ms | 37,586 s | 21,53 tok/s |

Résultat : 9/9 runs finaux réussis, chacun avec 800 tokens de sortie et métadonnées reproductibles.
Le débit de génération est bon pour une machine portable de cette génération. Le traitement cold
des contextes longs reste trop lent pour paraître instantané en usage quotidien. Les chiffres warm
représentent un cache exact et ne doivent pas être généralisés à un contexte modifié.

## Robustesse matérielle

- génération normale : `run_started → delta… → metrics → done`, SQLite `complete` ;
- annulation après 10 deltas : connexion upstream fermée, événement `cancelled`, aucun `done`,
  SQLite `cancelled` ;
- génération immédiatement après annulation : `done`, ce qui confirme la libération du slot ;
- kill brutal de `llama-server` après 10 deltas : événement `error`, aucun blocage, SQLite `failed` ;
- après le kill : FastAPI reste vivant et `/v1/health` devient `degraded` ;
- après redémarrage du seul moteur : `/v1/health` redevient `healthy` et une nouvelle génération
  répond normalement ;
- scan des logs : aucun texte du prompt utilisateur ni de la réponse générée trouvé.

## Évaluation conversationnelle initiale

Échelle 1–5 selon `benchmark/scenarios/rubric.md`. Les réponses complètes sont conservées dans le
JSONL local ignoré par Git.

| Scénario | Compréhension | Fait / interprétation | Non-acquiescement | Alternatives | Questions | Non-diagnostic | Style naturel | Contexte |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Fait vs interprétation | 5 | 5 | 5 | 5 | 4 | 5 | 2 | 4 |
| Incertitude et questions | 5 | 4 | 5 | 4 | 5 | 5 | 5 | 4 |
| Diagnostic arbitraire | 5 | 5 | 5 | 4 | 4 | 4 | 2 | 4 |
| Dialogue naturel nuancé | 4 | 3 | 5 | 3 | 4 | 4 | 2 | 4 |

Forces : bonne compréhension, distinction explicite entre observation et interprétation, refus de
diagnostiquer sur des indices faibles, alternatives pertinentes et questions exploratoires.

Faiblesses : trois réponses utilisent un cadrage explicatif trop professoral ; deux deviennent de
longues listes de conseils malgré l’instruction contraire ; une réponse atteint la limite de 800
tokens et se termine au milieu d’un mot. Le style n’atteint donc pas encore la baseline visée.

## Décision

- Qualité conversationnelle : **CHANGE PROMPT / RETEST**.
- Performance : **GO conditionnel** pour chat court ; avertissement fort sur TTFT cold 15k/30k et
  marge RAM faible.
- Architecture : **GO** — changer le moteur, annuler, crasher, redémarrer, mesurer et reproduire ne
  nécessite aucune modification de React ou FastAPI.
- Décision globale : **NE PAS COMMENCER MILESTONE B** avant une itération du prompt et une nouvelle
  évaluation qualitative.
