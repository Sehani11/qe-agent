"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import apiClient from "@/lib/api/client";
import { FINE_TUNED_STATUS_KEY } from "@/lib/hooks/useFineTunedStatus";
import { filenameFromHeader, triggerBlobDownload } from "@/lib/download";
import type {
    ServingReadiness,
    TrainingDataset,
    TrainingReadiness,
    TrainingRun,
    TrainingUploadResponse,
} from "@/lib/types/training";
import { isRunActive } from "@/lib/types/training";

const QUERY_KEY = ["training", "datasets"];
const RUNS_KEY = ["training", "runs"];
const READINESS_KEY = ["training", "readiness"];
const SERVING_READINESS_KEY = ["training", "serving-readiness"];

/**
 * Fetch the authenticated user's uploaded training datasets (Story 6.7).
 */
export function useTrainingDatasets() {
    return useQuery({
        queryKey: QUERY_KEY,
        queryFn: async () => {
            const { data } = await apiClient.get<TrainingDataset[]>(
                "/training/datasets"
            );
            return data;
        },
        retry: false, // consistent with the other hooks; don't hammer on 401
    });
}

/**
 * Upload one or more .feature / .jsonl files.
 *
 * Files are appended under a single repeated `files` key, which is what
 * FastAPI's `files: list[UploadFile]` expects. The backend validates each file
 * independently and returns per-file outcomes, so a 422 (nothing accepted) is
 * still a useful response body rather than an error to swallow.
 */
export function useUploadTrainingDatasets() {
    const queryClient = useQueryClient();
    return useMutation<TrainingUploadResponse, Error, File[]>({
        mutationFn: async (files: File[]) => {
            const form = new FormData();
            files.forEach((file) => form.append("files", file));
            try {
                const { data } = await apiClient.post<TrainingUploadResponse>(
                    "/training/datasets",
                    form,
                    { headers: { "Content-Type": "multipart/form-data" } }
                );
                return data;
            } catch (err: unknown) {
                // A 422 means every file was rejected — the body still carries
                // the per-file reasons the user needs, so surface it as data.
                if (typeof err === "object" && err !== null && "response" in err) {
                    const axiosErr = err as {
                        response?: { status?: number; data?: TrainingUploadResponse };
                    };
                    if (axiosErr.response?.status === 422 && axiosErr.response.data) {
                        return axiosErr.response.data;
                    }
                }
                throw err instanceof Error ? err : new Error("Upload failed.");
            }
        },
        onSuccess: (result) => {
            if (result.accepted.length > 0) {
                void queryClient.invalidateQueries({ queryKey: QUERY_KEY });
            }
        },
    });
}

/**
 * Delete an uploaded dataset (row + stored object), then refresh the list.
 */
export function useDeleteTrainingDataset() {
    const queryClient = useQueryClient();
    return useMutation({
        mutationFn: async (id: string) => {
            await apiClient.delete(`/training/datasets/${id}`);
            return id;
        },
        onSuccess: () => {
            void queryClient.invalidateQueries({ queryKey: QUERY_KEY });
        },
    });
}

// ---------------------------------------------------------------------------
// Sample datasets
// ---------------------------------------------------------------------------

/**
 * Download a valid example of an accepted training file.
 *
 * The server generates it from the same constants the uploader validates
 * against, so what arrives here is guaranteed to be something the upload box
 * next to this button will accept.
 */
export function useDownloadSampleDataset() {
    return useMutation({
        mutationFn: async (kind: "jsonl" | "feature" | "csv") => {
            const response = await apiClient.get("/training/sample-dataset", {
                params: { kind },
                responseType: "blob",
            });
            triggerBlobDownload(
                response.data as Blob,
                filenameFromHeader(
                    response.headers["content-disposition"],
                    {
                        feature: "sample-scenarios.feature",
                        csv: "sample-training-pairs.csv",
                        jsonl: "sample-training-pairs.jsonl",
                    }[kind]
                )
            );
        },
    });
}

// ---------------------------------------------------------------------------
// Fine-tuning runs
// ---------------------------------------------------------------------------

/** Whether this server can start a run, and what stops it if not. */
export function useTrainingReadiness() {
    return useQuery({
        queryKey: READINESS_KEY,
        queryFn: async () => {
            const { data } = await apiClient.get<TrainingReadiness>(
                "/training/readiness"
            );
            return data;
        },
        retry: false,
    });
}

/** Whether a finished adapter can be published into a runtime from here. */
export function useServingReadiness() {
    return useQuery({
        queryKey: SERVING_READINESS_KEY,
        queryFn: async () => {
            const { data } = await apiClient.get<ServingReadiness>(
                "/training/serving-readiness"
            );
            return data;
        },
        retry: false,
    });
}

/**
 * Ask Kaggle what a dropped run's kernel is doing, and pick up from there.
 *
 * The watcher is the fragile part of a long run — a DNS blip or the wall-clock
 * timeout ends it while the kernel carries on — so this re-attaches instead of
 * starting over. It never pushes: a new run would overwrite the very kernel
 * being recovered.
 */
export function useRecheckTrainingRun() {
    const queryClient = useQueryClient();
    return useMutation({
        mutationFn: async (runId: string) => {
            const { data } = await apiClient.post<TrainingRun>(
                `/training/runs/${runId}/recheck`
            );
            return data;
        },
        onSuccess: () => {
            void queryClient.invalidateQueries({ queryKey: RUNS_KEY });
            // The run is active again, so "can another run start" has changed.
            void queryClient.invalidateQueries({ queryKey: READINESS_KEY });
        },
    });
}

/**
 * Publish one run's adapter into the local model runtime.
 *
 * Converting and registering takes longer than a request, so this returns as
 * soon as the row says `publishing` and the runs list polls from there — the
 * same shape as starting a run.
 */
export function usePublishTrainingRun() {
    const queryClient = useQueryClient();
    return useMutation({
        mutationFn: async (runId: string) => {
            const { data } = await apiClient.post<TrainingRun>(
                `/training/runs/${runId}/serve`
            );
            return data;
        },
        onSuccess: () => {
            void queryClient.invalidateQueries({ queryKey: RUNS_KEY });
            // What the app serves has just changed, so the model toggle's
            // availability is stale.
            void queryClient.invalidateQueries({ queryKey: FINE_TUNED_STATUS_KEY });
        },
    });
}

/**
 * The user's fine-tuning runs, polled while any of them is still moving.
 *
 * A run has no push channel — its worker is a detached task writing to a row —
 * so the list is the progress indicator. Polling stops as soon as every run has
 * reached a final status, which is the whole reason the status enum
 * distinguishes them.
 */
export function useTrainingRuns() {
    return useQuery({
        queryKey: RUNS_KEY,
        queryFn: async () => {
            const { data } = await apiClient.get<TrainingRun[]>("/training/runs");
            return data;
        },
        retry: false,
        refetchInterval: (query) => {
            const runs = query.state.data;
            // A publish is also a detached worker writing to the row, so it
            // keeps the list polling exactly as a run does.
            const busy = runs?.some(
                (run) => isRunActive(run.status) || run.serve_status === "publishing"
            );
            if (!busy) return false;
            // Five seconds: the stages a user actually watches (build, fetch)
            // last a minute or two, while the long middle is a Kaggle kernel
            // whose own status the server polls far less often anyway.
            return 5000;
        },
    });
}

/**
 * Start a fine-tuning run over the uploaded datasets.
 *
 * The server refuses (409) without Kaggle credentials or while another run is
 * active; `useTrainingReadiness` reports both up front so the button can
 * explain rather than fail.
 */
export function useStartTrainingRun() {
    const queryClient = useQueryClient();
    return useMutation({
        mutationFn: async () => {
            const { data } = await apiClient.post<TrainingRun>("/training/runs");
            return data;
        },
        onSuccess: () => {
            void queryClient.invalidateQueries({ queryKey: RUNS_KEY });
            // The new run is active, so readiness has just flipped to false.
            void queryClient.invalidateQueries({ queryKey: READINESS_KEY });
        },
    });
}

/**
 * Delete one run, its log and the adapter it produced.
 *
 * The server refuses (409) while a run is still going — the row is not what a
 * run is made of, so removing it would not stop the work.
 */
export function useDeleteTrainingRun() {
    const queryClient = useQueryClient();
    return useMutation({
        mutationFn: async (runId: string) => {
            await apiClient.delete(`/training/runs/${runId}`);
            return runId;
        },
        onSuccess: () => {
            void queryClient.invalidateQueries({ queryKey: RUNS_KEY });
        },
    });
}

/**
 * Delete every finished run. Anything still training is left alone.
 *
 * One request rather than a loop: the server already knows which runs are
 * finished, and deciding that in the client would race with a run finishing
 * between the list and the deletes. Resolves to the number removed.
 */
export function useDeleteFinishedTrainingRuns() {
    const queryClient = useQueryClient();
    return useMutation({
        mutationFn: async () => {
            const { data } = await apiClient.delete<{ deleted: number }>(
                "/training/runs"
            );
            return data.deleted;
        },
        onSuccess: () => {
            void queryClient.invalidateQueries({ queryKey: RUNS_KEY });
        },
    });
}

/**
 * Delete everything fine-tuning has produced, here and on Kaggle.
 *
 * The start-over button. Invalidates all three caches rather than just the one
 * it posted to — the page shows all of them, and leaving two stale would look
 * like the wipe half-worked. Resolves to per-store counts; `kernels` is how
 * many Kaggle kernels the server tried to delete alongside the rows, and
 * `served_model` names the model unloaded from the serving runtime, if any.
 */
export function useWipeAllTrainingData() {
    const queryClient = useQueryClient();
    return useMutation({
        mutationFn: async () => {
            const { data } = await apiClient.delete<{
                datasets: number;
                runs: number;
                evaluation_rows: number;
                kernels: number;
                served_model: string | null;
            }>("/training/all-data");
            return data;
        },
        onSuccess: () => {
            void queryClient.invalidateQueries({ queryKey: QUERY_KEY });
            void queryClient.invalidateQueries({ queryKey: RUNS_KEY });
            void queryClient.invalidateQueries({ queryKey: READINESS_KEY });
            void queryClient.invalidateQueries({ queryKey: ["evaluation"] });
            // The wipe unloads the served model, so the fine-tuned toggle on
            // the session page is stale the moment this returns — it would
            // otherwise stay enabled until that query happened to refetch.
            void queryClient.invalidateQueries({ queryKey: FINE_TUNED_STATUS_KEY });
        },
    });
}

/** Download a completed run's LoRA adapter as a zip. */
export function useDownloadTrainedModel() {
    return useMutation({
        mutationFn: async (runId: string) => {
            const response = await apiClient.get(`/training/runs/${runId}/model`, {
                responseType: "blob",
            });
            triggerBlobDownload(
                response.data as Blob,
                filenameFromHeader(
                    response.headers["content-disposition"],
                    `${runId}-adapter.zip`
                )
            );
        },
    });
}
