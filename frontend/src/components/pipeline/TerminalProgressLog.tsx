"use client";

import { useEffect, useRef } from "react";
import { Check, CircleDashed, X } from "lucide-react";

import { useSessionContext } from "@/context/SessionContext";
import { ProgressBar, RunSpinner } from "@/components/ui/loaders";
import { cn } from "@/lib/utils";

type StageState = "done" | "running" | "failed" | "waiting";

/**
 * The run panel: three stages in the order they actually happen, each showing
 * its own state, plus the live log tail from the SSE stream. It reads like the
 * output of a test runner because that is what the user is waiting on.
 */
export default function TerminalProgressLog() {
    const {
        jiraTicketId,
        acceptanceCriteria,
        bddContent,
        logs,
        isIngesting,
        isGeneratingBDD,
        isVerifying,
        globalError,
        verificationSummary,
    } = useSessionContext();

    const isWorking = isIngesting || isGeneratingBDD || isVerifying;

    // A stage is "failed" only when the error landed on the stage that was
    // last active — an ingest error must not paint the verify row red.
    const stages: { keyword: string; label: string; state: StageState; detail: string }[] = [
        {
            keyword: "Given",
            label: "Ingest ticket",
            state: isIngesting
                ? "running"
                : globalError && !jiraTicketId
                  ? "failed"
                  : acceptanceCriteria || jiraTicketId
                    ? "done"
                    : "waiting",
            detail: jiraTicketId ? jiraTicketId : "No ticket yet",
        },
        {
            keyword: "When",
            label: "Generate scenarios",
            state: isGeneratingBDD
                ? "running"
                : bddContent
                  ? "done"
                  : globalError && jiraTicketId && !bddContent
                    ? "failed"
                    : "waiting",
            detail: bddContent
                ? `${bddContent.split(/^\s*Scenario/m).length - 1 || 1} in the editor`
                : "Waiting on a ticket",
        },
        {
            keyword: "Then",
            label: "Verify against code",
            state: isVerifying
                ? "running"
                : verificationSummary
                  ? verificationSummary.failed > 0
                      ? "failed"
                      : "done"
                  : "waiting",
            detail: verificationSummary
                ? `${verificationSummary.passed}/${verificationSummary.total} passed`
                : "Waiting on scenarios",
        },
    ];

    const completed = stages.filter((s) => s.state === "done").length;

    return (
        <section className="flex flex-col overflow-hidden rounded-lg border border-rule bg-card">
            <header className="flex items-center justify-between gap-3 border-b border-rule px-4 py-3.5">
                <div>
                    <p className="eyebrow text-muted-foreground">Run</p>
                    <h2 className="mt-1 text-base">Pipeline</h2>
                </div>
                <span
                    className={cn(
                        "inline-flex items-center gap-1.5 font-mono text-xs font-semibold",
                        isWorking
                            ? "text-pending-ink"
                            : globalError
                              ? "text-fail-ink"
                              : "text-muted-foreground"
                    )}
                >
                    {isWorking && <RunSpinner />}
                    {isWorking ? "running" : globalError ? "stopped" : "idle"}
                </span>
            </header>

            <div className="px-4 pt-3">
                <ProgressBar
                    value={isWorking ? null : (completed / stages.length) * 100}
                    signal={globalError ? "fail" : isWorking ? "pending" : "pass"}
                    label="Pipeline progress"
                />
            </div>

            <ol className="px-4 py-3">
                {stages.map(({ keyword, label, state, detail }) => (
                    <li
                        key={keyword}
                        className="gutter-rule flex items-start gap-3 py-2.5"
                        data-signal={
                            state === "done"
                                ? "pass"
                                : state === "failed"
                                  ? "fail"
                                  : state === "running"
                                    ? "pending"
                                    : ""
                        }
                    >
                        <StageGlyph state={state} />
                        <div className="min-w-0 flex-1">
                            <p className="flex items-baseline gap-2">
                                <span className="kw text-xs">{keyword}</span>
                                <span className="truncate text-[0.8125rem] font-medium text-foreground">
                                    {label}
                                </span>
                            </p>
                            <p className="mt-0.5 truncate font-mono text-xs text-muted-foreground">
                                {detail}
                            </p>
                        </div>
                    </li>
                ))}
            </ol>

            {globalError && (
                <div
                    className="gutter-rule mx-4 mb-4 rounded-md border border-fail/30 bg-fail-soft/60 px-3 py-2.5"
                    data-signal="fail"
                    role="alert"
                >
                    <p className="eyebrow text-fail-ink">Stopped</p>
                    <p className="mt-1 text-xs leading-snug text-foreground">{globalError}</p>
                </div>
            )}

            {logs.length > 0 && <LogTail logs={logs} />}
        </section>
    );
}

function StageGlyph({ state }: { state: StageState }) {
    if (state === "running") {
        return (
            <span className="mt-0.5 w-4 shrink-0 text-center text-pending" aria-hidden="true">
                <RunSpinner />
            </span>
        );
    }
    if (state === "done") {
        return <Check className="mt-0.5 h-4 w-4 shrink-0 text-pass" aria-hidden="true" />;
    }
    if (state === "failed") {
        return <X className="mt-0.5 h-4 w-4 shrink-0 text-fail" aria-hidden="true" />;
    }
    return (
        <CircleDashed
            className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground/50"
            aria-hidden="true"
        />
    );
}

function LogTail({ logs }: { logs: { message: string; timestamp: string }[] }) {
    const endRef = useRef<HTMLDivElement>(null);

    // Follow the tail as new lines stream in.
    useEffect(() => {
        endRef.current?.scrollIntoView({ block: "nearest" });
    }, [logs.length]);

    return (
        <div className="border-t border-rule">
            <p className="eyebrow px-4 pb-1.5 pt-3 text-muted-foreground">Log</p>
            <div
                className="scrollbar-thin max-h-44 overflow-y-auto px-4 pb-4"
                role="log"
                aria-live="polite"
                aria-label="Pipeline log"
            >
                {logs.map((entry, index) => (
                    <p
                        key={`${entry.timestamp}-${index}`}
                        className="flex gap-2 py-0.5 font-mono text-[0.6875rem] leading-relaxed"
                    >
                        <span className="shrink-0 text-muted-foreground/60 tabular-nums">
                            {new Date(entry.timestamp).toLocaleTimeString(undefined, {
                                hour12: false,
                            })}
                        </span>
                        <span className="min-w-0 break-words text-muted-foreground">
                            {entry.message}
                        </span>
                    </p>
                ))}
                <div ref={endRef} />
            </div>
        </div>
    );
}
