import asyncio
import json
import re
from pathlib import Path

from pydantic import ValidationError

from backend.app.config.metadata import git_commit, runtime_info
from backend.app.config.prompt import load_prompt
from backend.app.config.settings import Settings
from backend.app.conversations.models import Message, MessageRole
from backend.app.conversations.repository import ConversationRepository, new_id
from backend.app.llm.base import LLMBackend
from backend.app.llm.models import LLMMessage, LLMRole
from backend.app.runs import ActiveRun, RunRepository, RunStatus
from backend.app.summaries.models import RollingSummary


class SummaryGenerationError(RuntimeError):
    code = "SUMMARY_GENERATION_FAILED"


class SummaryValidationError(SummaryGenerationError):
    code = "SUMMARY_VALIDATION_FAILED"


_WORDS = re.compile(r"[\wÀ-ÿ'-]+", re.UNICODE)
_FRAME_WORDS = {
    "assistant",
    "utilisateur",
    "utilisatrice",
    "affirme",
    "indique",
    "rapporte",
    "dit",
    "demande",
    "souhaite",
    "explique",
    "décrit",
    "précise",
    "confirme",
    "mentionne",
    "mentionné",
    "indiqué",
    "plusieurs",
    "fois",
    "reprises",
    "concernant",
    "propos",
    "que",
    "the",
    "user",
    "says",
    "reports",
    "states",
    "mentions",
    "asks",
    "wants",
    "explains",
    "describes",
    "confirms",
    "repeatedly",
}


class SummaryService:
    def __init__(
        self,
        settings: Settings,
        backend: LLMBackend,
        conversation_repository: ConversationRepository,
        run_repository: RunRepository,
        repository_root: Path,
        max_attempts: int = 2,
    ) -> None:
        self.settings = settings
        self.backend = backend
        self.conversation_repository = conversation_repository
        self.run_repository = run_repository
        self.repository_root = repository_root
        self.max_attempts = max_attempts

    async def summarize(
        self,
        conversation_id: str,
        source_messages: list[Message],
        cancel_event: asyncio.Event | None = None,
    ) -> tuple[str, str]:
        latest = await self.conversation_repository.latest_summary(conversation_id)
        covered = (
            await self.conversation_repository.summary_covered_message_ids(latest["id"])
            if latest
            else set()
        )
        new_sources = [message for message in source_messages if message.id not in covered]
        if latest and not new_sources:
            return str(latest["id"]), str(latest["content_json"])

        prompt = load_prompt(
            self.repository_root,
            self.settings.summary_prompt_id,
            self.settings.summary_prompt_version,
        )
        request_content = json.dumps(
            {
                "parent_summary": json.loads(latest["content_json"]) if latest else None,
                "new_sources": [
                    {
                        "message_id": message.id,
                        "role": message.role.value,
                        "content": message.content,
                    }
                    for message in new_sources
                ],
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        messages = [
            LLMMessage(role=LLMRole.SYSTEM, content=prompt.content),
            LLMMessage(role=LLMRole.USER, content=request_content),
        ]
        last_error: Exception | None = None
        last_error_code = SummaryGenerationError.code
        for _attempt in range(1, self.max_attempts + 1):
            run = ActiveRun(id=new_id("run"))
            await self.run_repository.create(
                run,
                model_name=self.settings.model_name,
                model_sha256=self.settings.model_expected_sha256,
                backend_name="llama.cpp",
                backend_version=self.settings.llama_cpp_version,
                backend_build=self.settings.llama_cpp_build,
                prompt_id=prompt.id,
                prompt_version=prompt.version,
                prompt_sha256=prompt.sha256,
                generation_config={"structured_schema": "rolling_summary:1.0"},
                seed=self.settings.default_seed,
                app_version=self.settings.app_version,
                app_git_commit=git_commit(self.repository_root),
                runtime_info=runtime_info(self.settings),
                context_size=self.settings.context_size,
                run_kind="rolling_summary",
            )
            await self.run_repository.update_status(run.id, RunStatus.GENERATING)
            try:
                generated = await self.backend.generate_structured(
                    messages, RollingSummary, cancel_event
                )
                summary = RollingSummary.model_validate(generated)
                self._validate_epistemic_provenance(summary, new_sources)
                content_json = summary.model_dump_json()
                if len(content_json.encode("utf-8")) > self.settings.summary_budget_tokens:
                    raise SummaryValidationError("Summary exceeds its conservative token budget")
            except asyncio.CancelledError:
                await self.run_repository.update_status(run.id, RunStatus.CANCELLED)
                raise
            except (ValidationError, SummaryValidationError) as error:
                last_error = error
                last_error_code = SummaryValidationError.code
                await self.run_repository.update_status(
                    run.id, RunStatus.FAILED, "SUMMARY_VALIDATION_FAILED"
                )
                continue
            except Exception as error:
                last_error = error
                last_error_code = SummaryGenerationError.code
                await self.run_repository.update_status(
                    run.id, RunStatus.FAILED, "SUMMARY_GENERATION_FAILED"
                )
                continue

            await self.run_repository.update_status(run.id, RunStatus.COMPLETE)
            summary_id = new_id("summary")
            await self.conversation_repository.create_summary(
                summary_id=summary_id,
                conversation_id=conversation_id,
                parent_summary_id=str(latest["id"]) if latest else None,
                content_json=content_json,
                prompt_id=prompt.id,
                prompt_version=prompt.version,
                prompt_sha256=prompt.sha256,
                model_run_id=run.id,
                source_message_ids=[message.id for message in new_sources],
            )
            return summary_id, content_json
        if last_error_code == SummaryValidationError.code:
            raise SummaryValidationError(
                "No valid rolling summary could be produced"
            ) from last_error
        raise SummaryGenerationError("No valid rolling summary could be produced") from last_error

    def _validate_epistemic_provenance(
        self, summary: RollingSummary, sources: list[Message]
    ) -> None:
        user_words = self._content_words(
            " ".join(source.content for source in sources if source.role is MessageRole.USER)
        )
        assistant_words = self._content_words(
            " ".join(source.content for source in sources if source.role is MessageRole.ASSISTANT)
        )
        for fact in summary.user_stated_facts:
            fact_words = self._content_words(fact) - _FRAME_WORDS
            if not fact_words:
                continue
            user_supported = fact_words & user_words
            assistant_only = (fact_words - user_words) & assistant_words
            support_ratio = len(user_supported) / len(fact_words)
            if assistant_only or support_ratio < 0.5:
                raise SummaryValidationError(
                    "A user_stated_fact lacks sufficient USER-source lexical support"
                )

    @staticmethod
    def _content_words(content: str) -> set[str]:
        return {word.casefold() for word in _WORDS.findall(content) if len(word) > 2}
