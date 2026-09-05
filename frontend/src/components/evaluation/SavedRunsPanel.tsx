"use client";

import React, { useState } from "react";
import {
    AlertCircle,
    AlertTriangle,
    BarChart3,
    CheckCircle2,
    Clock,
    Database,
    Trash2,
} from "lucide-react";
import {
    useBatchComparison,
    useDeleteRun,
    useEvaluationReport,
    useEvaluationRuns,
} from "@/lib/hooks/useModelComparison";
import type { ProviderMetrics, RunSummary } from "@/lib/types/evaluation";
import { ConfirmModal } from "@/components/ui/confirm-modal";
import { Button } from "@/components/ui/button";
import { Field, Input, Textarea } from "@/components/ui/field";
import { InlineLoader, SkeletonRows } from "@/components/ui/loaders";
import { useToast } from "@/components/ui/toast";
import { cn } from "@/lib/utils";

const PROVIDER_LABELS: Record<string, string> = {
    general_llm: "General LLM",
    fine_tuned: "Fine-tuned",
};

const GROUP_LABELS: Record<string, string> = {
    on_domain: "On domain",
    off_domain: "Off domain",
};

function pct(v: number | null): string {
    return v === null || v === undefined ? "—" : `${Math.round(v * 100)}%`;
}

function num(v: number | null, suffix = ""): string {
    return v === null || v === undefined ? "—" : `${v.toFixed(2)}${suffix}`;
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

function MetricRow({ metrics }: { metrics: ProviderMetrics }) {
    return (
        <tr className="border-t border-rule">
            <td className="py-2 pr-3 text-xs font-medium text-foreground">
                {PROVIDER_LABELS[metrics.provider] ?? metrics.provider}
            </td>
            <td className="py-2 pr-3 text-right font-mono text-xs tabular-nums text-muted-foreground">
                {metrics.items_scored}
            </td>
            <td className="py-2 pr-3 text-right font-mono text-xs tabular-nums text-foreground">
                {pct(metrics.coverage)}
            </td>
            <td className="py-2 pr-3 text-right font-mono text-xs tabular-nums text-foreground">
                {pct(metrics.duplicate_rate)}
            </td>
            <td className="py-2 pr-3 text-right font-mono text-xs tabular-nums text-muted-foreground">
                {num(metrics.latency_seconds, "s")}
            </td>
            <td className="py-2 text-right font-mono text-xs tabular-nums text-muted-foreground">
                {metrics.degraded_excluded > 0 ? (
                    <span className="text-pending-ink">{metrics.degraded_excluded}</span>
                ) : (
                    "0"
                )}
            </td>
        </tr>
    );
}

function RunRow({
    run,
    onDelete,
    onSelect,
    selected,
}: {
    run: RunSummary;
    onDelete: (run: RunSummary) => void;
    onSelect: (runId: string) => void;
    selected: boolean;
}) {
    return (
        <li
            className={cn(
                "gutter-rule flex items-center justify-between gap-3 rounded-md border transition-colors",
                selected
                    ? "border-pass/40 bg-pass-soft/50"
                    : "border-rule bg-card hover:bg-muted/40"
            )}
            data-signal={selected ? "pass" : ""}
        >
            <button
                type="button"
                onClick={() => onSelect(run.run_id)}
                aria-pressed={selected}
                className="flex min-w-0 flex-1 flex-col items-start px-3 py-2 text-left"
            >
                <span className="truncate font-mono text-[0.8125rem] font-semibold text-foreground">
                    {run.run_id}
                </span>
                <span className="text-[11px] text-muted-foreground">
                    {run.item_count} ticket{run.item_count === 1 ? "" : "s"} ·{" "}
                    {run.row_count} rows
                    {run.degraded_rows > 0 ? (
                        <span className="text-pending-ink"> · {run.degraded_rows} degraded</span>
                    ) : null}
                    {run.last_created_at
                        ? ` · ${new Date(run.last_created_at).toLocaleDateString()}`
                        : ""}
                </span>
            </button>
            <button
                type="button"
                onClick={() => onDelete(run)}
                aria-label={`Delete run ${run.run_id}`}
                className="mr-2 rounded p-1.5 text-muted-foreground transition-colors hover:bg-fail-soft hover:text-fail-ink"
            >
                <Trash2 className="h-4 w-4" aria-hidden="true" />
            </button>
        </li>
    );
}

export default function SavedRunsPanel() {
    const batch = useBatchComparison();
    const runs = useEvaluationRuns();
    const deleteRun = useDeleteRun();
    const toast = useToast();

    const [tickets, setTickets] = useState("");
    const [runId, setRunId] = useState("");
    const [validation, setValidation] = useState<string | null>(null);
    const [selectedRun, setSelectedRun] = useState<string | undefined>(undefined);
    const [pendingDelete, setPendingDelete] = useState<RunSummary | null>(null);

    const report = useEvaluationReport(selectedRun);

    const parsedTickets = tickets
        .split(/[\s,]+/)
        .map((t) => t.trim())
        .filter(Boolean);

    const handleSubmit = (e: React.FormEvent) => {
        e.preventDefault();
        setValidation(null);

        if (parsedTickets.length === 0) {
            setValidation("Enter at least one Jira ticket ID.");
            return;
        }
        if (!runId.trim()) {
            setValidation("Give this run a name so its results can be grouped.");
            return;
        }
        batch.mutate(
            { jira_ticket_ids: parsedTickets, run_id: runId.trim() },
            {
                onSuccess: (data) => {
                    const failed = data.requested - data.saved;
                    if (failed > 0) {
                        toast.warning(`Saved ${data.saved} of ${data.requested}`, {
                            description: `${failed} ticket${failed === 1 ? "" : "s"} did not save — see the list below.`,
                        });
                    } else {
                        toast.success(`Saved ${data.saved} into ${data.run_id}`, {
                            description: "The report below now includes this run.",
                        });
                    }
                },
                onError: (error) => {
                    toast.error("Run failed", {
                        description: error?.message ?? "Nothing was saved.",
                    });
                },
            }
        );
    };

    return (
        <div className="space-y-6">
            {/* --- Batch form --- */}
            <form
                onSubmit={handleSubmit}
                className="space-y-4 rounded-lg border border-rule bg-card p-4"
            >
                <h2 className="flex items-center gap-2 text-sm">
                    <Database className="h-4 w-4 text-keyword" aria-hidden="true" />
                    Run and save a comparison
                </h2>

                <Field
                    label="Jira tickets"
                    htmlFor="tickets"
                    description={`Separate with commas, spaces or new lines.${
                        parsedTickets.length > 0
                            ? ` ${parsedTickets.length} ticket${parsedTickets.length === 1 ? "" : "s"} detected.`
                            : ""
                    }`}
                >
                    <Textarea
                        id="tickets"
                        mono
                        rows={3}
                        value={tickets}
                        onChange={(e) => setTickets(e.target.value)}
                        placeholder="PROJ-101, PROJ-102, PROJ-103"
                        className="text-xs"
                    />
                </Field>

                <Field
                    label="Run name"
                    htmlFor="runId"
                    description="Reusing a name adds to that run. Re-running the same ticket replaces its rows rather than duplicating them."
                >
                    <Input
                        id="runId"
                        type="text"
                        mono
                        value={runId}
                        onChange={(e) => setRunId(e.target.value)}
                        placeholder="sprint-24-batch-1"
                    />
                </Field>

                {validation ? <ErrorNote message={validation} /> : null}
                {batch.isError ? (
                    <ErrorNote message={batch.error?.message ?? "The run failed."} />
                ) : null}

                <Button type="submit" disabled={batch.isPending} loading={batch.isPending}>
                    {!batch.isPending && <Database className="h-4 w-4" aria-hidden="true" />}
                    {batch.isPending
                        ? `Running ${parsedTickets.length} ticket${parsedTickets.length === 1 ? "" : "s"}…`
                        : "Run and save"}
                </Button>

                {batch.isPending ? (
                    <p className="flex items-start gap-1.5 text-xs leading-relaxed text-muted-foreground">
                        <Clock className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                        Each ticket runs both models in sequence. Rows are saved as
                        they finish, so stopping early keeps what is already done.
                    </p>
                ) : null}

                {batch.data ? (
                    <div className="rounded-md border border-rule bg-surface-raised p-3">
                        <p className="text-xs font-medium text-foreground">
                            Saved {batch.data.saved} of {batch.data.requested} into{" "}
                            <span className="font-mono">{batch.data.run_id}</span>
                        </p>
                        <ul className="mt-2 space-y-1">
                            {batch.data.items.map((it) => (
                                <li
                                    key={it.jira_ticket_id}
                                    className="flex items-start gap-1.5 text-[11px]"
                                >
                                    {it.saved ? (
                                        <CheckCircle2
                                            className="mt-0.5 h-3 w-3 shrink-0 text-pass"
                                            aria-hidden="true"
                                        />
                                    ) : (
                                        <AlertCircle
                                            className="mt-0.5 h-3 w-3 shrink-0 text-fail"
                                            aria-hidden="true"
                                        />
                                    )}
                                    <span className="font-mono text-foreground">
                                        {it.jira_ticket_id}
                                    </span>
                                    <span className="text-muted-foreground">
                                        {it.saved
                                            ? `${it.ac_clause_count} criteria${it.comparable ? "" : " · not comparable"}`
                                            : it.error}
                                    </span>
                                </li>
                            ))}
                        </ul>
                    </div>
                ) : null}
            </form>

            {/* --- Saved runs --- */}
            <section className="rounded-lg border border-rule bg-card p-4">
                <div className="flex items-center justify-between gap-3">
                    <h2 className="text-sm">Saved runs</h2>
                    <button
                        type="button"
                        onClick={() => setSelectedRun(undefined)}
                        aria-pressed={selectedRun === undefined}
                        className={cn(
                            "font-mono text-xs font-semibold transition-colors",
                            selectedRun === undefined
                                ? "text-pass"
                                : "text-muted-foreground hover:text-foreground"
                        )}
                    >
                        All runs combined
                    </button>
                </div>

                {runs.isLoading ? (
                    <SkeletonRows rows={2} className="mt-3" />
                ) : runs.isError ? (
                    <div className="mt-3">
                        <ErrorNote message="Could not load saved runs." />
                    </div>
                ) : !runs.data || runs.data.length === 0 ? (
                    <p className="mt-3 text-xs text-muted-foreground">
                        No saved runs yet. Run a comparison above to record one.
                    </p>
                ) : (
                    <ul className="mt-3 space-y-2">
                        {runs.data.map((run) => (
                            <RunRow
                                key={run.run_id}
                                run={run}
                                selected={selectedRun === run.run_id}
                                onSelect={setSelectedRun}
                                onDelete={setPendingDelete}
                            />
                        ))}
                    </ul>
                )}
            </section>

            {/* --- Report --- */}
            <section className="rounded-lg border border-rule bg-card p-4">
                <h2 className="flex items-center gap-2 text-sm">
                    <BarChart3 className="h-4 w-4 text-keyword" aria-hidden="true" />
                    {selectedRun ? `Report — ${selectedRun}` : "Report — all runs"}
                </h2>

                {report.isLoading ? (
                    <InlineLoader className="mt-3">Scoring</InlineLoader>
                ) : !report.data ? (
                    <p className="mt-3 text-xs text-muted-foreground">
                        Nothing saved yet for this selection.
                    </p>
                ) : (
                    <>
                        <p className="mt-1.5 font-mono text-xs text-muted-foreground">
                            {report.data.item_count} ticket
                            {report.data.item_count === 1 ? "" : "s"} ·{" "}
                            {report.data.row_count} rows across{" "}
                            {report.data.run_ids.length} run
                            {report.data.run_ids.length === 1 ? "" : "s"}
                        </p>

                        {report.data.notes.length > 0 ? (
                            <div
                                className="gutter-rule mt-3 flex items-start gap-2 rounded-md border border-pending/30 bg-pending-soft/60 px-3 py-2"
                                data-signal="pending"
                            >
                                <AlertTriangle
                                    className="mt-0.5 h-3.5 w-3.5 shrink-0 text-pending-ink"
                                    aria-hidden="true"
                                />
                                <ul className="space-y-0.5 text-xs text-foreground">
                                    {report.data.notes.map((n, i) => (
                                        <li key={i}>{n}</li>
                                    ))}
                                </ul>
                            </div>
                        ) : null}

                        {report.data.groups.map((group) => (
                            <div key={group.domain_group} className="mt-4">
                                <h3 className="eyebrow text-muted-foreground">
                                    {GROUP_LABELS[group.domain_group] ?? group.domain_group}
                                </h3>
                                <div className="scrollbar-thin mt-1.5 overflow-x-auto">
                                    <table className="w-full min-w-[30rem]">
                                        <thead>
                                            <tr className="text-muted-foreground">
                                                <th className="eyebrow pb-1.5 text-left">Model</th>
                                                <th className="eyebrow pb-1.5 text-right">Scored</th>
                                                <th className="eyebrow pb-1.5 text-right">Coverage</th>
                                                <th className="eyebrow pb-1.5 text-right">Duplicates</th>
                                                <th className="eyebrow pb-1.5 text-right">Time</th>
                                                <th className="eyebrow pb-1.5 text-right">Excluded</th>
                                            </tr>
                                        </thead>
                                        <tbody>
                                            {group.providers.map((p) => (
                                                <MetricRow key={p.provider} metrics={p} />
                                            ))}
                                        </tbody>
                                    </table>
                                </div>
                            </div>
                        ))}

                        <p className="mt-4 text-[11px] leading-relaxed text-muted-foreground">
                            Coverage and duplicates must be read together: a model can
                            reach full coverage by writing the same scenario for every
                            criterion. &ldquo;Excluded&rdquo; counts rows answered by a
                            different model than the one configured — those are left out
                            of every average.
                        </p>
                    </>
                )}
            </section>

            <ConfirmModal
                open={pendingDelete !== null}
                destructive
                title="Delete this run?"
                description={
                    pendingDelete
                        ? `All ${pendingDelete.row_count} saved rows for "${pendingDelete.run_id}" will be removed. This cannot be undone.`
                        : ""
                }
                confirmLabel="Delete"
                onConfirm={() => {
                    if (pendingDelete) {
                        const name = pendingDelete.run_id;
                        deleteRun.mutate(name, {
                            onSuccess: () =>
                                toast.success("Run deleted", {
                                    description: `${name} is no longer in the report.`,
                                }),
                            onError: () =>
                                toast.error("Could not delete that run", {
                                    description: "It is still listed. Try again.",
                                }),
                        });
                    }
                    if (pendingDelete && selectedRun === pendingDelete.run_id) {
                        setSelectedRun(undefined);
                    }
                    setPendingDelete(null);
                }}
                onCancel={() => setPendingDelete(null)}
            />
        </div>
    );
}
