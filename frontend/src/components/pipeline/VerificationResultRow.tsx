"use client";

import React, { useState, useCallback } from "react";
import { ChevronDown, ChevronUp, ExternalLink } from "lucide-react";
import type { VerificationVerdict } from "@/lib/types/verification";
import RAGContextPanel from "@/components/pipeline/RAGContextPanel";
import { VerdictBadge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

interface VerificationResultRowProps {
    verdict: VerificationVerdict;
}

export default function VerificationResultRow({ verdict }: VerificationResultRowProps) {
    const isFail = verdict.status === "fail";
    const isInconclusive = verdict.status === "inconclusive";
    const isPartial = verdict.status === "partial";
    // Anything that is not a clean pass needs reading, so failures, partials
    // and undecided scenarios auto-expand; only passes stay collapsed. A
    // non-pass row must never inherit the pass styling — a scenario nobody
    // could verify looking "green" is the one misread we cannot afford, and a
    // partial one hides work that is genuinely outstanding.
    const needsAttention = isFail || isInconclusive || isPartial;
    const [isExpanded, setIsExpanded] = useState(needsAttention);

    const handleToggle = useCallback(() => {
        setIsExpanded((prev) => !prev);
    }, []);

    return (
        <div
            className={cn(
                "gutter-rule overflow-hidden rounded-md border bg-card",
                isFail
                    ? "border-fail/25"
                    : isInconclusive || isPartial
                      ? "border-pending/25"
                      : "border-rule"
            )}
            data-signal={
                isFail ? "fail" : isInconclusive || isPartial ? "pending" : "pass"
            }
        >
            {/* Row header — always visible, click to expand/collapse */}
            <button
                onClick={handleToggle}
                aria-expanded={isExpanded}
                className="flex w-full items-center justify-between gap-3 px-3.5 py-3 text-left transition-colors hover:bg-muted/50"
            >
                <span className="flex min-w-0 items-center gap-2.5">
                    <VerdictBadge status={verdict.status} />
                    <span className="truncate text-[0.8125rem] font-medium text-foreground">
                        {verdict.scenario_title}
                    </span>
                </span>

                {isExpanded ? (
                    <ChevronUp
                        className="h-4 w-4 shrink-0 text-muted-foreground"
                        aria-hidden="true"
                    />
                ) : (
                    <ChevronDown
                        className="h-4 w-4 shrink-0 text-muted-foreground"
                        aria-hidden="true"
                    />
                )}
            </button>

            {/* Expanded body */}
            {isExpanded && (
                <div className="flex flex-col gap-3 border-t border-rule px-3.5 pb-4 pt-3.5">
                    <p className="text-[0.8125rem] leading-relaxed text-muted-foreground">
                        {verdict.justification}
                    </p>

                    {/* Code reference — the file and line that decided the verdict. */}
                    <p className="min-w-0">
                        <span className="inline-block max-w-full truncate rounded border border-rule bg-surface-raised px-2 py-1 font-mono text-xs text-foreground">
                            {verdict.code_reference.file}
                            {verdict.code_reference.function ? ` › ${verdict.code_reference.function}` : ""}
                            {verdict.code_reference.line ? `:${verdict.code_reference.line}` : ""}
                        </span>
                    </p>

                    {verdict.github_links.length > 0 && (
                        <div className="flex flex-wrap gap-2">
                            {verdict.github_links.map((link) => (
                                <a
                                    key={link}
                                    href={link}
                                    target="_blank"
                                    rel="noopener noreferrer"
                                    className="inline-flex items-center gap-1.5 rounded border border-rule bg-surface px-2 py-1 font-mono text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                                >
                                    <ExternalLink className="h-3 w-3" aria-hidden="true" />
                                    View on GitHub
                                </a>
                            ))}
                        </div>
                    )}

                    {/* Guidance — the fix for a failure, or what would settle an
                        undecided scenario. Both are the reader's next action. */}
                    {needsAttention && verdict.implementation_suggestion && (
                        <div
                            className="gutter-rule rounded-md border border-pending/30 bg-pending-soft/60 px-3 py-2.5"
                            data-signal="pending"
                        >
                            <p className="eyebrow text-pending-ink">
                                {isInconclusive ? "How to resolve this" : "Implementation Suggestion"}
                            </p>
                            <p className="mt-1.5 text-[0.8125rem] leading-relaxed text-foreground">
                                {verdict.implementation_suggestion}
                            </p>
                        </div>
                    )}

                    {/* Knowledge base context — always rendered for a completed
                        verdict; the panel owns its own empty state (Story 4.4). */}
                    <RAGContextPanel items={verdict.rag_context ?? []} />
                </div>
            )}
        </div>
    );
}
