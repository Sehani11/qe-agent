"use client";

import React, { useState, useCallback } from "react";
import { CheckCircle2, XCircle, ChevronDown, ChevronUp, ExternalLink, Code2 } from "lucide-react";
import type { VerificationVerdict } from "@/lib/types/verification";

interface VerificationResultRowProps {
    verdict: VerificationVerdict;
}

export default function VerificationResultRow({ verdict }: VerificationResultRowProps) {
    const isFail = verdict.status === "fail";
    // Failed scenarios auto-expand; passed scenarios collapsed by default
    const [isExpanded, setIsExpanded] = useState(isFail);

    const handleToggle = useCallback(() => {
        setIsExpanded((prev) => !prev);
    }, []);

    return (
        <div className="rounded-2xl border border-sky-100 bg-white/90 shadow-[0_4px_20px_-8px_rgba(14,116,144,0.12)]">
            {/* Row header — always visible, click to expand/collapse */}
            <button
                onClick={handleToggle}
                aria-expanded={isExpanded}
                className="flex w-full items-center justify-between gap-3 rounded-2xl px-5 py-4 text-left transition hover:bg-sky-50/50"
            >
                <div className="flex min-w-0 items-center gap-3">
                    {/* Verdict badge */}
                    {isFail ? (
                        <span className="flex shrink-0 items-center gap-1 rounded-full border border-rose-200 bg-rose-50 px-2 py-0.5 text-xs font-semibold text-rose-700">
                            <XCircle className="h-3.5 w-3.5" aria-hidden="true" />
                            FAIL
                        </span>
                    ) : (
                        <span className="flex shrink-0 items-center gap-1 rounded-full border border-emerald-200 bg-emerald-50 px-2 py-0.5 text-xs font-semibold text-emerald-700">
                            <CheckCircle2 className="h-3.5 w-3.5" aria-hidden="true" />
                            PASS
                        </span>
                    )}

                    {/* Scenario title */}
                    <span className="truncate text-sm font-medium text-slate-900">{verdict.scenario_title}</span>
                </div>

                {/* Expand/collapse chevron */}
                {isExpanded ? (
                    <ChevronUp className="h-4 w-4 shrink-0 text-slate-400" aria-hidden="true" />
                ) : (
                    <ChevronDown className="h-4 w-4 shrink-0 text-slate-400" aria-hidden="true" />
                )}
            </button>

            {/* Expanded body */}
            {isExpanded && (
                <div className="flex flex-col gap-3 border-t border-sky-50 px-5 pb-5 pt-4">
                    {/* Justification */}
                    <p className="text-sm text-slate-700">{verdict.justification}</p>

                    {/* Code reference pill */}
                    <div className="flex items-center gap-2">
                        <Code2 className="h-3.5 w-3.5 shrink-0 text-slate-400" aria-hidden="true" />
                        <span className="font-mono text-xs bg-slate-100 border border-slate-200 rounded px-1.5 py-0.5 text-slate-700">
                            {verdict.code_reference.file}
                            {verdict.code_reference.function ? ` › ${verdict.code_reference.function}` : ""}
                            {verdict.code_reference.line ? `:${verdict.code_reference.line}` : ""}
                        </span>
                    </div>

                    {/* GitHub links */}
                    {verdict.github_links.length > 0 && (
                        <div className="flex flex-wrap gap-2">
                            {verdict.github_links.map((link) => (
                                <a
                                    key={link}
                                    href={link}
                                    target="_blank"
                                    rel="noopener noreferrer"
                                    className="flex items-center gap-1 rounded-lg border border-sky-200 bg-sky-50 px-2.5 py-1 text-xs font-medium text-sky-700 transition hover:bg-sky-100"
                                >
                                    <ExternalLink className="h-3 w-3" aria-hidden="true" />
                                    View on GitHub
                                </a>
                            ))}
                        </div>
                    )}

                    {/* Implementation suggestion — only for failed scenarios */}
                    {isFail && verdict.implementation_suggestion && (
                        <div className="rounded-xl border border-amber-200 bg-amber-50 p-3">
                            <p className="mb-1 text-xs font-semibold uppercase tracking-wider text-amber-700">
                                Implementation Suggestion
                            </p>
                            <p className="text-sm text-amber-900">{verdict.implementation_suggestion}</p>
                        </div>
                    )}
                </div>
            )}
        </div>
    );
}
