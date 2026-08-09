# Addendum de sortie — Milestone A10.1

Date : 2026-08-09  
Spec canonique : `milestone-a10.1-spec:v1.0`  
Branche : `milestone-a`  
Commit applicatif des runs finaux : `689cc01b1e6e01493a2108948a850953a22d126a`

## Décision

```text
QUALITY = GO
ARCHITECTURE = GO
PERFORMANCE = ACCEPTED

MILESTONE A = CLOSED
MILESTONE B = AUTHORIZED
```

Le modèle de référence répond de manière suffisamment naturelle et nuancée pour poursuivre le
projet. La latence d'un grand prefill reste perceptible, mais elle est honnêtement exposée par
l'interface, interruptible, et n'est pas un hard gate de cette milestone.

## Configuration de référence

- Windows 11 `10.0.26200`, Intel Core i7-10750H, 31,84 Gio RAM ;
- NVIDIA GeForce RTX 2070 Max-Q, 8 Gio VRAM, CUDA 12.4 ;
- llama.cpp b9637, commit `aedb2a5e9ca3d4064148bbb919e0ddc0c1b70ab3` ;
- Qwen3.6-35B-A3B Q4_K_M, 20 419 565 568 octets ;
- modèle SHA256 : `671e47e0ec53c665d048b98c3ecbfd5236b5ca9c3e02ed19fc8f81f7b85140c7` ;
- contexte 32 768, parallel 1, reasoning off, seed 42.

## Prompts

| Version | SHA256 | Résultat |
|---|---|---|
| v0.1.0 | `c6050fcbe1ad7dfcb878586a726429783331534fb5dce4ca6c4d5ce97979da7e` | baseline intacte |
| v0.1.1 | `36ee7497ca05fe4b95f7c2b44620a14b4b9fea91863cb272bdf2dec181ce209c` | style amélioré, médiane NS 3,5 : rejeté |
| v0.1.2 | `5a23b62014bf5fed816007d28391e3a6318e344bf28bb79984fb387036a161c6` | hard gates satisfaits |

v0.1.1 a été conservé et non réécrit. La petite itération v0.1.2, autorisée par la spec après un
échec stylistique léger, renforce uniquement la brièveté, le langage ordinaire, la forme sans
rubriques et la reconnaissance douce d'une tension. Les règles fait/interprétation,
non-acquiescement, incertitude et non-diagnostic sont conservées.

## Benchmark qualitatif final

Quatorze scénarios ont été exécutés avec `max_tokens=1200` et seed 42. Dix scénarios
représentatifs ont été comparés à v0.1.0, ordre A/B randomisé indépendamment avec seed 10402.
Le livret a été figé sous SHA256
`948aca11cfd4b4c6fc096a426b9bcca5fa846ac83a4a332c746b35e43dd12084`.
La table de scores a été commitée avant consultation de la clé séparée.

Artifacts auditables :

- [livret aveugle v0.1.2](blind-a102.md) ;
- [scores complets à 13 dimensions](blind-a102-scores.md) ;
- commit de scores pré-révélation : `689cc01` ;
- première itération conservée : [livret v0.1.1](blind-a101.md) et
  [scores v0.1.1](blind-a101-scores.md), commit pré-révélation `f258a8a`.

### Scores décodés

| Indicateur | v0.1.0 | v0.1.2 | Gate |
|---|---:|---:|---|
| médiane `natural_style` | 1,5 | **4,0** | PASS (`>=4`) |
| médiane `conversational_pull` | 3,0 | **4,0** | diagnostic positif |
| médiane `appropriate_length` | 1,5 | **4,0** | diagnostic positif |
| structure superflue, médiane | 1,0 | **5,0** | PASS, non systématique |

Les trois comparaisons de garde sur leurs scénarios dédiés ne montrent aucune régression :

| Garde | v0.1.0 | v0.1.2 | Décision |
|---|---:|---:|---|
| fait vs interprétation | 5 | 5 | stable |
| non-acquiescement | 4 | 5 | amélioration |
| non-diagnostic | 3 | 4 | amélioration |

Des imperfections diagnostiques restent visibles sans invalider les gates : une explication par
la dopamine est trop affirmative, « épuisement du système nerveux » est trop catégorique, et la
vitesse d'une réponse future est parfois surinterprétée. Elles sont consignées dans les notes de
score et ne justifient ni nouvelle itération ni changement de modèle dans A10.1.

### Longueur et troncature

Sur les 14 réponses v0.1.2 :

- minimum 55 tokens ; médiane 156 ; moyenne 158,1 ; maximum 257 ;
- `hit_max_tokens=true` : **0/14** ;
- terminaison naturelle : **14/14** ;
- titres, listes ou cadres non demandés : **0/14**.

Le plafond de 1200 n'a donc masqué aucun problème de longueur et n'a pas été atteint.

## Conversations incrémentales

Configuration cache explicitement enregistrée sur chaque ligne :

```json
{
  "cache_prompt": true,
  "cache_reuse": 0,
  "cache_reuse_description": "llama-server longest-prefix reuse; KV-shift reuse disabled",
  "parallel": 1,
  "number_of_slots": 1,
  "slot_prompt_similarity": 0.10,
  "context_size": 32768
}
```

`evaluated_prompt_tokens` provient directement de `timings.prompt_n`. `reused_prompt_tokens` est
calculé uniquement comme `usage.prompt_tokens - timings.prompt_n`; aucune approximation par TTFT
n'est utilisée. Le build a exposé ces deux valeurs de manière fiable sur tous les runs.

### INCREMENTAL-NATURAL

| Cible | Entrée | Évalués | Réutilisés | TTFT ms | Gén. ms | tok/s | RAM pic Gio | VRAM pic Mio |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 5k | 5 345 | 4 952 | 393 | 22 369 | 5 149 | 18,64 | 28,81 | 7 066 |
| 10k | 9 992 | 4 651 | 5 341 | 22 226 | 4 954 | 19,38 | 29,04 | 7 067 |
| 15k | 15 073 | 5 085 | 9 988 | 24 245 | 5 062 | 18,96 | 29,23 | 7 087 |
| 20k | 19 979 | 4 910 | 15 069 | 24 244 | 4 941 | 19,43 | 29,27 | 7 067 |
| 30k | 29 977 | 10 002 | 19 975 | 50 822 | 5 165 | 18,59 | 29,45 | 7 090 |

Le premier tour naturel a réutilisé 393 tokens, essentiellement le prompt système déjà présent
dans le slot après les tests qualitatifs. Les tours suivants mesurent bien une conversation
croissante : historique précédent, réponse générée, puis nouveau message.

### INCREMENTAL-CONTROLLED

Le moteur a été redémarré avant cette série et `id_slot=0` a été imposé.

| Cible | Entrée | Évalués | Réutilisés | TTFT ms | Gén. ms | tok/s | RAM pic Gio | VRAM pic Mio |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 5k | 5 345 | 5 345 | 0 | 66 291 | 13 374 | 7,18 | 26,06 | 7 067 |
| 10k | 9 992 | 4 651 | 5 341 | 21 222 | 5 422 | 17,71 | 26,55 | 7 067 |
| 15k | 15 073 | 5 085 | 9 988 | 26 569 | 5 351 | 17,94 | 26,83 | 7 094 |
| 20k | 19 979 | 4 910 | 15 069 | 24 624 | 5 260 | 18,25 | 26,94 | 7 069 |
| 30k | 29 977 | 10 002 | 19 975 | 49 535 | 5 316 | 18,06 | 27,20 | 7 070 |

Après le premier tour froid, CONTROLLED et NATURAL réutilisent exactement les mêmes nombres de
tokens. Avec `parallel=1` et un slot, le chemin naturel atteint donc déjà la réutilisation observée
dans le chemin explicitement contrôlé ; aucune optimisation de slot n'est nécessaire pour fermer A.

### Cold-after-restart

Après arrêt et redémarrage complets de llama-server, l'historique naturel final a été repris avec
un nouveau tour. Un seul run exploitable a été demandé et conservé :

| Entrée | Évalués | Réutilisés | TTFT ms | Total ms | RAM pic Gio | VRAM pic Mio |
|---:|---:|---:|---:|---:|---:|---:|
| 30 098 | 30 098 | 0 | 183 149 | 197 101 | 26,83 | 7 211 |

La perte du KV cache après redémarrage est donc directement observable, sans inférence : tout le
contexte est réévalué. Le coût est perceptible mais accepté par la spec, sans seuil arbitraire.

## UX d'attente

- l'assistant vide affiche exactement `◌ Réponse en préparation…` ;
- `run_started` place l'UI dans `preparing` ; le premier `delta` passe à `generating` et remplace
  proprement l'indicateur par le texte ;
- Stop est visible et appelle l'annulation pour `preparing` comme pour `generating` ;
- une erreur appelle l'état terminal `error`, efface le run actif et quitte l'attente ;
- le flux SSE et les mises à jour React sont asynchrones : aucune boucle de calcul ne bloque le
  thread UI ; l'animation CSS reste indépendante du prefill backend ;
- aucune chaîne simulant un chain-of-thought n'est présente ; aucune citation tournante ajoutée.

Vérification finale : 3 tests frontend passent, dont le libellé exact et Stop en état
`preparing`, puis `tsc --noEmit` et le build Vite passent. Le prefill froid réel de 183 s a laissé
l'API disponible pendant la mesure. Les tests de cancellation/crash matériels du rapport
provisoire restent valides.

## Definition of Done

- [x] prompts v0.1.0, v0.1.1 et v0.1.2 immuables et hashés ;
- [x] 14 scénarios, plafond 1200, tokens et troncature enregistrés ;
- [x] deux comparaisons randomisées réellement aveugles, clés séparées ;
- [x] scores complets, `conversational_pull` et `appropriate_length` ;
- [x] médiane `natural_style >= 4`, garanties épistémiques sans régression ;
- [x] troncature normale nulle et absence de structure systématique ;
- [x] NATURAL et CONTROLLED à 5k/10k/15k/20k/30k ;
- [x] configuration cache, tokens évalués/réutilisés, RAM et VRAM enregistrés ;
- [x] un replay cold-after-restart exploitable ;
- [x] état d'attente honnête, Stop accessible, aucun faux raisonnement ;
- [x] aucun travail hors scope : mémoire, persistance conversationnelle, retrieval, microphone,
  backend, modèle, quantification et optimisation matérielle inchangés.

## Validation logicielle finale

```text
pytest backend/tests: 24 passed
ruff backend benchmark: passed
vitest: 3 passed
TypeScript + Vite production build: passed
```

Conclusion : la réponse vaut suffisamment l'attente et ouvre naturellement la suite de la
conversation. Les hard gates de `milestone-a10.1-spec:v1.0` sont satisfaits ; Milestone A est
fermée.
