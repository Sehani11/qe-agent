/**
 * ChatPanel.test.tsx — Story 5.2 RAG Chat UI
 * Covers AC1 (send), AC2 (history), AC4 (thinking indicator), AC6 (error, disable).
 */
import React from "react";
import { render, screen, fireEvent } from '@/test/test-utils';
import { describe, it, expect, vi, beforeEach } from "vitest";
import type { ChatMessage } from "@/lib/types/chat";
import ChatPanel from "@/components/pipeline/ChatPanel";

const sendMessage = vi.fn();

let chatState: {
    messages: ChatMessage[];
    sendMessage: typeof sendMessage;
    isStreaming: boolean;
    error: string | null;
    isHistoryLoading: boolean;
};

vi.mock("@/lib/hooks/useChat", () => ({
    useChat: () => chatState,
}));

function msg(overrides: Partial<ChatMessage>): ChatMessage {
    return {
        id: crypto.randomUUID(),
        session_id: "s-1",
        role: "user",
        content: "hi",
        created_at: "2026-07-05T00:00:00Z",
        ...overrides,
    };
}

beforeEach(() => {
    vi.clearAllMocks();
    chatState = {
        messages: [],
        sendMessage,
        isStreaming: false,
        error: null,
        isHistoryLoading: false,
    };
});

describe("ChatPanel", () => {
    it("renders history messages (AC2)", () => {
        chatState.messages = [
            msg({ role: "user", content: "What are the edge cases?" }),
            msg({ role: "assistant", content: "The ticket lists three edge cases." }),
        ];
        render(<ChatPanel sessionId="s-1" />);
        expect(screen.getByText("What are the edge cases?")).toBeInTheDocument();
        expect(screen.getByText("The ticket lists three edge cases.")).toBeInTheDocument();
    });

    it("shows the empty prompt when there is no history", () => {
        render(<ChatPanel sessionId="s-1" />);
        expect(screen.getByText(/ask a question to understand/i)).toBeInTheDocument();
    });

    it("shows a loading state while history loads (AC3)", () => {
        chatState.isHistoryLoading = true;
        render(<ChatPanel sessionId="s-1" />);
        expect(screen.getByText(/loading chat history/i)).toBeInTheDocument();
    });

    it("sends the typed question and clears the input (AC1)", () => {
        render(<ChatPanel sessionId="s-1" />);
        const input = screen.getByLabelText(/chat question/i);
        fireEvent.change(input, { target: { value: "How does login work?" } });
        fireEvent.click(screen.getByRole("button", { name: /send/i }));
        expect(sendMessage).toHaveBeenCalledWith("How does login work?");
        expect((input as HTMLTextAreaElement).value).toBe("");
    });

    it("sends on Enter (without Shift) (AC1)", () => {
        render(<ChatPanel sessionId="s-1" />);
        const input = screen.getByLabelText(/chat question/i);
        fireEvent.change(input, { target: { value: "q?" } });
        fireEvent.keyDown(input, { key: "Enter", shiftKey: false });
        expect(sendMessage).toHaveBeenCalledWith("q?");
    });

    it("does not send on empty input", () => {
        render(<ChatPanel sessionId="s-1" />);
        fireEvent.click(screen.getByRole("button", { name: /send/i }));
        expect(sendMessage).not.toHaveBeenCalled();
    });

    it("shows the thinking indicator while streaming an empty assistant bubble (AC4)", () => {
        chatState.isStreaming = true;
        chatState.messages = [
            msg({ role: "user", content: "q?" }),
            msg({ role: "assistant", content: "" }),
        ];
        render(<ChatPanel sessionId="s-1" />);
        expect(screen.getByText(/thinking/i)).toBeInTheDocument();
    });

    it("disables send while streaming (AC6)", () => {
        chatState.isStreaming = true;
        render(<ChatPanel sessionId="s-1" />);
        expect(screen.getByRole("button", { name: /send/i })).toBeDisabled();
    });

    it("renders an inline error banner (AC6)", () => {
        chatState.error = "The AI service is busy right now.";
        render(<ChatPanel sessionId="s-1" />);
        expect(screen.getByText(/the ai service is busy/i)).toBeInTheDocument();
    });
});
