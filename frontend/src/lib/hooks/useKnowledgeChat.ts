"use client";

import { useCallback, useState } from "react";

import { supabase } from "@/lib/supabase/client";
import type { RagContextItem } from "@/lib/types/verification";
import { useModelSelection } from "@/providers/ModelProvider";
import { useActiveProjectId } from "@/lib/stores/projectStore";
import { useSSEStream } from "./useSSEStream";

/** A message in the project knowledge-base chat (session-less, not persisted). */
export interface KnowledgeChatMessage {
    id: string;
    role: "user" | "assistant";
    content: string;
    /** Knowledge sources the answer was grounded in (assistant messages only). */
    sources?: RagContextItem[];
}

/**
 * Orchestrates the project-wide knowledge-base chat: sends a question over SSE
 * (POST /api/v1/chat/knowledge), accumulates streamed tokens into one assistant
 * bubble, and attaches the retrieved sources. Stateless — history lives only in
 * component state for this mount (the backend persists nothing).
 */
export function useKnowledgeChat() {
    const selection = useModelSelection();
    // Answers come from this project's knowledge namespace, not a shared one.
    const activeProjectId = useActiveProjectId();
    const [messages, setMessages] = useState<KnowledgeChatMessage[]>([]);
    const [error, setError] = useState<string | null>(null);

    const { connect, isStreaming } = useSSEStream({
        onToken: useCallback((content: string) => {
            setMessages((prev) => {
                if (prev.length === 0) return prev;
                const last = prev[prev.length - 1];
                if (last.role !== "assistant") return prev;
                const updated = { ...last, content: last.content + content };
                return [...prev.slice(0, -1), updated];
            });
        }, []),
        onSources: useCallback((sources: RagContextItem[]) => {
            setMessages((prev) => {
                if (prev.length === 0) return prev;
                const last = prev[prev.length - 1];
                if (last.role !== "assistant") return prev;
                return [...prev.slice(0, -1), { ...last, sources }];
            });
        }, []),
        onError: useCallback((message: string) => {
            setError(message);
            // Drop the empty assistant placeholder so no blank bubble lingers.
            setMessages((prev) => {
                const last = prev[prev.length - 1];
                if (last && last.role === "assistant" && last.content === "") {
                    return prev.slice(0, -1);
                }
                return prev;
            });
        }, []),
    });

    const sendMessage = useCallback(
        async (question: string) => {
            const q = question.trim();
            if (!q || isStreaming) return;

            setError(null);
            setMessages((prev) => [
                ...prev,
                { id: crypto.randomUUID(), role: "user", content: q },
                { id: crypto.randomUUID(), role: "assistant", content: "" },
            ]);

            const apiBaseUrl = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
            const {
                data: { session },
            } = await supabase.auth.getSession();
            const authHeaders: Record<string, string> = session?.access_token
                ? { Authorization: `Bearer ${session.access_token}` }
                : {};

            connect(
                `${apiBaseUrl}/api/v1/chat/knowledge`,
                "POST",
                { question: q, project_id: activeProjectId, ...selection },
                authHeaders
            );
        },
        [isStreaming, connect, selection, activeProjectId]
    );

    return { messages, sendMessage, isStreaming, error };
}
