#!/usr/bin/env python3
"""Build a randomized blind A/B booklet and keep its answer key separate."""

import argparse
import hashlib
import json
import random
from pathlib import Path
from typing import Any


def read_jsonl(path: Path) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            rows[row["scenario_id"]] = row
    return rows


def comparable(left: dict[str, Any], right: dict[str, Any]) -> None:
    fields = ("model_sha256", "backend_version", "backend_build")
    differences = [field for field in fields if left.get(field) != right.get(field)]
    left_generation = json.loads(left["generation_config_json"])
    right_generation = json.loads(right["generation_config_json"])
    for field in ("temperature", "top_p", "top_k", "min_p", "seed", "max_tokens"):
        if left_generation.get(field) != right_generation.get(field):
            differences.append(f"generation.{field}")
    if differences:
        raise ValueError(f"Non-comparable runs: {', '.join(differences)}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--booklet", type=Path, required=True)
    parser.add_argument("--key", type=Path, required=True)
    parser.add_argument("--scores-template", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=10401)
    args = parser.parse_args()

    baseline = read_jsonl(args.baseline)
    candidate = read_jsonl(args.candidate)
    scenario_ids = sorted(set(baseline) & set(candidate))
    if not scenario_ids:
        raise ValueError("No paired scenarios")

    rng = random.Random(args.seed)
    key: dict[str, Any] = {"randomization_seed": args.seed, "pairs": {}}
    dimensions = [
        "understanding",
        "fact_vs_interpretation",
        "non_acquiescence",
        "uncertainty_handling",
        "alternative_explanations",
        "question_quality",
        "non_diagnostic",
        "natural_style",
        "appropriate_length",
        "unnecessary_structure",
        "unnecessary_repetition",
        "context_use",
        "conversational_pull",
    ]
    scores: dict[str, Any] = {"rubric_version": "a10.1", "pairs": {}}
    sections = [
        "# Comparaison aveugle A/B — Milestone A10.1",
        "",
        "Noter A et B sans consulter la clé séparée. Échelle 1–5 ; `null` si non sollicité.",
    ]
    for scenario_id in scenario_ids:
        left, right = baseline[scenario_id], candidate[scenario_id]
        comparable(left, right)
        swapped = bool(rng.getrandbits(1))
        versions = [candidate, baseline] if swapped else [baseline, candidate]
        labels = [versions[0][scenario_id], versions[1][scenario_id]]
        key["pairs"][scenario_id] = {
            "A": labels[0]["prompt_version"],
            "B": labels[1]["prompt_version"],
        }
        scores["pairs"][scenario_id] = {
            label: {dimension: None for dimension in dimensions} | {"notes": ""}
            for label in ("A", "B")
        }
        user_text = "\n\n".join(
            f"{message['role']}: {message['content']}" for message in left["messages"]
        )
        sections.extend(
            [
                "",
                f"## {scenario_id}",
                "",
                "### Conversation",
                "",
                user_text,
                "",
                "### Réponse A",
                "",
                labels[0]["answer"],
                "",
                "### Réponse B",
                "",
                labels[1]["answer"],
            ]
        )

    args.booklet.parent.mkdir(parents=True, exist_ok=True)
    args.key.parent.mkdir(parents=True, exist_ok=True)
    args.scores_template.parent.mkdir(parents=True, exist_ok=True)
    args.booklet.write_text("\n".join(sections) + "\n", encoding="utf-8")
    args.key.write_text(json.dumps(key, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.scores_template.write_text(
        json.dumps(scores, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    fingerprint = hashlib.sha256(args.booklet.read_bytes()).hexdigest()
    print(f"Wrote {len(scenario_ids)} blind pairs; booklet_sha256={fingerprint}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
