"use client";

import React, { useEffect, useState } from "react";
import { Loader2 } from "lucide-react";

import { cn } from "@/lib/utils";

/* ---------------------------------------------------------------------------
   Loading states.

   Two registers, used deliberately:
   - `Spinner` for a control that is busy (inside a button, beside a label).
   - `RunSpinner` for work the agent is doing on your behalf — it cycles the
     braille frames a test runner uses, which is the vernacular of the thing
     the user is actually waiting for.
   Skeletons stand in for content whose shape we already know; a spinner is for
   work whose duration we don't.
--------------------------------------------------------------------------- */

export function Spinner({
    className,
    label,
}: {
    className?: string;
    /** Announced to screen readers. Omit inside a control that already says it. */
    label?: string;
}) {
    return (
        <>
            <Loader2
                className={cn("h-4 w-4 animate-spin", className)}
                aria-hidden="true"
            />
            {label && <span className="sr-only">{label}</span>}
        </>
    );
}

const RUN_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"];

/** The braille cycler a test runner shows while a suite is executing. */
export function RunSpinner({ className }: { className?: string }) {
    const [frame, setFrame] = useState(0);
    const [animate, setAnimate] = useState(true);

    useEffect(() => {
        // Honour reduced motion by holding on a single frame.
        const query = window.matchMedia("(prefers-reduced-motion: reduce)");
        const sync = () => setAnimate(!query.matches);
        sync();
        query.addEventListener("change", sync);
        return () => query.removeEventListener("change", sync);
    }, []);

    useEffect(() => {
        if (!animate) return;
        const timer = window.setInterval(
            () => setFrame((current) => (current + 1) % RUN_FRAMES.length),
            80
        );
        return () => window.clearInterval(timer);
    }, [animate]);

    return (
        <span
            className={cn("inline-block w-[1ch] font-mono leading-none", className)}
            aria-hidden="true"
        >
            {RUN_FRAMES[frame]}
        </span>
    );
}

/** A labelled inline loading line, for panel bodies and status rows. */
export function InlineLoader({
    children,
    className,
}: {
    children: React.ReactNode;
    className?: string;
}) {
    return (
        <p
            className={cn(
                "flex items-center gap-2 font-mono text-[0.8125rem] text-muted-foreground",
                className
            )}
            role="status"
        >
            <RunSpinner className="text-pending" />
            {children}
        </p>
    );
}

export function Skeleton({ className }: { className?: string }) {
    return (
        <span
            className={cn(
                "relative block overflow-hidden rounded bg-muted",
                // The sheen is a child pseudo-element so the base block still
                // reads as a solid placeholder under reduced motion.
                "after:absolute after:inset-0 after:-translate-x-full after:animate-[shimmer_1.6s_infinite] after:bg-gradient-to-r after:from-transparent after:via-foreground/[0.06] after:to-transparent",
                className
            )}
            aria-hidden="true"
        />
    );
}

/** A paragraph-shaped placeholder; the last line is short, as real text is. */
export function SkeletonText({
    lines = 3,
    className,
}: {
    lines?: number;
    className?: string;
}) {
    return (
        <span className={cn("block space-y-2", className)} aria-hidden="true">
            {Array.from({ length: lines }).map((_, index) => (
                <Skeleton
                    key={index}
                    className={cn("h-3", index === lines - 1 ? "w-2/5" : "w-full")}
                />
            ))}
        </span>
    );
}

/** Placeholder rows for a list whose items are uniform (sessions, sources). */
export function SkeletonRows({
    rows = 3,
    className,
}: {
    rows?: number;
    className?: string;
}) {
    return (
        <div className={cn("space-y-2", className)} role="status" aria-label="Loading">
            {Array.from({ length: rows }).map((_, index) => (
                <div
                    key={index}
                    className="flex items-center gap-3 rounded-lg border border-rule bg-card p-3.5"
                >
                    <Skeleton className="h-8 w-8 shrink-0 rounded" />
                    <div className="min-w-0 flex-1 space-y-2">
                        <Skeleton className="h-3 w-1/3" />
                        <Skeleton className="h-3 w-3/5" />
                    </div>
                    <Skeleton className="h-5 w-16 shrink-0 rounded-full" />
                </div>
            ))}
        </div>
    );
}

/**
 * A hairline progress track. Pass `value` (0–100) when the work reports real
 * progress; omit it for an indeterminate sweep.
 */
export function ProgressBar({
    value,
    signal = "pass",
    className,
    label,
}: {
    value?: number | null;
    signal?: "pass" | "fail" | "pending";
    className?: string;
    label?: string;
}) {
    const indeterminate = value == null;
    const clamped = indeterminate ? 0 : Math.min(100, Math.max(0, value));
    const fill =
        signal === "fail" ? "bg-fail" : signal === "pending" ? "bg-pending" : "bg-pass";

    return (
        <div
            className={cn("h-1 w-full overflow-hidden rounded-full bg-muted", className)}
            role="progressbar"
            aria-label={label}
            aria-valuenow={indeterminate ? undefined : clamped}
            aria-valuemin={indeterminate ? undefined : 0}
            aria-valuemax={indeterminate ? undefined : 100}
        >
            {indeterminate ? (
                <div className={cn("h-full w-1/3 animate-[indeterminate_1.4s_ease-in-out_infinite] rounded-full", fill)} />
            ) : (
                <div
                    className={cn("h-full rounded-full transition-[width] duration-500 ease-out", fill)}
                    style={{ width: `${clamped}%` }}
                />
            )}
        </div>
    );
}

/** Covers a panel while its contents refresh, without collapsing the layout. */
export function LoadingOverlay({
    show,
    message = "Loading",
}: {
    show: boolean;
    message?: string;
}) {
    if (!show) return null;
    return (
        <div className="absolute inset-0 z-10 flex items-center justify-center rounded-lg bg-card/75 backdrop-blur-[1px]">
            <InlineLoader>{message}…</InlineLoader>
        </div>
    );
}
