"""Chat schemas for the RAG ticket-comprehension chatbot (Story 5.1)."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.llm import LLMSelectionMixin


class ChatMessageRequest(LLMSelectionMixin):
    """Request body for POST /api/v1/chat/message."""

    session_id: str = Field(..., description="Session whose ticket is being queried")
    question: str = Field(
        ..., min_length=1, description="User's natural-language question"
    )


class ChatMessageResponse(BaseModel):
    """A stored chat message row (for GET history)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    session_id: str
    role: str  # "user" | "assistant"
    content: str
    created_at: datetime


class KnowledgeChatRequest(LLMSelectionMixin):
    """Request body for POST /api/v1/chat/knowledge.

    Answered from one project's ingested knowledge base. Not scoped to a
    session.
    """

    question: str = Field(
        ..., min_length=1, description="User's natural-language question"
    )
    project_id: uuid.UUID | None = Field(
        default=None,
        description=(
            "Whose knowledge base to answer from. Omit to read the "
            "pre-projects namespace."
        ),
    )
