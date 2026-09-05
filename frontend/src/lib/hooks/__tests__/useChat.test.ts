/**
 * useChat.test.ts — Story 5.2 (review M1)
 * Directly exercises the intricate logic mocked away in ChatPanel.test:
 * optimistic push, token accumulation into the last assistant bubble,
 * history + local merge order, and the L1 empty-bubble-on-error trim.
 */
import type React from "react";
import { renderHook, act } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import type { ChatMessage } from "@/lib/types/chat";

// Capture the options passed to useSSEStream so we can fire onToken/onError.
let capturedOptions: {
    onToken?: (c: string) => void;
    onError?: (m: string) => void;
} = {};
const connect = vi.fn();
let streaming = false;

vi.mock("@/lib/hooks/useSSEStream", () => ({
    useSSEStream: (opts: typeof capturedOptions) => {
        capturedOptions = opts;
        return { connect, isStreaming: streaming };
    },
}));

let historyData: ChatMessage[] | undefined;
// Mocking the whole module means the shared test Providers cannot build a real
// QueryClient, so the pieces it mounts are stubbed alongside useQuery.
vi.mock("@tanstack/react-query", () => ({
    useQuery: () => ({ data: historyData, isLoading: false }),
    QueryClient: class {},
    QueryClientProvider: ({ children }: { children: React.ReactNode }) => children,
}));

vi.mock("@/lib/api/client", () => ({ default: { get: vi.fn() } }));
vi.mock("@/lib/supabase/client", () => ({
    supabase: {
        auth: {
            getSession: vi
                .fn()
                .mockResolvedValue({ data: { session: { access_token: "tok" } } }),
        },
    },
}));

import { useChat } from "@/lib/hooks/useChat";
import { Providers } from "@/test/test-utils";

// The hook reads the chosen model from context, so every render needs the same
// provider shell the app mounts — which also makes the default selection part
// of what is asserted on the outgoing payload below.
const render = (sessionId: string | null) =>
    renderHook(() => useChat(sessionId), { wrapper: Providers });

beforeEach(() => {
    vi.clearAllMocks();
    capturedOptions = {};
    streaming = false;
    historyData = [];
});

describe("useChat", () => {
    it("renders fetched history before this-session messages", () => {
        historyData = [
            {
                id: "h1",
                session_id: "s",
                role: "user",
                content: "old question",
                created_at: "2026-07-05T00:00:00Z",
            },
        ];
        const { result } = render("s");
        expect(result.current.messages).toHaveLength(1);
        expect(result.current.messages[0].content).toBe("old question");
    });

    it("pushes an optimistic user + assistant placeholder and connects", async () => {
        const { result } = render("s");
        await act(async () => {
            await result.current.sendMessage("how does it work?");
        });
        expect(result.current.messages).toHaveLength(2);
        expect(result.current.messages[0]).toMatchObject({
            role: "user",
            content: "how does it work?",
        });
        expect(result.current.messages[1]).toMatchObject({ role: "assistant", content: "" });
        expect(connect).toHaveBeenCalledWith(
            expect.stringContaining("/chat/message"),
            "POST",
            {
                session_id: "s",
                question: "how does it work?",
                llm_provider: "openai",
                llm_model: "gpt-4o",
            },
            expect.any(Object)
        );
    });

    it("accumulates streamed tokens into the last assistant bubble", async () => {
        const { result } = render("s");
        await act(async () => {
            await result.current.sendMessage("q?");
        });
        act(() => capturedOptions.onToken?.("Hel"));
        act(() => capturedOptions.onToken?.("lo"));
        expect(result.current.messages[1].content).toBe("Hello");
    });

    it("drops the empty assistant bubble and sets error on failure (L1)", async () => {
        const { result } = render("s");
        await act(async () => {
            await result.current.sendMessage("q?");
        });
        act(() => capturedOptions.onError?.("The AI service is busy."));
        expect(result.current.error).toBe("The AI service is busy.");
        expect(result.current.messages).toHaveLength(1);
        expect(result.current.messages[0].role).toBe("user");
    });

    it("does not send with no session id", async () => {
        const { result } = render(null);
        await act(async () => {
            await result.current.sendMessage("q?");
        });
        expect(connect).not.toHaveBeenCalled();
    });

    it("does not send an empty question", async () => {
        const { result } = render("s");
        await act(async () => {
            await result.current.sendMessage("   ");
        });
        expect(connect).not.toHaveBeenCalled();
    });
});
