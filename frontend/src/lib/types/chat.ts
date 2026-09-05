// Chat types for Epic 5 — RAG ticket comprehension (Story 5.2)

/** A stored or in-flight chat message. Matches backend ChatMessageResponse. */
export interface ChatMessage {
    id: string;
    session_id: string;
    role: "user" | "assistant";
    content: string;
    created_at: string;
}

/** Request body for POST /api/v1/chat/message. */
export interface ChatMessageRequest {
    session_id: string;
    question: string;
}
