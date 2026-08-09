import asyncio
from dataclasses import dataclass
from typing import Protocol

from backend.app.conversations.models import Message
from backend.app.conversations.repository import ConversationRepository
from backend.app.llm.models import LLMMessage, LLMRole


class ContextBudgetExceededError(RuntimeError):
    code = "CONTEXT_BUDGET_EXCEEDED"


@dataclass(frozen=True)
class ContextBudget:
    max_context_tokens: int
    reserved_output_tokens: int
    safety_margin_tokens: int
    summary_budget_tokens: int
    recent_raw_budget_tokens: int

    @property
    def max_input_tokens(self) -> int:
        available = (
            self.max_context_tokens - self.reserved_output_tokens - self.safety_margin_tokens
        )
        if available <= 0:
            raise ContextBudgetExceededError("No input budget remains")
        return available


class TokenCounter(Protocol):
    def upper_bound(self, messages: list[LLMMessage]) -> int: ...


class ConservativeTokenCounter:
    """UTF-8 bytes plus framing: a safe upper bound for the pinned byte-fallback tokenizer."""

    framing_tokens_per_message = 32

    def upper_bound(self, messages: list[LLMMessage]) -> int:
        return sum(
            len(message.content.encode("utf-8")) + self.framing_tokens_per_message
            for message in messages
        )


@dataclass(frozen=True)
class ContextBuildResult:
    messages: list[LLMMessage]
    input_token_upper_bound: int
    summary_id: str | None


class SummaryProvider(Protocol):
    async def summarize(
        self,
        conversation_id: str,
        source_messages: list[Message],
        cancel_event: asyncio.Event | None = None,
    ) -> tuple[str, str]: ...


class ContextBuilder:
    def __init__(
        self,
        repository: ConversationRepository,
        summary_provider: SummaryProvider,
        token_counter: TokenCounter | None = None,
    ) -> None:
        self.repository = repository
        self.summary_provider = summary_provider
        self.token_counter = token_counter or ConservativeTokenCounter()

    async def build_context(
        self,
        *,
        conversation_id: str,
        current_message_id: str,
        system_prompt: str,
        budget: ContextBudget,
        cancel_event: asyncio.Event | None = None,
    ) -> ContextBuildResult:
        raw = await self.repository.context_messages(conversation_id, current_message_id)
        raw_messages = [
            LLMMessage(role=LLMRole(message.role.value), content=message.content) for message in raw
        ]
        full = [LLMMessage(role=LLMRole.SYSTEM, content=system_prompt), *raw_messages]
        full_count = self.token_counter.upper_bound(full)
        if full_count <= budget.max_input_tokens:
            return ContextBuildResult(full, full_count, None)

        recent: list[Message] = []
        for message in reversed(raw):
            candidate = [message, *recent]
            candidate_llm = [
                LLMMessage(role=LLMRole(item.role.value), content=item.content)
                for item in candidate
            ]
            if self.token_counter.upper_bound(candidate_llm) > budget.recent_raw_budget_tokens:
                break
            recent = candidate
        if not recent or recent[-1].id != current_message_id:
            raise ContextBudgetExceededError("Current USER does not fit recent raw budget")
        old = raw[: len(raw) - len(recent)]
        if not old:
            raise ContextBudgetExceededError(
                "Raw context exceeds budget and nothing can be summarized"
            )

        summary_id, summary_content = await self.summary_provider.summarize(
            conversation_id, old, cancel_event
        )
        combined_system = (
            f"{system_prompt.rstrip()}\n\n"
            "Résumé roulant durable des échanges plus anciens (JSON de provenance validée) :\n"
            f"{summary_content}"
        )
        messages = [
            LLMMessage(role=LLMRole.SYSTEM, content=combined_system),
            *[
                LLMMessage(role=LLMRole(message.role.value), content=message.content)
                for message in recent
            ],
        ]
        count = self.token_counter.upper_bound(messages)
        if count > budget.max_input_tokens:
            raise ContextBudgetExceededError(
                f"Built context upper bound {count} exceeds {budget.max_input_tokens}"
            )
        return ContextBuildResult(messages, count, summary_id)
