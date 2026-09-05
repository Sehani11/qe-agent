"use client";

import React, { useState } from "react";
import {
    BookOpen,
    Code2,
    Database,
    ExternalLink,
    FileText,
    Loader2,
    AlertCircle,
    CheckCircle2,
    Trash2,
} from "lucide-react";
import {
    useCodeIndexStatus,
    useDeleteAllKnowledgeSources,
    useDeleteKnowledgeSource,
    useIndexCode,
    useIngestConfluence,
    useIngestDocument,
    useIngestJira,
    useKnowledgeSources,
} from "@/lib/hooks/useKnowledge";
import { useProject } from "@/lib/hooks/useProjects";
import { useActiveProjectId } from "@/lib/stores/projectStore";
import type { KnowledgeSource, KnowledgeSSEEvent } from "@/lib/types/knowledge";
import { ConfirmModal } from "@/components/ui/confirm-modal";
import { Button } from "@/components/ui/button";
import { Field, Input, Textarea } from "@/components/ui/field";
import { cn } from "@/lib/utils";
import { InlineLoader, SkeletonRows } from "@/components/ui/loaders";
import { useToast } from "@/components/ui/toast";

const JIRA_PROJECT_KEY_RE = /^[A-Z][A-Z0-9_]{0,9}$/;

/** Split a pasted block into refs. Tolerates CRLF and commas, drops blanks. */
function parseRefs(raw: string): string[] {
    return raw
        .split(/[\r\n,]+/)
        .map((line) => line.trim())
        .filter(Boolean);
}

/**
 * The same segmented control the verification source uses, so "pick how, then
 * fill in what" reads identically wherever the app asks it.
 */
function MethodSelector<T extends string>({
    label,
    value,
    options,
    onChange,
    disabled,
}: {
    label: string;
    value: T;
    options: { value: T; label: string }[];
    onChange: (value: T) => void;
    disabled?: boolean;
}) {
    return (
        <div>
            <p className="eyebrow mb-2 text-muted-foreground">Method</p>
            <div className="grid gap-2 sm:grid-cols-2" role="tablist" aria-label={label}>
                {options.map((option) => {
                    const active = option.value === value;
                    return (
                        <button
                            key={option.value}
                            type="button"
                            role="tab"
                            aria-selected={active}
                            disabled={disabled}
                            onClick={() => onChange(option.value)}
                            className={cn(
                                "rounded-md border px-3 py-2 text-[0.8125rem] font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-55",
                                active
                                    ? "border-pass/40 bg-pass-soft text-pass-ink"
                                    : "border-rule bg-surface text-muted-foreground hover:bg-muted hover:text-foreground"
                            )}
                        >
                            {option.label}
                        </button>
                    );
                })}
            </div>
        </div>
    );
}

type ConfluenceMethod = "space" | "urls";
type JiraMethod = "project" | "urls";
const DOC_MAX_BYTES = 10 * 1024 * 1024; // 10 MB — matches backend _MAX_DOC_BYTES
const DOC_ACCEPT_RE = /\.(pdf|docx)$/i;

/** Inline result note after an ingestion run. count === 0 is informational, not an error. */
function ResultNote({ count }: { count: number }) {
    if (count > 0) {
        return (
            <div
                className="gutter-rule flex items-start gap-2 rounded-md border border-pass/30 bg-pass-soft/60 px-3 py-2"
                data-signal="pass"
            >
                <CheckCircle2
                    className="mt-0.5 h-3.5 w-3.5 shrink-0 text-pass-ink"
                    aria-hidden="true"
                />
                <span className="text-xs text-foreground">
                    Ingested {count} item{count === 1 ? "" : "s"} into your knowledge base.
                </span>
            </div>
        );
    }
    return (
        <div
            className="gutter-rule flex items-start gap-2 rounded-md border border-pending/30 bg-pending-soft/60 px-3 py-2"
            data-signal="pending"
        >
            <AlertCircle
                className="mt-0.5 h-3.5 w-3.5 shrink-0 text-pending-ink"
                aria-hidden="true"
            />
            <span className="text-xs text-foreground">
                Nothing was ingested. Check that Pinecone and your Confluence/Jira
                credentials are configured on the backend.
            </span>
        </div>
    );
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

export default function KnowledgeBasePanel() {
    const { data: sources, isLoading: sourcesLoading, isError: sourcesError, refetch } =
        useKnowledgeSources();
    const deleteSource = useDeleteKnowledgeSource();
    const deleteAllSources = useDeleteAllKnowledgeSources();
    const [pendingDelete, setPendingDelete] = useState<KnowledgeSource | null>(null);
    // Separate from `pendingDelete` so the two confirmations can never be open
    // at once and can't be confused for one another.
    const [pendingDeleteAll, setPendingDeleteAll] = useState(false);
    const toast = useToast();

    const confirmDeleteAll = () => {
        deleteAllSources.mutate(undefined, {
            onSuccess: (deleted) => {
                toast.success(
                    `Deleted ${deleted} source${deleted === 1 ? "" : "s"}`,
                    {
                        description:
                            "Your knowledge base is empty. Verification and project Q&A will fall back to code only.",
                    }
                );
            },
            onError: () => {
                toast.error("Could not clear the knowledge base", {
                    description: "Your sources are still there. Try again.",
                });
            },
            onSettled: () => setPendingDeleteAll(false),
        });
    };

    const confirmDelete = () => {
        if (!pendingDelete) return;
        const name = pendingDelete.title ?? "the source";
        deleteSource.mutate(pendingDelete.id, {
            onSuccess: () => {
                toast.success("Source deleted", {
                    description: `${name} no longer feeds verification or project Q&A.`,
                });
            },
            onError: () => {
                toast.error("Could not delete that source", {
                    description: "It is still in your knowledge base. Try again.",
                });
            },
            onSettled: () => setPendingDelete(null),
        });
    };

    // Code index state. The repo field is seeded from the project's configured
    // repository, which is the one verification runs against — typing it again
    // is the common case and getting it wrong makes an index nothing can use.
    const activeProjectId = useActiveProjectId();
    const { data: project } = useProject(activeProjectId);
    const { data: codeIndex } = useCodeIndexStatus();
    const [codeRepo, setCodeRepo] = useState("");
    const [codeRef, setCodeRef] = useState("");
    const [codeValidation, setCodeValidation] = useState<string | null>(null);
    const [codeResult, setCodeResult] = useState<KnowledgeSSEEvent | null>(null);
    const codeIndexer = useIndexCode({
        onComplete: (_count, event) => setCodeResult(event),
    });

    const effectiveRepo = codeRepo.trim() || project?.github_repo?.trim() || "";

    const handleIndexCode = (e: React.FormEvent) => {
        e.preventDefault();
        setCodeValidation(null);
        setCodeResult(null);
        if (!effectiveRepo) {
            setCodeValidation(
                "Enter a repository, or set one in Project settings first."
            );
            return;
        }
        void codeIndexer.ingest({
            repo_url: effectiveRepo,
            ref: codeRef.trim() || "HEAD",
        });
    };

    // Confluence form state
    const [confluenceMethod, setConfluenceMethod] = useState<ConfluenceMethod>("space");
    const [spaceKey, setSpaceKey] = useState("");
    const [pageRefs, setPageRefs] = useState("");
    const [confluenceValidation, setConfluenceValidation] = useState<string | null>(null);
    const [confluenceCount, setConfluenceCount] = useState<number | null>(null);
    const confluence = useIngestConfluence({
        onComplete: (count) => {
            setConfluenceCount(count);
            if (count > 0) {
                toast.success("Confluence ingested", {
                    description: `${count} item${count === 1 ? "" : "s"} added to the knowledge base.`,
                });
            } else {
                toast.warning("Nothing was ingested", {
                    description: "Check the space key and your backend credentials.",
                });
            }
            void refetch();
        },
    });

    // Jira form state
    const [jiraMethod, setJiraMethod] = useState<JiraMethod>("project");
    const [ticketRefs, setTicketRefs] = useState("");
    const [projectKey, setProjectKey] = useState("");
    const [sprint, setSprint] = useState("");
    const [label, setLabel] = useState("");
    const [jiraValidation, setJiraValidation] = useState<string | null>(null);
    const [jiraCount, setJiraCount] = useState<number | null>(null);
    const jira = useIngestJira({
        onComplete: (count) => {
            setJiraCount(count);
            if (count > 0) {
                toast.success("Jira ingested", {
                    description: `${count} ticket${count === 1 ? "" : "s"} added to the knowledge base.`,
                });
            } else {
                toast.warning("Nothing was ingested", {
                    description: "Check the project key and your backend credentials.",
                });
            }
            void refetch();
        },
    });

    // Document upload state
    const [docFile, setDocFile] = useState<File | null>(null);
    const [docValidation, setDocValidation] = useState<string | null>(null);
    const doc = useIngestDocument({
        onComplete: (result) => {
            if (result) {
                toast.success("Document ingested", {
                    description: `${result.chunk_count} chunk${
                        result.chunk_count === 1 ? "" : "s"
                    } from ${result.title}.`,
                });
            }
            void refetch();
        },
    });

    const handleConfluenceSubmit = (e: React.FormEvent) => {
        e.preventDefault();
        setConfluenceCount(null);

        if (confluenceMethod === "urls") {
            const refs = parseRefs(pageRefs);
            if (refs.length === 0) {
                setConfluenceValidation("Add at least one page URL or page ID.");
                return;
            }
            setConfluenceValidation(null);
            void confluence.ingest({ page_refs: refs });
            return;
        }

        if (!spaceKey.trim()) {
            setConfluenceValidation("Provide a space key.");
            return;
        }
        setConfluenceValidation(null);
        void confluence.ingest({ space_key: spaceKey.trim() });
    };

    const handleJiraSubmit = (e: React.FormEvent) => {
        e.preventDefault();
        setJiraCount(null);

        if (jiraMethod === "urls") {
            const refs = parseRefs(ticketRefs);
            if (refs.length === 0) {
                setJiraValidation("Add at least one ticket URL or key.");
                return;
            }
            setJiraValidation(null);
            void jira.ingest({ ticket_refs: refs });
            return;
        }

        const normalized = projectKey.trim().toUpperCase();
        if (!JIRA_PROJECT_KEY_RE.test(normalized)) {
            setJiraValidation(
                "Project key must be 1–10 uppercase alphanumeric characters (e.g. PROJ)."
            );
            return;
        }
        setJiraValidation(null);
        void jira.ingest({
            project_key: normalized,
            sprint: sprint.trim() || undefined,
            label: label.trim() || undefined,
        });
    };

    const handleDocSubmit = (e: React.FormEvent) => {
        e.preventDefault();
        if (!docFile) {
            setDocValidation("Choose a .pdf or .docx file to upload.");
            return;
        }
        if (!DOC_ACCEPT_RE.test(docFile.name)) {
            setDocValidation("Only .pdf and .docx files are supported.");
            return;
        }
        if (docFile.size > DOC_MAX_BYTES) {
            setDocValidation("File is too large. Maximum size is 10 MB.");
            return;
        }
        setDocValidation(null);
        void doc.ingest(docFile);
    };

    return (
        <div className="flex flex-col gap-5">
            {/* Ingestion forms */}
            <div className="grid gap-5 lg:grid-cols-2">
                {/* Confluence */}
                <form
                    onSubmit={handleConfluenceSubmit}
                    className="flex flex-col gap-3 rounded-lg border border-rule bg-card p-4"
                >
                    <div className="flex items-center gap-2">
                        <BookOpen className="h-4 w-4 text-keyword" aria-hidden="true" />
                        <h2 className="text-sm">Confluence</h2>
                    </div>
                    <p className="text-xs leading-relaxed text-muted-foreground">
                        Ingest a whole space, or just the pages you name.
                    </p>

                    <MethodSelector<ConfluenceMethod>
                        label="Confluence ingestion method"
                        value={confluenceMethod}
                        onChange={setConfluenceMethod}
                        disabled={confluence.isIngesting}
                        options={[
                            { value: "space", label: "Whole space" },
                            { value: "urls", label: "Page URLs" },
                        ]}
                    />

                    {confluenceMethod === "space" ? (
                        <Field
                            label="Space key"
                            htmlFor="kb-space-key"
                            description="Every page in the space is ingested."
                        >
                            <Input
                                id="kb-space-key"
                                mono
                                placeholder="e.g. ENG"
                                value={spaceKey}
                                onChange={(e) => setSpaceKey(e.target.value)}
                                disabled={confluence.isIngesting}
                            />
                        </Field>
                    ) : (
                        <Field
                            label="Page URLs or IDs"
                            htmlFor="kb-page-refs"
                            description="One per line. Full page URLs or bare numeric IDs both work; short /wiki/x/ links do not carry an ID."
                        >
                            <Textarea
                                id="kb-page-refs"
                                mono
                                rows={4}
                                spellCheck={false}
                                autoComplete="off"
                                placeholder={"https://acme.atlassian.net/wiki/spaces/ENG/pages/123456/Auth+Design\n123457"}
                                value={pageRefs}
                                onChange={(e) => setPageRefs(e.target.value)}
                                disabled={confluence.isIngesting}
                                className="text-[0.8125rem]"
                            />
                        </Field>
                    )}
                    <Button
                        type="submit"
                        disabled={confluence.isIngesting}
                        loading={confluence.isIngesting}
                        // Pinned to the bottom of the card so it lines up with
                        // the button in the card beside it. These are grid items
                        // and stretch to equal height, but Jira has an extra row
                        // (sprint/label), so without this the shorter card left
                        // its slack below the button instead of above it.
                        className="mt-auto"
                    >
                        {confluence.isIngesting ? "Ingesting…" : "Ingest Confluence"}
                    </Button>
                    {confluence.isIngesting && confluence.progressMessage && (
                        <InlineLoader>{confluence.progressMessage}</InlineLoader>
                    )}
                    {confluenceValidation && <ErrorNote message={confluenceValidation} />}
                    {confluence.error && <ErrorNote message={confluence.error} />}
                    {!confluence.isIngesting && confluenceCount !== null && !confluence.error && (
                        <ResultNote count={confluenceCount} />
                    )}
                </form>

                {/* Jira */}
                <form
                    onSubmit={handleJiraSubmit}
                    className="flex flex-col gap-3 rounded-lg border border-rule bg-card p-4"
                >
                    <div className="flex items-center gap-2">
                        <Database className="h-4 w-4 text-keyword" aria-hidden="true" />
                        <h2 className="text-sm">Jira workspace</h2>
                    </div>
                    <p className="text-xs leading-relaxed text-muted-foreground">
                        Ingest a whole project, or just the tickets you name.
                    </p>

                    <MethodSelector<JiraMethod>
                        label="Jira ingestion method"
                        value={jiraMethod}
                        onChange={setJiraMethod}
                        disabled={jira.isIngesting}
                        options={[
                            { value: "project", label: "Whole project" },
                            { value: "urls", label: "Ticket URLs" },
                        ]}
                    />

                    {jiraMethod === "urls" ? (
                        <Field
                            label="Ticket URLs or keys"
                            htmlFor="kb-ticket-refs"
                            description="One per line. Full issue URLs or bare keys like PROJ-142 both work."
                        >
                            <Textarea
                                id="kb-ticket-refs"
                                mono
                                rows={4}
                                spellCheck={false}
                                autoComplete="off"
                                placeholder={"https://acme.atlassian.net/browse/PROJ-142\nPROJ-143"}
                                value={ticketRefs}
                                onChange={(e) => setTicketRefs(e.target.value)}
                                disabled={jira.isIngesting}
                                className="text-[0.8125rem]"
                            />
                        </Field>
                    ) : (
                    <>
                    <Field label="Project key" htmlFor="kb-project-key">
                        <Input
                            id="kb-project-key"
                            mono
                            placeholder="e.g. PROJ"
                            value={projectKey}
                            onChange={(e) => setProjectKey(e.target.value)}
                            disabled={jira.isIngesting}
                        />
                    </Field>
                    <div className="grid grid-cols-2 gap-3">
                        <Field label="Sprint" htmlFor="kb-sprint">
                            <Input
                                id="kb-sprint"
                                placeholder="optional"
                                value={sprint}
                                onChange={(e) => setSprint(e.target.value)}
                                disabled={jira.isIngesting}
                            />
                        </Field>
                        <Field label="Label" htmlFor="kb-label">
                            <Input
                                id="kb-label"
                                placeholder="optional"
                                value={label}
                                onChange={(e) => setLabel(e.target.value)}
                                disabled={jira.isIngesting}
                            />
                        </Field>
                    </div>
                    </>
                    )}
                    <Button
                        type="submit"
                        disabled={jira.isIngesting}
                        loading={jira.isIngesting}
                        className="mt-auto"
                    >
                        {jira.isIngesting ? "Ingesting…" : "Ingest Jira"}
                    </Button>
                    {jira.isIngesting && jira.progressMessage && (
                        <InlineLoader>{jira.progressMessage}</InlineLoader>
                    )}
                    {jiraValidation && <ErrorNote message={jiraValidation} />}
                    {jira.error && <ErrorNote message={jira.error} />}
                    {!jira.isIngesting && jiraCount !== null && !jira.error && (
                        <ResultNote count={jiraCount} />
                    )}
                </form>
            </div>

            {/* Code index */}
            <form
                onSubmit={handleIndexCode}
                className="flex flex-col gap-3 rounded-lg border border-rule bg-card p-4"
            >
                <div className="flex items-center gap-2">
                    <Code2 className="h-4 w-4 text-keyword" aria-hidden="true" />
                    <h2 className="text-sm">Code index</h2>
                </div>
                <p className="text-xs leading-relaxed text-muted-foreground">
                    Index this project&rsquo;s source so verification can jump
                    straight to the files a scenario is likely implemented in,
                    instead of exploring to find them. Suggestions only &mdash;
                    the agent still reads each file before judging it.
                </p>

                {codeIndex?.indexed ? (
                    <p
                        className="text-xs text-muted-foreground"
                        data-testid="code-index-status"
                    >
                        Indexed {codeIndex.repo} at{" "}
                        <code className="font-mono text-foreground">
                            {(codeIndex.indexed_ref ?? "").slice(0, 7)}
                        </code>
                        {codeIndex.indexed_at
                            ? ` · ${new Date(codeIndex.indexed_at).toLocaleDateString()}`
                            : ""}
                        {codeIndex.file_count
                            ? ` · ${codeIndex.file_count} files`
                            : ""}
                        . Re-index after the branch moves &mdash; a stale index
                        is skipped rather than used.
                    </p>
                ) : (
                    <p
                        className="text-xs text-muted-foreground"
                        data-testid="code-index-status"
                    >
                        Not indexed yet.
                    </p>
                )}

                <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
                    <Field
                        label="Repository"
                        htmlFor="kb-code-repo"
                        className="min-w-0 flex-1"
                    >
                        <Input
                            id="kb-code-repo"
                            value={codeRepo}
                            onChange={(e) => setCodeRepo(e.target.value)}
                            placeholder={
                                project?.github_repo || "owner/repo"
                            }
                            disabled={codeIndexer.isIngesting}
                            spellCheck={false}
                            autoComplete="off"
                        />
                    </Field>
                    <Field
                        label="Branch or commit"
                        htmlFor="kb-code-ref"
                        className="sm:w-44"
                    >
                        <Input
                            id="kb-code-ref"
                            value={codeRef}
                            onChange={(e) => setCodeRef(e.target.value)}
                            placeholder="HEAD"
                            disabled={codeIndexer.isIngesting}
                            spellCheck={false}
                            autoComplete="off"
                        />
                    </Field>
                    <Button
                        type="submit"
                        disabled={codeIndexer.isIngesting}
                        loading={codeIndexer.isIngesting}
                    >
                        {codeIndexer.isIngesting
                            ? "Indexing…"
                            : codeIndex?.indexed
                              ? "Re-index code"
                              : "Index code"}
                    </Button>
                </div>

                {codeIndexer.isIngesting && codeIndexer.progressMessage && (
                    <InlineLoader>{codeIndexer.progressMessage}</InlineLoader>
                )}
                {codeValidation && <ErrorNote message={codeValidation} />}
                {codeIndexer.error && <ErrorNote message={codeIndexer.error} />}
                {!codeIndexer.isIngesting && codeResult && !codeIndexer.error && (
                    <div
                        className="gutter-rule flex items-start gap-2 rounded-md border border-pass/30 bg-pass-soft/60 px-3 py-2"
                        data-signal="pass"
                    >
                        <CheckCircle2
                            className="mt-0.5 h-3.5 w-3.5 shrink-0 text-pass-ink"
                            aria-hidden="true"
                        />
                        <span className="text-xs text-foreground">
                            Indexed {codeResult.indexed_files} file
                            {codeResult.indexed_files === 1 ? "" : "s"} (
                            {codeResult.chunks} chunks) at{" "}
                            {(codeResult.sha ?? "").slice(0, 7)}.
                        </span>
                    </div>
                )}
            </form>

            {/* Document upload */}
            <form
                onSubmit={handleDocSubmit}
                className="flex flex-col gap-3 rounded-lg border border-rule bg-card p-4"
            >
                <div className="flex items-center gap-2">
                    <FileText className="h-4 w-4 text-keyword" aria-hidden="true" />
                    <h2 className="text-sm">Documents</h2>
                </div>
                <p className="text-xs leading-relaxed text-muted-foreground">
                    Upload a PDF or DOCX file (max 10 MB) to add it to the knowledge base.
                </p>
                <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
                    <input
                        type="file"
                        accept=".pdf,.docx"
                        aria-label="Document file"
                        onChange={(e) => {
                            setDocFile(e.target.files?.[0] ?? null);
                            setDocValidation(null);
                            doc.reset(); // clear any prior success/error note
                        }}
                        disabled={doc.isUploading}
                        className="min-w-0 flex-1 text-[0.8125rem] text-muted-foreground file:mr-3 file:rounded file:border file:border-rule file:bg-surface file:px-3 file:py-1.5 file:font-mono file:text-xs file:font-semibold file:text-foreground hover:file:bg-muted"
                    />
                    <Button type="submit" disabled={doc.isUploading} loading={doc.isUploading}>
                        {doc.isUploading ? "Uploading…" : "Upload document"}
                    </Button>
                </div>
                {docValidation && <ErrorNote message={docValidation} />}
                {doc.error && <ErrorNote message={doc.error} />}
                {!doc.isUploading && doc.result && !doc.error && (
                    <div
                        className="gutter-rule flex items-start gap-2 rounded-md border border-pass/30 bg-pass-soft/60 px-3 py-2"
                        data-signal="pass"
                    >
                        <CheckCircle2
                            className="mt-0.5 h-3.5 w-3.5 shrink-0 text-pass-ink"
                            aria-hidden="true"
                        />
                        <span className="text-xs text-foreground">
                            Ingested {doc.result.chunk_count} chunk
                            {doc.result.chunk_count === 1 ? "" : "s"} from {doc.result.title}.
                        </span>
                    </div>
                )}
            </form>

            {/* Ingested sources list */}
            <div className="rounded-lg border border-rule bg-card p-4">
                <div className="mb-3 flex items-center justify-between gap-3">
                    <h2 className="text-sm">Ingested sources</h2>
                    {/* Hidden when the list is empty — an action that cannot do
                        anything is noise, and it also can't be mistaken for a
                        way to delete something else on the page. */}
                    {sources && sources.length > 0 && (
                        <Button
                            type="button"
                            variant="destructive"
                            size="sm"
                            onClick={() => setPendingDeleteAll(true)}
                            disabled={deleteAllSources.isPending}
                            loading={deleteAllSources.isPending}
                        >
                            {!deleteAllSources.isPending && (
                                <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
                            )}
                            Delete all
                        </Button>
                    )}
                </div>
                {sourcesLoading ? (
                    <SkeletonRows rows={2} />
                ) : sourcesError ? (
                    <ErrorNote message="Could not load ingested sources." />
                ) : !sources || sources.length === 0 ? (
                    <p className="text-[0.8125rem] text-muted-foreground">
                        No knowledge sources ingested yet.
                    </p>
                ) : (
                    <ul className="flex flex-col gap-2">
                        {sources.map((s) => (
                            <li
                                key={s.id}
                                className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-rule bg-surface-raised px-3 py-2"
                            >
                                <div className="flex min-w-0 items-center gap-2">
                                    <span className="shrink-0 rounded-full border border-keyword/30 bg-keyword-soft px-1.5 py-0.5 font-mono text-[10px] font-semibold uppercase text-keyword">
                                        {s.source_type}
                                    </span>
                                    {s.source_url ? (
                                        <a
                                            href={s.source_url}
                                            target="_blank"
                                            rel="noopener noreferrer"
                                            className="inline-flex min-w-0 items-center gap-1 truncate text-[0.8125rem] font-medium text-foreground underline decoration-rule-strong underline-offset-2 hover:decoration-foreground"
                                        >
                                            <span className="truncate">{s.title ?? s.source_url}</span>
                                            <ExternalLink className="h-3 w-3 shrink-0" aria-hidden="true" />
                                        </a>
                                    ) : (
                                        <span className="truncate text-[0.8125rem] text-foreground">
                                            {s.title ?? "(untitled)"}
                                        </span>
                                    )}
                                </div>
                                <div className="flex shrink-0 items-center gap-3 font-mono text-xs text-muted-foreground">
                                    <span>
                                        {s.page_count} page{s.page_count === 1 ? "" : "s"}
                                    </span>
                                    <span>{s.ingestion_status}</span>
                                    <span>{new Date(s.created_at).toLocaleDateString()}</span>
                                    <button
                                        type="button"
                                        onClick={() => setPendingDelete(s)}
                                        disabled={
                                            deleteSource.isPending &&
                                            deleteSource.variables === s.id
                                        }
                                        aria-label={`Delete ${s.title ?? "source"}`}
                                        title="Delete source"
                                        className="inline-flex h-7 w-7 items-center justify-center rounded border border-rule text-muted-foreground transition-colors hover:border-fail/40 hover:bg-fail-soft hover:text-fail-ink disabled:cursor-not-allowed disabled:opacity-50"
                                    >
                                        {deleteSource.isPending &&
                                        deleteSource.variables === s.id ? (
                                            <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
                                        ) : (
                                            <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
                                        )}
                                    </button>
                                </div>
                            </li>
                        ))}
                    </ul>
                )}
            </div>

            <ConfirmModal
                open={pendingDeleteAll}
                destructive
                title="Delete all knowledge sources"
                description={
                    <>
                        Delete{" "}
                        <span className="font-semibold text-foreground">
                            all {sources?.length ?? 0} source
                            {(sources?.length ?? 0) === 1 ? "" : "s"}
                        </span>{" "}
                        from your knowledge base? Verification and project Q&amp;A will
                        fall back to code only until you ingest something again. This
                        can&apos;t be undone.
                    </>
                }
                confirmLabel="Delete all"
                isConfirming={deleteAllSources.isPending}
                onConfirm={confirmDeleteAll}
                onCancel={() => setPendingDeleteAll(false)}
            />

            <ConfirmModal
                open={pendingDelete !== null}
                destructive
                title="Delete knowledge source"
                description={
                    <>
                        Delete{" "}
                        <span className="font-semibold text-foreground">
                            {pendingDelete?.title ?? "this source"}
                        </span>{" "}
                        from your knowledge base? Its content will no longer be used in
                        verification or project Q&amp;A. This can&apos;t be undone.
                    </>
                }
                confirmLabel="Delete"
                isConfirming={deleteSource.isPending}
                onConfirm={confirmDelete}
                onCancel={() => setPendingDelete(null)}
            />
        </div>
    );
}
