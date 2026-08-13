"""Minimal real-model smoke for the Milestone G retrieval runtime classes."""

import asyncio

from backend.app.config.prompt import load_prompt
from backend.app.config.settings import REPOSITORY_ROOT, Settings
from backend.app.retrieval import (
    RerankInput,
    RetrievalSourceType,
    TransformerEmbedder,
    TransformerReranker,
)


async def main() -> int:
    settings = Settings()
    query_instruction = load_prompt(REPOSITORY_ROOT, "retrieval_query_instruction", "0.1.0").content
    rerank_instruction = load_prompt(
        REPOSITORY_ROOT, "retrieval_rerank_instruction", "0.1.0"
    ).content
    embedder = TransformerEmbedder(
        settings.embedding_model_path,
        name=settings.embedding_model_name,
        revision=settings.embedding_model_revision,
        expected_sha256=settings.embedding_model_expected_sha256,
        query_instruction=query_instruction,
        dimensions=settings.retrieval_embedding_dimensions,
    )
    vector = await embedder.embed_query("Qu'est-ce qui m'aide à apprendre ?")
    print(
        "embedding",
        len(vector),
        round(sum(value * value for value in vector), 4),
        (await embedder.health()).status,
    )
    await embedder.close()

    reranker = TransformerReranker(
        settings.reranker_model_path,
        name=settings.reranker_model_name,
        revision=settings.reranker_model_revision,
        expected_sha256=settings.reranker_model_expected_sha256,
        instruction=rerank_instruction,
    )
    ranked = await reranker.rerank(
        "Qu'est-ce qui m'aide à apprendre ?",
        [
            RerankInput(
                source_type=RetrievalSourceType.MEMORY,
                source_id="useful",
                content=("Je me sens mieux quand je comprends pourquoi les choses fonctionnent."),
            ),
            RerankInput(
                source_type=RetrievalSourceType.RAW_USER,
                source_id="noise",
                content="J'ai acheté du pain.",
            ),
        ],
        top_k=2,
    )
    print(
        "rerank",
        [(item.source_id, round(item.score, 6)) for item in ranked],
        (await reranker.health()).status,
    )
    await reranker.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
