# Rapport de sortie — Milestone A

Date :  
Commit Psych-local :  
Build llama.cpp :  
Modèle et SHA256 :  
Matériel / OS :  

## A. Qualité conversationnelle

Résumer les scores de la grille, les échecs critiques et les exemples qui justifient la conclusion.

Décision : `GO` / `CHANGE MODEL/PROMPT` / `NO-GO`

## B. Performance

| Contexte | Cold TTFT | Warm TTFT médian | Débit médian | RAM peak | VRAM peak | Verdict |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| 5k | | | | | | |
| 15k | | | | | | |
| 30k | | | | | | |

## C. Architecture

- [ ] changer le modèle sans modifier FastAPI/React ;
- [ ] changer le backend derrière `LLMBackend` ;
- [ ] annuler et libérer réellement le slot ;
- [ ] détecter un crash en plein stream ;
- [ ] récupérer après redémarrage sans relancer FastAPI ;
- [ ] mesurer et reproduire un run complet.

## Décision finale

`GO` / `CHANGE MODEL` / `CHANGE QUANTIZATION` / `CHANGE BACKEND` / `FIX MILESTONE A`

Justification :

