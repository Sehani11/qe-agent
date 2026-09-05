"use client";

import React, {
    createContext,
    useCallback,
    useContext,
    useEffect,
    useMemo,
    useRef,
    useState,
} from "react";
import { AlertTriangle, Check, Info, Loader2, X } from "lucide-react";

import { cn } from "@/lib/utils";

/* ---------------------------------------------------------------------------
   Toasts, in the vocabulary of a test runner.

   A toast here reads like a verdict line from a .feature run: a monospace
   keyword in the gutter, then plain language about what actually happened.
   Copy rule (see the interface's voice): an action keeps its name through the
   whole flow, so "Generate BDD" resolves to "BDD generated" — never a vague
   "Success!". Errors say what broke and what to do next; they don't apologise.
--------------------------------------------------------------------------- */

export type ToastVariant = "success" | "error" | "warning" | "info" | "loading";

export interface ToastAction {
    label: string;
    onClick: () => void;
}

export interface ToastOptions {
    /** Plain-language summary of what happened. Sentence case, no trailing period. */
    description?: string;
    /** Milliseconds before auto-dismiss. `null` keeps it until dismissed. */
    duration?: number | null;
    /** A single follow-up the person can take, e.g. "Retry". */
    action?: ToastAction;
    /** Reuse an id to replace an existing toast in place (used by `promise`). */
    id?: string;
}

interface ToastRecord extends ToastOptions {
    id: string;
    title: string;
    variant: ToastVariant;
}

interface ToastContextValue {
    toasts: ToastRecord[];
    push: (variant: ToastVariant, title: string, options?: ToastOptions) => string;
    dismiss: (id: string) => void;
    dismissAll: () => void;
}

const ToastContext = createContext<ToastContextValue | undefined>(undefined);

const DEFAULT_DURATION: Record<ToastVariant, number | null> = {
    success: 4000,
    info: 4500,
    warning: 6000,
    // Errors stay long enough to be read and acted on.
    error: 8000,
    // A loading toast is resolved by the caller, never by a timer.
    loading: null,
};

/** Cap the stack so a burst of SSE errors can't paper over the app. */
const MAX_VISIBLE = 4;

export function ToastProvider({ children }: { children: React.ReactNode }) {
    const [toasts, setToasts] = useState<ToastRecord[]>([]);
    const counterRef = useRef(0);

    const dismiss = useCallback((id: string) => {
        setToasts((current) => current.filter((toast) => toast.id !== id));
    }, []);

    const dismissAll = useCallback(() => setToasts([]), []);

    const push = useCallback(
        (variant: ToastVariant, title: string, options: ToastOptions = {}) => {
            const id = options.id ?? `toast-${(counterRef.current += 1)}`;
            const record: ToastRecord = {
                ...options,
                id,
                title,
                variant,
                duration:
                    options.duration === undefined ? DEFAULT_DURATION[variant] : options.duration,
            };

            setToasts((current) => {
                const existing = current.findIndex((toast) => toast.id === id);
                if (existing !== -1) {
                    const next = [...current];
                    next[existing] = record;
                    return next;
                }
                return [...current, record].slice(-MAX_VISIBLE);
            });

            return id;
        },
        []
    );

    const value = useMemo(
        () => ({ toasts, push, dismiss, dismissAll }),
        [toasts, push, dismiss, dismissAll]
    );

    return (
        <ToastContext.Provider value={value}>
            {children}
            <Toaster />
        </ToastContext.Provider>
    );
}

function useToastContext() {
    const context = useContext(ToastContext);
    if (!context) {
        throw new Error("useToast must be used within a ToastProvider");
    }
    return context;
}

/**
 * Fire toasts from anywhere under `ToastProvider`.
 *
 * ```ts
 * const toast = useToast();
 * toast.success("BDD generated", { description: "6 scenarios from 3 criteria" });
 * toast.error("Ticket not found", { description: "Check the ID and try again" });
 * ```
 */
export function useToast() {
    const { push, dismiss, dismissAll } = useToastContext();

    return useMemo(() => {
        const api = {
            success: (title: string, options?: ToastOptions) => push("success", title, options),
            error: (title: string, options?: ToastOptions) => push("error", title, options),
            warning: (title: string, options?: ToastOptions) => push("warning", title, options),
            info: (title: string, options?: ToastOptions) => push("info", title, options),
            loading: (title: string, options?: ToastOptions) => push("loading", title, options),
            dismiss,
            dismissAll,
            /**
             * Show a loading toast, then swap it in place for the outcome.
             * Rethrows so callers can still handle the failure themselves.
             */
            promise: async <T,>(
                promise: Promise<T>,
                messages: {
                    loading: string;
                    success: string | ((value: T) => string);
                    error: string | ((error: unknown) => string);
                }
            ): Promise<T> => {
                const id = push("loading", messages.loading);
                try {
                    const value = await promise;
                    const title =
                        typeof messages.success === "function"
                            ? messages.success(value)
                            : messages.success;
                    push("success", title, { id });
                    return value;
                } catch (error) {
                    const title =
                        typeof messages.error === "function"
                            ? messages.error(error)
                            : messages.error;
                    push("error", title, { id });
                    throw error;
                }
            },
        };
        return api;
    }, [push, dismiss, dismissAll]);
}

const VARIANT_META: Record<
    ToastVariant,
    { label: string; icon: React.ElementType; signal: string; tone: string }
> = {
    success: { label: "Passed", icon: Check, signal: "pass", tone: "text-pass-ink" },
    error: { label: "Failed", icon: X, signal: "fail", tone: "text-fail-ink" },
    warning: { label: "Warning", icon: AlertTriangle, signal: "pending", tone: "text-pending-ink" },
    info: { label: "Note", icon: Info, signal: "", tone: "text-muted-foreground" },
    loading: { label: "Running", icon: Loader2, signal: "pending", tone: "text-pending-ink" },
};

function Toaster() {
    const { toasts, dismiss } = useToastContext();

    return (
        <div
            // `pointer-events-none` on the region keeps the column from eating
            // clicks in the page behind it; each card re-enables its own.
            className="pointer-events-none fixed inset-x-0 bottom-0 z-[100] flex flex-col items-center gap-2 p-4 sm:inset-x-auto sm:right-0 sm:items-end sm:p-6"
            role="region"
            aria-label="Notifications"
        >
            {toasts.map((toast) => (
                <ToastCard key={toast.id} toast={toast} onDismiss={dismiss} />
            ))}
        </div>
    );
}

function ToastCard({
    toast,
    onDismiss,
}: {
    toast: ToastRecord;
    onDismiss: (id: string) => void;
}) {
    const [leaving, setLeaving] = useState(false);
    const meta = VARIANT_META[toast.variant];
    const Icon = meta.icon;

    const close = useCallback(() => {
        setLeaving(true);
        // Let the exit animation play before the node is removed. Reduced-motion
        // users have the animation collapsed to ~0ms, so this is imperceptible.
        window.setTimeout(() => onDismiss(toast.id), 140);
    }, [onDismiss, toast.id]);

    // Auto-dismiss, paused while the pointer rests on the toast so a passing
    // cursor can't make someone miss the message.
    const [paused, setPaused] = useState(false);
    useEffect(() => {
        if (toast.duration == null || paused || leaving) return;
        const timer = window.setTimeout(close, toast.duration);
        return () => window.clearTimeout(timer);
    }, [toast.duration, toast.id, paused, leaving, close]);

    return (
        <div
            className={cn(
                "pointer-events-auto w-full max-w-sm overflow-hidden rounded-lg border bg-popover shadow-lg shadow-black/5 dark:shadow-black/40",
                leaving ? "animate-[toast-out_140ms_ease-in_forwards]" : "animate-[toast-in_180ms_cubic-bezier(0.21,1.02,0.73,1)]",
                toast.variant === "error" ? "border-fail/35" : "border-rule"
            )}
            role={toast.variant === "error" ? "alert" : "status"}
            aria-live={toast.variant === "error" ? "assertive" : "polite"}
            onMouseEnter={() => setPaused(true)}
            onMouseLeave={() => setPaused(false)}
            onFocusCapture={() => setPaused(true)}
            onBlurCapture={() => setPaused(false)}
        >
            <div className="gutter-rule flex gap-3 p-3.5" data-signal={meta.signal}>
                <Icon
                    className={cn(
                        "mt-0.5 h-4 w-4 shrink-0",
                        meta.tone,
                        toast.variant === "loading" && "animate-spin"
                    )}
                    aria-hidden="true"
                />

                <div className="min-w-0 flex-1">
                    <p className={cn("eyebrow", meta.tone)}>{meta.label}</p>
                    <p className="mt-1 text-sm leading-snug font-medium text-foreground">
                        {toast.title}
                    </p>
                    {toast.description && (
                        <p className="mt-1 text-[0.8125rem] leading-snug text-muted-foreground">
                            {toast.description}
                        </p>
                    )}
                    {toast.action && (
                        <button
                            type="button"
                            onClick={() => {
                                toast.action?.onClick();
                                close();
                            }}
                            className="mt-2.5 font-mono text-xs font-semibold text-foreground underline underline-offset-4 transition-colors hover:text-pass"
                        >
                            {toast.action.label}
                        </button>
                    )}
                </div>

                <button
                    type="button"
                    onClick={close}
                    aria-label="Dismiss notification"
                    className="-m-1 h-6 w-6 shrink-0 rounded p-1 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                >
                    <X className="h-full w-full" aria-hidden="true" />
                </button>
            </div>
        </div>
    );
}
