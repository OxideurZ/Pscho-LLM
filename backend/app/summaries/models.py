from pydantic import BaseModel, ConfigDict, Field


class RollingSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_progression: list[str] = Field(max_length=128)
    user_stated_facts: list[str] = Field(max_length=128)
    user_interpretations: list[str] = Field(max_length=128)
    assistant_proposals: list[str] = Field(max_length=128)
    topics_discussed: list[str] = Field(max_length=128)
    open_questions: list[str] = Field(max_length=128)
    decisions_or_actions: list[str] = Field(max_length=128)
