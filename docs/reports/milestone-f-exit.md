# Milestone F — rapport de sortie

Date de validation : 2026-08-13 (Europe/Zurich)  
Branche : `milestone-f`  
Modèle : `Qwen3.6-35B-A3B-Q4_K_M`  
SHA-256 : `671e47e0ec53c665d048b98c3ecbfd5236b5ca9c3e02ed19fc8f81f7b85140c7`

## Décision

**GO — Milestone F est clôturable et Milestone G est autorisée.**

F persiste une mémoire structurée, sourcée et contrôlable, mais ne la réinjecte pas dans le
contexte de chat. La récupération contextuelle reste explicitement hors périmètre jusqu'à G.

## Preuves réelles

Le 2026-08-13, le runtime local a appliqué le schéma 12 à la base existante et a retourné :

- `schema_current=true`, `foreign_keys=true`, `journal_mode=wal` ;
- moteur llama.cpp chargé et backend sain ;
- corpus réel `memory_extraction:v0.1.2` : **17/17 cas passés**.

Le rapport machine lisible est [milestone-f-corpus.json](milestone-f-corpus.json). Il couvre les
captures, exclusions, labels épistémiques, contamination assistant, voix, doublons, entités Alex
ambiguës et mises à jour d'objectif. Le smoke isolé initial est conservé dans
[milestone-f-corpus-smoke.json](milestone-f-corpus-smoke.json).

L'audit local agrégé, sans contenu utilisateur, a retourné :

```text
assistant_contamination_count = 0
unsupported_inference_count   = 0
wrong_entity_merge_count      = 0
failed_jobs                   = 0
```

La base de production ne contenait pas encore de messages traités par F : les compteurs de volume
et rendements sont donc à zéro, sans masquer les résultats du corpus réel.

## Contrôles fonctionnels

| Gate | Statut | Preuve |
| --- | --- | --- |
| JOB_DURABILITY | GO | jobs SQL durables, lease, reprise, retry et préemption testés |
| CHAT_NON_BLOCKING | GO | idle 120 s, un seul worker, chat/STT préemptent le background |
| GROUNDING | GO | citations exactes, offsets et hash ; USER-only imposé aussi par trigger SQL |
| EPISTEMIC_SAFETY | GO | corpus réel 17/17 ; belief et interpretation distincts de personal_fact |
| MEMORY_EXTRACTION | GO | extraction structurée, candidates auditables, pas de source assistant |
| CONSOLIDATION | GO | doublons exacts seulement ; mise à jour explicite prudente ; ambiguïté conservée |
| USER_CONTROL | GO | inspection, source, correction, reclassification, disable/enable et suppression structurée |
| ENTITY_SAFETY | GO | entités non résolues par défaut, aucune fusion automatique par nom |
| MEMORY_AUDIT | GO | script et endpoint debug agrégés, sans contenu par défaut |
| SECURITY_REGRESSION | GO | SQLCipher/DPAPI, loopback authentifié, `no-store`, tests DPAPI verts |
| REGRESSION | GO | backend 134 passés, 1 ignoré ; DPAPI 2 passés ; frontend 13 passés et build vert |

## Cycle de vie et backfill

- `memory_enabled` est ON par défaut ; OFF n'enqueue aucune extraction automatique.
- Le backfill est strictement opt-in : aperçu (`conversations`, `messages éligibles`) puis
  confirmation ; lots bornés à 25 par défaut ; travail durable et préemptible.
- Une suppression retire le contenu dérivé et garde le message USER brut ; un tombstone empêche la
  recréation depuis le même span, sans interdire une future nouvelle source.
- Une correction utilisateur verrouille la mémoire contre tout écrasement automatique.

## Questions de sortie

| Question | Décision |
| --- | --- |
| Trust | GO |
| Provenance | GO |
| Epistemic safety | GO |
| Parsimony | GO |
| Consolidation | GO |
| Control | GO |
| Background | GO |
| Security baseline E | GO |

## Limites conscientes

- F ne fait pas de retrieval, d'embeddings, de VectorStore ni de score de confiance/importance.
- Le consolidateur LLM est défini mais non activé systématiquement ; F préfère garder séparé à
  toute fusion incertaine.
- La mémoire ne produit aucun decay temporel automatique.
- Les métriques de rendement sur l'historique réel seront renseignées lors d'un backfill volontaire
  par l'utilisateur ; elles ne sont pas extrapolées depuis le corpus.
