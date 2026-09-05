/**
 * Types for manual training-dataset upload (Story 6.7).
 *
 * Fields are snake_case because the API returns them that way — the axios
 * client only attaches the auth header, it does not transform keys.
 */

/** One uploaded training corpus file. */
export interface TrainingDataset {
    id: string;
    filename: string;
    /** "feature" (Gherkin) or "jsonl" (ready-made training pairs). */
    kind: string;
    /** Scenarios for a .feature file, pairs for a .jsonl file. */
    item_count: number;
    storage_path: string;
    created_at: string;
}

/** A file the backend would not accept, and why. */
export interface RejectedFile {
    filename: string;
    /** Shown verbatim — for JSONL failures it names the offending line. */
    reason: string;
}

/** Per-file outcomes for one upload request. */
export interface TrainingUploadResponse {
    accepted: TrainingDataset[];
    rejected: RejectedFile[];
}

/**
 * Where a run is. The first four are active; a client polls until it sees one
 * of the last two, which is the only thing that stops the polling.
 */
export type TrainingRunStatus =
    | "queued"
    | "building"
    | "training"
    | "fetching"
    | "completed"
    | "failed";

/** Statuses a run can still leave under its own power. */
export const ACTIVE_RUN_STATUSES: readonly TrainingRunStatus[] = [
    "queued",
    "building",
    "training",
    "fetching",
];

export function isRunActive(status: TrainingRunStatus): boolean {
    return ACTIVE_RUN_STATUSES.includes(status);
}

/** One fine-tuning run: uploads -> dataset -> Kaggle GPU -> LoRA adapter. */
export interface TrainingRun {
    id: string;
    status: TrainingRunStatus;
    /** One line of context for `status`. Shown verbatim. */
    detail: string | null;
    train_pairs: number;
    holdout_pairs: number;
    /** Kaggle kernel as "owner/slug", once the push lands. */
    kernel_ref: string | null;
    /** Whether the download button has anything to fetch. */
    has_model: boolean;
    /** Accumulated stage output, capped server-side. */
    log: string;
    created_at: string;
    completed_at: string | null;
}

/**
 * Whether this server can start a run at all.
 *
 * Asked before the button is shown: both blockers — no training/ directory, no
 * Kaggle credentials — are operator configuration a user cannot fix from here,
 * so an explanation beats a button that always fails.
 */
export interface TrainingReadiness {
    can_train: boolean;
    /** Why a run cannot start. Null when `can_train`, or when one is simply running. */
    reason: string | null;
    active_run_id: string | null;
    /**
     * Uploaded files needed before a run can produce anything.
     *
     * Two, because the builder splits train/holdout by origin FILE and always
     * reserves at least one origin for the holdout — so a single file, however
     * many pairs it holds, becomes the holdout and leaves train empty. Sent by
     * the server rather than assumed here, since the number follows from how
     * the split works.
     */
    min_datasets: number;
}
