"use client";

import React, { useEffect, useRef, useState } from "react";
import { BookOpen, ExternalLink, ChevronDown, ChevronUp } from "lucide-react";
import type { RagContextItem } from "@/lib/types/verification";

interface RAGContextPanelProps {
    items: RagContextItem[];
}

/** Empty-state message shown when a completed verification retrieved no context. */
function EmptyState() {
    return (
        <div className="rounded-md border border-dashed border-rule px-3 py-2.5">
            <p className="flex items-center gap-1.5">
                <BookOpen
                    className="h-3.5 w-3.5 shrink-0 text-muted-foreground/70"
                    aria-hidden="true"
                />
                <span className="eyebrow text-muted-foreground">Knowledge Base Context</span>
            </p>
            <p className="mt-1.5 text-xs text-muted-foreground">
                No additional project context available
            </p>
        </div>
    );
}

/** Renders the source identifier — a link (new tab) when a URL is available. */
function SourceLabel({ item }: { item: RagContextItem }) {
    const label = item.title?.trim() || item.source_id;
    const isJira = item.source === "jira";

    // Jira: always show ticket ID; append the title/summary when present.
    const text = isJira && item.title?.trim()
        ? `${item.source_id} — ${item.title.trim()}`
        : label;

    if (item.url) {
        return (
            <a
                href={item.url}
                target="_blank"
                rel="noopener noreferrer"
                onClick={(e) => e.stopPropagation()}
                className="inline-flex min-w-0 items-center gap-1 truncate font-medium text-keyword underline decoration-keyword/40 underline-offset-2 transition-colors hover:decoration-keyword"
            >
                <span className="truncate">{text}</span>
                <ExternalLink className="h-3 w-3 shrink-0" aria-hidden="true" />
            </a>
        );
    }

    return <span className="truncate font-medium text-foreground">{text}</span>;
}

/**
 * One retrieved chunk: the source line, the snippet, and the toggle.
 *
 * Its own component so each item can measure itself — whether the clamp hides
 * anything depends on this card's width, which only the element knows.
 */
function ContextItem({ item }: { item: RagContextItem }) {
    const [isOpen, setIsOpen] = useState(false);
    const [isClamped, setIsClamped] = useState(false);
    const snippetRef = useRef<HTMLParagraphElement>(null);

    // Whether `line-clamp-2` actually cuts anything off is a layout question,
    // not a length one. Guessing from character count (">100 chars") put a
    // "Show more" on snippets that already fitted on two lines — on a wide
    // card the whole 300-character snippet fits, so the button revealed
    // nothing when clicked.
    //
    // Skipped while open, because the clamp is off then and the element would
    // measure as fitting — which would remove the only control that closes it
    // again. The last collapsed measurement stands instead.
    useEffect(() => {
        const el = snippetRef.current;
        if (isOpen || !el) return;

        // ResizeObserver reports the initial size on observe, so this measures
        // now and again on every reflow — no synchronous read needed here.
        const observer = new ResizeObserver(() => {
            // A pixel of tolerance: fractional line heights round the two
            // values apart on text that is not actually clamped.
            setIsClamped(el.scrollHeight > el.clientHeight + 1);
        });
        observer.observe(el);
        return () => observer.disconnect();
    }, [isOpen]);

    return (
        <li className="rounded border border-rule bg-card px-3 py-2">
            <div className="mb-1 flex items-center gap-2">
                <span className="shrink-0 rounded-full border border-keyword/30 bg-keyword-soft px-1.5 py-0.5 font-mono text-[10px] font-semibold uppercase text-keyword">
                    {item.source}
                </span>
                <span className="min-w-0 flex-1 truncate font-mono text-xs">
                    <SourceLabel item={item} />
                </span>
            </div>
            <p
                ref={snippetRef}
                className={
                    isOpen
                        ? "text-xs leading-relaxed text-muted-foreground"
                        : "line-clamp-2 text-xs leading-relaxed text-muted-foreground"
                }
            >
                {item.snippet}
            </p>
            {isClamped && (
                <button
                    type="button"
                    onClick={() => setIsOpen((open) => !open)}
                    aria-expanded={isOpen}
                    className="mt-1.5 inline-flex items-center gap-0.5 font-mono text-[11px] font-semibold text-keyword transition-opacity hover:opacity-75"
                >
                    {isOpen ? (
                        <>
                            Show less
                            <ChevronUp className="h-3 w-3" aria-hidden="true" />
                        </>
                    ) : (
                        <>
                            Show more
                            <ChevronDown className="h-3 w-3" aria-hidden="true" />
                        </>
                    )}
                </button>
            )}
        </li>
    );
}

export default function RAGContextPanel({ items }: RAGContextPanelProps) {
    if (!items || items.length === 0) return <EmptyState />;

    return (
        <div className="gutter-rule rounded-md border border-keyword/25 bg-keyword-soft/50 px-3 py-2.5">
            <p className="mb-2 flex items-center gap-1.5">
                <BookOpen className="h-3.5 w-3.5 shrink-0 text-keyword" aria-hidden="true" />
                <span className="eyebrow text-keyword">Knowledge Base Context</span>
            </p>
            <ul className="flex flex-col gap-2">
                {items.map((item, idx) => (
                    <ContextItem
                        key={`${item.source}-${item.source_id}-${idx}`}
                        item={item}
                    />
                ))}
            </ul>
        </div>
    );
}
