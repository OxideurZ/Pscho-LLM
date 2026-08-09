import random
from dataclasses import dataclass


@dataclass(frozen=True)
class ContextScenario:
    id: str
    target_tokens: int
    seed: int
    task: str


SUBJECTS = ["Alex", "Camille", "Noa", "Sam", "Morgan", "Charlie"]
PLACES = ["atelier", "bibliothèque", "gare", "association", "équipe", "quartier"]
EVENTS = [
    "a reporté une décision après avoir reçu une information incomplète",
    "a interprété un silence comme un désaccord sans en avoir la confirmation",
    "a demandé davantage de temps avant de répondre à une proposition",
    "a constaté un écart entre ce qui avait été promis et ce qui a été livré",
    "a changé d'avis après une conversation calme avec un collègue",
    "a noté que plusieurs explications restaient compatibles avec les faits",
]


def build_context(scenario: ContextScenario) -> str:
    """Create deterministic synthetic prose with an approximate whitespace-token target."""
    randomizer = random.Random(scenario.seed)
    paragraphs: list[str] = []
    word_count = 0
    index = 1
    while word_count < scenario.target_tokens:
        paragraph = (
            f"Note {index}. {randomizer.choice(SUBJECTS)} se trouvait dans "
            f"{randomizer.choice(PLACES)} "
            f"et {randomizer.choice(EVENTS)}. Le compte rendu distingue l'observation directe, "
            "les souvenirs rapportés et les hypothèses encore incertaines. Aucun diagnostic n'est "
            "posé et les personnes concernées ne s'accordent pas encore sur la signification de "
            "l'événement. Une vérification ultérieure pourrait confirmer ou contredire cette "
            "lecture."
        )
        paragraphs.append(paragraph)
        word_count += len(paragraph.split())
        index += 1
    return "\n\n".join(paragraphs) + f"\n\nTâche finale : {scenario.task}"
