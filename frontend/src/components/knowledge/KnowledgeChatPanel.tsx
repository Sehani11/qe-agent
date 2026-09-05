"use client";

import React, { useEffect, useRef, useState } from "react";
import { Send, Sparkles, AlertCircle, ExternalLink } from "lucide-react";
import { useKnowledgeChat, type KnowledgeChatMessage } from "@/lib/hooks/useKnowledgeChat";
import type { RagContextItem } from "@/lib/types/verification";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/field";
import { RunSpinner } from "@/components/ui/loaders";

/** Renders the knowledge sources an assistant answer was grounded in. */
function SourceList({ sources }: { sources: RagContextItem[] }) {
    if (!sources || sources.length === 0) return null;
    return (
        <div className="mt-2 flex flex-wrap gap-1.5">
            {sources.map((s, i) => {
                const label = s.title || s.source_id || s.source;
                const chip = (
                    <span className="inline-flex items-center gap-1 rounded-full border border-keyword/30 bg-keyword-soft px-2 py-0.5 font-mono text-[11px] font-semibold text-keyword">
                        {label}
                        {s.url && <ExternalLink className="h-3 w-3" aria-hidden="true" />}
                    </span>
                );
                return s.url ? (
                    <a
                        key={`${s.source_id}-${i}`}
                        href={s.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        title={s.snippet}
                    >
                        {chip}
                    </a>
                ) : (
                    <span key={`${s.source_id}-${i}`} title={s.snippet}>
                        {chip}
                    </span>
                );
            })}
        </div>
    );
}

/** A single chat bubble. Memoized so only changed rows re-render as tokens stream. */
const MessageBubble = React.memo(function MessageBubble({
    message,
    isThinking,
}: {
    message: KnowledgeChatMessage;
    isThinking: boolean;
}) {
    const isUser = message.role === "user";
    return (
        <div className={isUser ? "flex justify-end" : "flex flex-col items-start"}>
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
                        Searching your knowledge base…
                    </span>
                ) : (
                    <span className="whitespace-pre-wrap">{message.content}</span>
                )}
            </div>
            {!isUser && message.sources && <SourceList sources={message.sources} />}
        </div>
    );
});

export default function KnowledgeChatPanel() {
    const { messages, sendMessage, isStreaming, error } = useKnowledgeChat();
    const [input, setInput] = useState("");
    const scrollRef = useRef<HTMLDivElement>(null);

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
                <Sparkles className="h-4 w-4 shrink-0 text-keyword" aria-hidden="true" />
                <div>
                    <p className="eyebrow text-muted-foreground">Ask</p>
                    <h3 className="mt-1">Ask about your project</h3>
                </div>
            </header>

            {/* Message history */}
            <div
                ref={scrollRef}
                className="scrollbar-thin flex max-h-96 min-h-[8rem] flex-col gap-2.5 overflow-y-auto px-4 py-4"
            >
                {messages.length === 0 ? (
                    <p className="text-[0.8125rem] leading-relaxed text-muted-foreground">
                        Ask a question about your project. Answers are grounded in the
                        Confluence pages, Jira tickets, and documents you&apos;ve ingested
                        above.
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

            {/* Input — hidden on mobile (desktop-first, mirrors ticket chat) */}
            <div className="hidden border-t border-rule p-3 md:block">
                <div className="flex items-end gap-2">
                    <Textarea
                        aria-label="Knowledge base question"
                        value={input}
                        onChange={(e) => setInput(e.target.value)}
                        onKeyDown={handleKeyDown}
                        rows={1}
                        placeholder="Ask about your project…"
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
