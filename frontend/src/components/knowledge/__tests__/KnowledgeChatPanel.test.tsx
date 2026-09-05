/**
 * KnowledgeChatPanel.test.tsx
 *
 * Unit tests for the project knowledge-base RAG chat panel (Story 5.5).
 * Covers: empty state, sending a question, rendering streamed answer + source
 * citations, and the error banner.
 */
import React from "react";
import { render, screen, fireEvent } from '@/test/test-utils';
import { describe, it, expect, vi, beforeEach } from "vitest";
import KnowledgeChatPanel from "@/components/knowledge/KnowledgeChatPanel";
import type { KnowledgeChatMessage } from "@/lib/hooks/useKnowledgeChat";

// ---------------------------------------------------------------------------
// Mock useKnowledgeChat
// ---------------------------------------------------------------------------

let mockMessages: KnowledgeChatMessage[] = [];
let mockIsStreaming = false;
let mockError: string | null = null;
const mockSend = vi.fn();

vi.mock("@/lib/hooks/useKnowledgeChat", () => ({
    useKnowledgeChat: () => ({
        messages: mockMessages,
        sendMessage: mockSend,
        isStreaming: mockIsStreaming,
        error: mockError,
    }),
}));

function reset() {
    mockMessages = [];
    mockIsStreaming = false;
    mockError = null;
}

describe("KnowledgeChatPanel", () => {
    beforeEach(() => {
        reset();
        vi.clearAllMocks();
    });

    it("shows the empty-state prompt when there are no messages", () => {
        render(<KnowledgeChatPanel />);
        expect(screen.getByText(/ask a question about your project/i)).toBeInTheDocument();
    });

    it("sends the typed question via sendMessage", () => {
        render(<KnowledgeChatPanel />);
        const box = screen.getByLabelText(/knowledge base question/i);
        fireEvent.change(box, { target: { value: "how does auth work?" } });
        fireEvent.click(screen.getByLabelText("Send"));
        expect(mockSend).toHaveBeenCalledWith("how does auth work?");
    });

    it("renders the answer and its source citations", () => {
        mockMessages = [
            { id: "u1", role: "user", content: "how does auth work?" },
            {
                id: "a1",
                role: "assistant",
                content: "Auth uses Supabase JWT.",
                sources: [
                    {
                        source: "confluence",
                        source_id: "123",
                        snippet: "JWT via JWKS",
                        title: "Auth Architecture",
                        url: "https://confluence/x",
                    },
                ],
            },
        ];
        render(<KnowledgeChatPanel />);

        expect(screen.getByText("Auth uses Supabase JWT.")).toBeInTheDocument();
        const cite = screen.getByText("Auth Architecture");
        expect(cite).toBeInTheDocument();
        expect(cite.closest("a")).toHaveAttribute("href", "https://confluence/x");
        expect(cite.closest("a")).toHaveAttribute("target", "_blank");
    });

    it("disables the send button while streaming", () => {
        mockIsStreaming = true;
        render(<KnowledgeChatPanel />);
        expect(screen.getByLabelText("Send")).toBeDisabled();
    });

    it("shows an error banner when the hook reports an error", () => {
        mockError = "Failed to generate a response.";
        render(<KnowledgeChatPanel />);
        expect(screen.getByText(/failed to generate a response/i)).toBeInTheDocument();
    });
});
