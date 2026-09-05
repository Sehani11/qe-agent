"use client";

import React, { useEffect, useRef, useState } from "react";
import { Send, MessageCircle, AlertCircle } from "lucide-react";
import { useChat } from "@/lib/hooks/useChat";
import type { ChatMessage } from "@/lib/types/chat";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/field";
import { InlineLoader, RunSpinner } from "@/components/ui/loaders";

interface ChatPanelProps {
    sessionId: string;
}

/** A single chat bubble. Memoized so only changed rows re-render as tokens stream. */
const MessageBubble = React.memo(function MessageBubble({
    message,
    isThinking,
}: {
    message: ChatMessage;
    isThinking: boolean;
}) {
    const isUser = message.role === "user";
    return (
        <div className={isUser ? "flex justify-end" : "flex justify-start"}>
            <div
                className={
                    isUser
                        ? "max-w-[80%] rounded-md rounded-br-sm bg-pass px-3.5 py-2 text-[0.8125rem] text-white dark:text-[color:var(--paper)]"
                        : "max-w-[80%] rounded-md rounded-bl-sm border border-rule bg-surface-raised px-3.5 py-2 text-[0.8125rem] text-foreground"
                }
            >
                {isThinking ? (
                    <span className="inline-flex items-center gap-1.5 font-mono text-muted-foreground">
                        <RunSpinner className="text-pending" />
                        Thinking…
                    </span>
                ) : (
                    <span className="whitespace-pre-wrap">{message.content}</span>
                )}
            </div>
        </div>
    );
});

export default function ChatPanel({ sessionId }: ChatPanelProps) {
    const { messages, sendMessage, isStreaming, error, isHistoryLoading } = useChat(sessionId);
    const [input, setInput] = useState("");
    const scrollRef = useRef<HTMLDivElement>(null);

    // Auto-scroll to the newest message as history grows / tokens stream in.
    // Optional-chain scrollTo — it's undefined in jsdom (tests).
    useEffect(() => {
        scrollRef.current?.scrollTo?.({
            top: scrollRef.current.scrollHeight,
            behavior: "smooth",
        });
    }, [messages]);

    const handleSend = () => {
        if (!input.trim() || isStreaming) return;
        void sendMessage(input);
        setInput("");
    };

    const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
        if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            handleSend();
        }
    };

    const lastMsg = messages[messages.length - 1];
    const showThinking =
        isStreaming && lastMsg?.role === "assistant" && lastMsg.content === "";

    return (
        <section className="flex flex-col overflow-hidden rounded-lg border border-rule bg-card">
            <header className="flex items-center gap-2.5 border-b border-rule px-4 py-3.5">
                <MessageCircle className="h-4 w-4 shrink-0 text-keyword" aria-hidden="true" />
                <div>
                    <p className="eyebrow text-muted-foreground">Context</p>
                    <h2 className="mt-1 text-base">Ask about this ticket</h2>
                </div>
            </header>

            {/* Message history */}
            <div
                ref={scrollRef}
                className="scrollbar-thin flex max-h-96 min-h-[8rem] flex-col gap-2.5 overflow-y-auto px-4 py-4"
            >
                {isHistoryLoading ? (
                    <InlineLoader>Loading chat history</InlineLoader>
                ) : messages.length === 0 ? (
                    <p className="text-[0.8125rem] leading-relaxed text-muted-foreground">
                        Ask a question to understand this ticket&apos;s requirements and edge cases.
                    </p>
                ) : (
                    messages.map((m, i) => (
                        <MessageBubble
                            key={m.id}
                            message={m}
                            isThinking={
                                showThinking &&
                                i === messages.length - 1 &&
                                m.role === "assistant" &&
                                m.content === ""
                            }
                        />
                    ))
                )}
            </div>

            {/* Error banner */}
            {error && (
                <div
                    className="gutter-rule mx-4 mb-3 flex items-start gap-2 rounded-md border border-fail/30 bg-fail-soft/60 px-3 py-2"
                    data-signal="fail"
                    role="alert"
                >
                    <AlertCircle
                        className="mt-0.5 h-3.5 w-3.5 shrink-0 text-fail-ink"
                        aria-hidden="true"
                    />
                    <span className="text-xs text-foreground">{error}</span>
                </div>
            )}

            {/* Input — hidden/read-only on mobile (AC5) */}
            <div className="hidden border-t border-rule p-3 md:block">
                <div className="flex items-end gap-2">
                    <Textarea
                        aria-label="Chat question"
                        value={input}
                        onChange={(e) => setInput(e.target.value)}
                        onKeyDown={handleKeyDown}
                        rows={1}
                        placeholder="Ask about this ticket…"
                        disabled={isStreaming}
                        className="min-h-[2.25rem] flex-1 resize-none text-[0.8125rem]"
                    />
                    <Button
                        type="button"
                        onClick={handleSend}
                        disabled={isStreaming || !input.trim()}
                        loading={isStreaming}
                        size="icon"
                        aria-label="Send"
                        className="shrink-0"
                    >
                        {!isStreaming && <Send className="h-4 w-4" aria-hidden="true" />}
                    </Button>
                </div>
            </div>
            <p className="border-t border-rule px-4 py-3 text-center text-xs text-muted-foreground md:hidden">
                Chat is read-only on mobile. Open on a larger screen to ask questions.
            </p>
        </section>
    );
}
