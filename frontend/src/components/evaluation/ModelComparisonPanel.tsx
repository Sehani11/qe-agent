"use client";

import React, { useState } from "react";
import {
    AlertCircle,
    AlertTriangle,
    CheckCircle2,
    Clock,
    Scale,
} from "lucide-react";
import { useModelComparison } from "@/lib/hooks/useModelComparison";
import type { ProviderResult } from "@/lib/types/evaluation";
import { Button } from "@/components/ui/button";
import { Field, Input, Textarea } from "@/components/ui/field";
import { useToast } from "@/components/ui/toast";

const PROVIDER_LABELS: Record<string, string> = {
    general_llm: "General LLM",
    fine_tuned: "Fine-tuned model",
};

function label(provider: string) {
    return PROVIDER_LABELS[provider] ?? provider;
}

/** Percentages for the rate metrics; "—" when the metric is undefined. */
function pct(value: number | null): string {
    return value === null || value === undefined
        ? "—"
        : `${Math.round(value * 100)}%`;
}

function secs(value: number | null): string {
    return value === null || value === undefined ? "—" : `${value.toFixed(1)}s`;
}

function ErrorNote({ message }: { message: string }) {
    return (
        <div
            className="gutter-rule flex items-start gap-2 rounded-md border border-fail/30 bg-fail-soft/60 px-3 py-2"
            data-signal="fail"
            role="alert"
        >
            <AlertCircle
                className="mt-0.5 h-3.5 w-3.5 shrink-0 text-fail-ink"
                aria-hidden="true"
            />
            <span className="text-xs text-foreground">{message}</span>
        </div>
    );
}

function Metric({
    name,
    value,
    hint,
}: {
    name: string;
    value: string;
    hint?: string;
}) {
    return (
        <div className="rounded-md border border-rule bg-surface-raised px-3 py-2">
            <dt className="eyebrow text-muted-foreground">{name}</dt>
            <dd className="mt-1 font-mono text-lg font-semibold tabular-nums text-foreground">
                {value}
            </dd>
            {hint ? (
                <p className="mt-0.5 text-[11px] leading-tight text-muted-foreground">{hint}</p>
            ) : null}
        </div>
    );
}

function ResultColumn({ result }: { result: ProviderResult }) {
    const substituted =
        result.succeeded &&
        result.effective_provider !== null &&
        result.effective_provider !== result.configured_provider;

    return (
        <section
            className="gutter-rule rounded-lg border border-rule bg-card p-4"
            data-signal={result.succeeded ? "pass" : "fail"}
        >
            <header className="flex items-center justify-between gap-2">
                <h3>{label(result.configured_provider)}</h3>
                {result.succeeded ? (
                    <CheckCircle2 className="h-4 w-4 text-pass" aria-label="answered" />
                ) : (
                    <AlertCircle className="h-4 w-4 text-fail" aria-label="did not answer" />
                )}
            </header>

            {result.model_identifier ? (
                <p className="mt-1 truncate font-mono text-[11px] text-muted-foreground">
                    {result.model_identifier}
                </p>
            ) : null}

            {/* A model that did not answer shows nothing. The other model's
                output is deliberately NOT substituted here — that would make
                two columns describe one model. */}
            {!result.succeeded ? (
                <div className="mt-3">
                    <ErrorNote
                        message={
                            result.error
                                ? `Did not answer — ${result.error}`
                                : "Did not answer."
                        }
                    />
                    <p className="mt-2 text-xs leading-relaxed text-muted-foreground">
                        No scenarios are shown for this model. Nothing has been
                        substituted in its place.
                    </p>
                </div>
            ) : (
                <>
                    {substituted ? (
                        <div
                            className="gutter-rule mt-3 flex items-start gap-2 rounded-md border border-pending/30 bg-pending-soft/60 px-3 py-2"
                            data-signal="pending"
                        >
                            <AlertTriangle
                                className="mt-0.5 h-3.5 w-3.5 shrink-0 text-pending-ink"
                                aria-hidden="true"
                            />
                            <span className="text-xs text-foreground">
                                Answered by {label(result.effective_provider ?? "")}
                                {result.fallback_reason
                                    ? ` (${result.fallback_reason})`
                                    : ""}
                                . This column does not describe the fine-tuned model.
                            </span>
                        </div>
                    ) : null}

                    <dl className="mt-3 grid grid-cols-3 gap-2">
                        <Metric
                            name="Coverage"
                            value={pct(result.coverage)}
                            hint="clauses covered"
                        />
                        <Metric
                            name="Duplicates"
                            value={pct(result.duplicate_rate)}
                            hint="lower is better"
                        />
                        <Metric name="Time" value={secs(result.latency_seconds)} />
                    </dl>

                    {/* Scenarios keep the Gherkin keyword column so both models'
                        output can be scanned down the same left edge. */}
                    <ol className="mt-3 space-y-2">
                        {result.scenarios.map((s, i) => (
                            <li
                                key={`${s.source_ac_clause}-${i}`}
                                className="rounded-md border border-rule bg-surface-raised p-3"
                            >
                                <div className="flex flex-wrap items-center gap-2">
                                    <span className="rounded border border-keyword/30 bg-keyword-soft px-1.5 py-0.5 font-mono text-[11px] font-semibold text-keyword">
                                        {s.source_ac_clause}
                                    </span>
                                    <span className="text-xs font-medium text-foreground">
                                        {s.scenario}
                                    </span>
                                </div>
                                <dl className="mt-2 space-y-1 text-[11px] leading-snug text-muted-foreground">
                                    <div>
                                        <span className="kw">Given </span>
                                        {s.given}
                                    </div>
                                    <div>
                                        <span className="kw">When </span>
                                        {s.when}
                                    </div>
                                    <div>
                                        <span className="kw">Then </span>
                                        {s.then}
                                    </div>
                                </dl>
                            </li>
                        ))}
                    </ol>
                </>
            )}
        </section>
    );
}

export default function ModelComparisonPanel() {
    const compare = useModelComparison();
    const [ticketId, setTicketId] = useState("");
    const [criteria, setCriteria] = useState("");
    const [validation, setValidation] = useState<string | null>(null);
    const toast = useToast();

    const handleSubmit = (e: React.FormEvent) => {
        e.preventDefault();
        setValidation(null);

        const ticket = ticketId.trim();
        const text = criteria.trim();
        if (!ticket && !text) {
            setValidation(
                "Enter a Jira ticket ID or paste the acceptance criteria."
            );
            return;
        }

        // Criteria win when both are filled, matching the backend, so the run
        // always uses the text the user can actually see.
        compare.mutate(text ? { acceptance_criteria: text } : { jira_ticket_id: ticket }, {
            onSuccess: (data) => {
                if (data.comparable) {
                    toast.success("Comparison ready", {
                        description: "Both models answered. Results are below.",
                    });
                } else {
                    toast.warning("Not a valid comparison", {
                        description: "See the notes below for what went wrong.",
                    });
                }
            },
            onError: (error) => {
                toast.error("Comparison failed", {
                    description: error?.message ?? "The run could not be completed.",
                });
            },
        });
    };

    const result = compare.data;

    return (
        <div className="space-y-6">
            <form
                onSubmit={handleSubmit}
                className="space-y-4 rounded-lg border border-rule bg-card p-4"
            >
                <Field label="Jira ticket" htmlFor="ticket">
                    <Input
                        id="ticket"
                        type="text"
                        mono
                        value={ticketId}
                        onChange={(e) => setTicketId(e.target.value)}
                        placeholder="PROJ-123 or a full Jira URL"
                    />
                </Field>

                <Field
                    label="Or paste acceptance criteria"
                    htmlFor="criteria"
                    description="Number the clauses AC1, AC2, … — coverage is measured against them and cannot be computed without them."
                >
                    <Textarea
                        id="criteria"
                        mono
                        rows={5}
                        value={criteria}
                        onChange={(e) => setCriteria(e.target.value)}
                        placeholder={"AC1: A user can request a reset link.\nAC2: A link older than one hour is rejected."}
                        className="text-xs"
                    />
                </Field>

                {validation ? <ErrorNote message={validation} /> : null}

                {compare.isError ? (
                    <ErrorNote
                        message={
                            compare.error?.message ?? "The comparison could not be run."
                        }
                    />
                ) : null}

                <Button type="submit" disabled={compare.isPending} loading={compare.isPending}>
                    {!compare.isPending && <Scale className="h-4 w-4" aria-hidden="true" />}
                    {compare.isPending ? "Running both models…" : "Compare"}
                </Button>

                {compare.isPending ? (
                    <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
                        <Clock className="h-3.5 w-3.5" aria-hidden="true" />
                        The fine-tuned model runs partly on the CPU and can take a
                        minute or more.
                    </p>
                ) : null}
            </form>

            {result ? (
                <>
                    {!result.comparable ? (
                        <div
                            className="gutter-rule flex items-start gap-2 rounded-md border border-pending/30 bg-pending-soft/60 px-4 py-3"
                            data-signal="pending"
                        >
                            <AlertTriangle
                                className="mt-0.5 h-4 w-4 shrink-0 text-pending-ink"
                                aria-hidden="true"
                            />
                            <div>
                                <p className="text-sm font-semibold text-foreground">
                                    This is not a valid comparison.
                                </p>
                                <ul className="mt-1 list-disc space-y-0.5 pl-4 text-xs text-muted-foreground">
                                    {result.notes.map((n, i) => (
                                        <li key={i}>{n}</li>
                                    ))}
                                </ul>
                            </div>
                        </div>
                    ) : result.notes.length > 0 ? (
                        <div className="rounded-md border border-rule bg-surface-raised px-4 py-3">
                            <ul className="list-disc space-y-0.5 pl-4 text-xs text-muted-foreground">
                                {result.notes.map((n, i) => (
                                    <li key={i}>{n}</li>
                                ))}
                            </ul>
                        </div>
                    ) : null}

                    <div className="rounded-md border border-rule bg-card px-4 py-3">
                        <p className="font-mono text-xs text-muted-foreground">
                            {result.jira_ticket_id ? (
                                <>
                                    <span className="font-semibold text-foreground">
                                        {result.jira_ticket_id}
                                    </span>{" "}
                                    ·{" "}
                                </>
                            ) : null}
                            {result.ac_clauses.length} acceptance{" "}
                            {result.ac_clauses.length === 1 ? "criterion" : "criteria"}
                            {result.ac_clauses.length > 0
                                ? ` (${result.ac_clauses.join(", ")})`
                                : ""}
                            , sent unchanged to both models.
                        </p>
                    </div>

                    <div className="grid gap-4 lg:grid-cols-2">
                        {result.results.map((r) => (
                            <ResultColumn key={r.configured_provider} result={r} />
                        ))}
                    </div>
                </>
            ) : null}
        </div>
    );
}
