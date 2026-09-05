/**
 * useSSEStream.test.ts — Story 5.2 Task 1
 * Verifies the chat `token` branch fires onToken, and a chat `complete`
 * (no session_id/total) stops streaming WITHOUT firing the pipeline/
 * verification complete callbacks (regression guard for the shared hook).
 */
import { renderHook, act, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, afterEach } from "vitest";
import { useSSEStream } from "@/lib/hooks/useSSEStream";

function sseStream(frames: string[]): ReadableStream<Uint8Array> {
    const encoder = new TextEncoder();
    return new ReadableStream({
        start(controller) {
            for (const f of frames) controller.enqueue(encoder.encode(f));
            controller.close();
        },
    });
}

afterEach(() => {
    vi.restoreAllMocks();
});

describe("useSSEStream — chat token handling (Story 5.2)", () => {
    it("fires onToken per token and stops on chat complete without other callbacks", async () => {
        const onToken = vi.fn();
        const onComplete = vi.fn();
        const onVerificationComplete = vi.fn();

        vi.stubGlobal(
            "fetch",
            vi.fn().mockResolvedValue({
                ok: true,
                body: sseStream([
                    'data: {"type":"token","content":"Hi"}\n\n',
                    'data: {"type":"token","content":" there"}\n\n',
                    'data: {"type":"complete"}\n\n',
                ]),
            })
        );

        const { result } = renderHook(() =>
            useSSEStream({ onToken, onComplete, onVerificationComplete })
        );

        await act(async () => {
            await result.current.connect("http://api/chat/message", "POST", { q: 1 });
        });

        expect(onToken).toHaveBeenCalledWith("Hi");
        expect(onToken).toHaveBeenCalledWith(" there");
        // Chat complete has neither session_id nor total → neither callback fires.
        expect(onComplete).not.toHaveBeenCalled();
        expect(onVerificationComplete).not.toHaveBeenCalled();
        await waitFor(() => expect(result.current.isStreaming).toBe(false));
    });

    it("still routes a verification complete (has total) to onVerificationComplete", async () => {
        const onToken = vi.fn();
        const onVerificationComplete = vi.fn();

        vi.stubGlobal(
            "fetch",
            vi.fn().mockResolvedValue({
                ok: true,
                body: sseStream([
                    'data: {"type":"complete","total":3,"passed":2,"failed":1}\n\n',
                ]),
            })
        );

        const { result } = renderHook(() =>
            useSSEStream({ onToken, onVerificationComplete })
        );

        await act(async () => {
            await result.current.connect("http://api/verification", "POST", {});
        });

        // No "inconclusive" in the payload — a server that predates the third
        // verdict — so the count arrives as 0 rather than undefined.
        expect(onVerificationComplete).toHaveBeenCalledWith(3, 2, 1, 0, 0);
        expect(onToken).not.toHaveBeenCalled();
    });

    it("passes the inconclusive count through when the server reports one", async () => {
        const onVerificationComplete = vi.fn();

        vi.stubGlobal(
            "fetch",
            vi.fn().mockResolvedValue({
                ok: true,
                body: sseStream([
                    'data: {"type":"complete","total":4,"passed":2,"failed":1,"inconclusive":1}\n\n',
                ]),
            })
        );

        const { result } = renderHook(() => useSSEStream({ onVerificationComplete }));

        await act(async () => {
            await result.current.connect("http://api/verification", "POST", {});
        });

        expect(onVerificationComplete).toHaveBeenCalledWith(4, 2, 1, 1, 0);
    });
});
