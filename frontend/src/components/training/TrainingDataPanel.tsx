"use client";

import React, { useRef, useState } from "react";
import {
    AlertCircle,
    CheckCircle2,
    Download,
    FileCode2,
    Loader2,
    Trash2,
    Upload,
} from "lucide-react";
import {
    useDeleteTrainingDataset,
    useDownloadSampleDataset,
    useTrainingDatasets,
    useUploadTrainingDatasets,
} from "@/lib/hooks/useTrainingData";
import type { TrainingDataset } from "@/lib/types/training";
import { ConfirmModal } from "@/components/ui/confirm-modal";
import { Button } from "@/components/ui/button";
import { SkeletonRows } from "@/components/ui/loaders";
import { useToast } from "@/components/ui/toast";

const MAX_BYTES = 10 * 1024 * 1024; // 10 MB — matches backend _MAX_UPLOAD_BYTES
const ACCEPT_RE = /\.(feature|jsonl)$/i;

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

export default function TrainingDataPanel() {
    const { data: datasets, isLoading, isError } = useTrainingDatasets();
    const upload = useUploadTrainingDatasets();
    const deleteDataset = useDeleteTrainingDataset();
    const downloadSample = useDownloadSampleDataset();
    const toast = useToast();

    const handleDownloadSample = (kind: "jsonl" | "feature") => {
        downloadSample.mutate(kind, {
            onError: () => {
                toast.error("Could not download the sample", {
                    description: "The server did not respond. Try again.",
                });
            },
        });
    };

    const [files, setFiles] = useState<File[]>([]);
    const [validation, setValidation] = useState<string | null>(null);
    const [pendingDelete, setPendingDelete] = useState<TrainingDataset | null>(null);
    const fileInputRef = useRef<HTMLInputElement>(null);

    const handleSubmit = (e: React.FormEvent) => {
        e.preventDefault();
        if (files.length === 0) {
            setValidation("Choose at least one .feature or .jsonl file.");
            return;
        }
        const badName = files.find((f) => !ACCEPT_RE.test(f.name));
        if (badName) {
            setValidation(`${badName.name}: only .feature and .jsonl files are supported.`);
            return;
        }
        const tooBig = files.find((f) => f.size > MAX_BYTES);
        if (tooBig) {
            setValidation(`${tooBig.name} is too large. Maximum size is 10 MB.`);
            return;
        }
        setValidation(null);
        upload.mutate(files, {
            onSuccess: (result) => {
                // Clear the selection so a second click cannot re-upload the
                // same batch — that would create duplicate rows, duplicate
                // stored objects, and duplicate training pairs.
                if (result.accepted.length > 0) {
                    setFiles([]);
                    if (fileInputRef.current) fileInputRef.current.value = "";
                    toast.success(
                        `Accepted ${result.accepted.length} file${
                            result.accepted.length === 1 ? "" : "s"
                        }`,
                        {
                            description: result.rejected.length
                                ? `${result.rejected.length} rejected — see the reasons below.`
                                : "They will be included the next time a training set is built.",
                        }
                    );
                } else {
                    toast.error("Nothing was accepted", {
                        description: "Every file was rejected. See the reasons below.",
                    });
                }
            },
            onError: () => {
                toast.error("Upload failed", {
                    description: "The server did not accept the batch. Try again.",
                });
            },
        });
    };

    const confirmDelete = () => {
        if (!pendingDelete) return;
        const name = pendingDelete.filename;
        deleteDataset.mutate(pendingDelete.id, {
            onSuccess: () => {
                toast.success("Dataset deleted", {
                    description: `${name} will not be included in future training sets.`,
                });
            },
            onError: () => {
                toast.error("Could not delete that dataset", {
                    description: "It is still listed. Try again.",
                });
            },
            onSettled: () => setPendingDelete(null),
        });
    };

    return (
        <div className="flex flex-col gap-5">
            {/* Upload */}
            <form
                onSubmit={handleSubmit}
                className="flex flex-col gap-3 rounded-lg border border-rule bg-card p-4"
            >
                <div className="flex items-center gap-2">
                    <Upload className="h-4 w-4 text-keyword" aria-hidden="true" />
                    <h2 className="text-sm">Upload training data</h2>
                </div>
                <p className="text-xs leading-relaxed text-muted-foreground">
                    Add <code className="font-mono text-foreground">.feature</code> files or a{" "}
                    <code className="font-mono text-foreground">.jsonl</code> dataset of
                    training pairs (max 10 MB each). Each file is checked with the same
                    rules the dataset builder applies, so anything accepted here is usable
                    for training.
                </p>

                {/* Samples sit above the file picker, not below it: the accepted
                    shape of a .jsonl pair is not guessable — the assistant side
                    has to be a JSON object encoded as a string — and a worked
                    example is faster to copy than the rules are to read. The
                    server generates these from the same constants that validate
                    an upload, so what downloads here is always accepted here. */}
                <div className="flex flex-wrap items-center gap-2 rounded-md border border-dashed border-rule bg-surface-raised/60 px-3 py-2.5">
                    <span className="mr-1 text-xs text-muted-foreground">
                        Not sure of the format? Start from a sample:
                    </span>
                    <Button
                        type="button"
                        size="sm"
                        variant="outline"
                        onClick={() => handleDownloadSample("jsonl")}
                        disabled={downloadSample.isPending}
                    >
                        <Download className="h-3.5 w-3.5" aria-hidden="true" />
                        sample.jsonl
                    </Button>
                    <Button
                        type="button"
                        size="sm"
                        variant="outline"
                        onClick={() => handleDownloadSample("feature")}
                        disabled={downloadSample.isPending}
                    >
                        <Download className="h-3.5 w-3.5" aria-hidden="true" />
                        sample.feature
                    </Button>
                </div>

                <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
                    <input
                        ref={fileInputRef}
                        type="file"
                        multiple
                        accept=".feature,.jsonl"
                        aria-label="Training data files"
                        onChange={(e) => {
                            setFiles(Array.from(e.target.files ?? []));
                            setValidation(null);
                            upload.reset();
                        }}
                        disabled={upload.isPending}
                        className="min-w-0 flex-1 text-[0.8125rem] text-muted-foreground file:mr-3 file:rounded file:border file:border-rule file:bg-surface file:px-3 file:py-1.5 file:font-mono file:text-xs file:font-semibold file:text-foreground hover:file:bg-muted"
                    />
                    <Button type="submit" disabled={upload.isPending} loading={upload.isPending}>
                        {upload.isPending ? "Uploading…" : "Upload"}
                    </Button>
                </div>

                {validation && <ErrorNote message={validation} />}
                {upload.isError && <ErrorNote message="Upload failed. Please try again." />}

                {/* Per-file outcomes */}
                {upload.data && upload.data.accepted.length > 0 && (
                    <div
                        className="gutter-rule flex items-start gap-2 rounded-md border border-pass/30 bg-pass-soft/60 px-3 py-2"
                        data-signal="pass"
                    >
                        <CheckCircle2
                            className="mt-0.5 h-3.5 w-3.5 shrink-0 text-pass-ink"
                            aria-hidden="true"
                        />
                        <span className="text-xs text-foreground">
                            Accepted {upload.data.accepted.length} file
                            {upload.data.accepted.length === 1 ? "" : "s"}:{" "}
                            {upload.data.accepted
                                .map((a) => `${a.filename} (${a.item_count})`)
                                .join(", ")}
                        </span>
                    </div>
                )}
                {upload.data && upload.data.rejected.length > 0 && (
                    <ul className="flex flex-col gap-2">
                        {upload.data.rejected.map((r) => (
                            <li key={r.filename}>
                                <ErrorNote message={`${r.filename}: ${r.reason}`} />
                            </li>
                        ))}
                    </ul>
                )}
            </form>

            {/* Uploaded datasets */}
            <div className="rounded-lg border border-rule bg-card p-4">
                <h2 className="mb-3 text-sm">Uploaded datasets</h2>
                {isLoading ? (
                    <SkeletonRows rows={2} />
                ) : isError ? (
                    <ErrorNote message="Could not load uploaded datasets." />
                ) : !datasets || datasets.length === 0 ? (
                    <p className="text-[0.8125rem] text-muted-foreground">Nothing uploaded yet.</p>
                ) : (
                    <ul className="flex flex-col gap-2">
                        {datasets.map((d) => (
                            <li
                                key={d.id}
                                className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-rule bg-surface-raised px-3 py-2"
                            >
                                <div className="flex min-w-0 items-center gap-2">
                                    <span className="shrink-0 rounded-full border border-keyword/30 bg-keyword-soft px-1.5 py-0.5 font-mono text-[10px] font-semibold uppercase text-keyword">
                                        {d.kind}
                                    </span>
                                    <FileCode2
                                        className="h-3.5 w-3.5 shrink-0 text-muted-foreground"
                                        aria-hidden="true"
                                    />
                                    <span className="truncate font-mono text-[0.8125rem] text-foreground">
                                        {d.filename}
                                    </span>
                                </div>
                                <div className="flex shrink-0 items-center gap-3 font-mono text-xs text-muted-foreground">
                                    <span>
                                        {d.item_count}{" "}
                                        {d.kind === "jsonl"
                                            ? `pair${d.item_count === 1 ? "" : "s"}`
                                            : `scenario${d.item_count === 1 ? "" : "s"}`}
                                    </span>
                                    <span>{new Date(d.created_at).toLocaleDateString()}</span>
                                    <button
                                        type="button"
                                        onClick={() => setPendingDelete(d)}
                                        disabled={
                                            deleteDataset.isPending &&
                                            deleteDataset.variables === d.id
                                        }
                                        aria-label={`Delete ${d.filename}`}
                                        title="Delete dataset"
                                        className="inline-flex h-7 w-7 items-center justify-center rounded border border-rule text-muted-foreground transition-colors hover:border-fail/40 hover:bg-fail-soft hover:text-fail-ink disabled:cursor-not-allowed disabled:opacity-50"
                                    >
                                        {deleteDataset.isPending &&
                                        deleteDataset.variables === d.id ? (
                                            <Loader2
                                                className="h-3.5 w-3.5 animate-spin"
                                                aria-hidden="true"
                                            />
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
                open={pendingDelete !== null}
                destructive
                title="Delete training dataset"
                description={
                    <>
                        Delete{" "}
                        <span className="font-semibold text-foreground">
                            {pendingDelete?.filename ?? "this file"}
                        </span>
                        ? It will no longer be included when a training set is built.
                        This can&apos;t be undone.
                    </>
                }
                confirmLabel="Delete"
                isConfirming={deleteDataset.isPending}
                onConfirm={confirmDelete}
                onCancel={() => setPendingDelete(null)}
            />
        </div>
    );
}
