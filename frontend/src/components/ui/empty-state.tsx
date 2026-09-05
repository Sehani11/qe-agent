import React from "react";

import { cn } from "@/lib/utils";

/**
 * An empty screen is an invitation to act, so this always names the next step
 * rather than just reporting that there is nothing here.
 */
export function EmptyState({
    icon: Icon,
    title,
    description,
    action,
    className,
}: {
    icon?: React.ElementType;
    /** What is not here yet, in the user's words. */
    title: string;
    /** How to fill it. One sentence, active voice. */
    description?: string;
    action?: React.ReactNode;
    className?: string;
}) {
    return (
        <div
            className={cn(
                "flex flex-col items-center justify-center rounded-lg border border-dashed border-rule px-6 py-12 text-center",
                className
            )}
        >
            {Icon && (
                <Icon
                    className="mb-3 h-5 w-5 text-muted-foreground/70"
                    aria-hidden="true"
                />
            )}
            <p className="font-mono text-sm font-semibold text-foreground">{title}</p>
            {description && (
                <p className="mt-1.5 max-w-sm text-[0.8125rem] leading-relaxed text-muted-foreground">
                    {description}
                </p>
            )}
            {action && <div className="mt-4">{action}</div>}
        </div>
    );
}

/** Failure state for a panel whose data could not load. Says what to do next. */
export function ErrorState({
    title = "Could not load this",
    description,
    onRetry,
    className,
}: {
    title?: string;
    description?: string;
    onRetry?: () => void;
    className?: string;
}) {
    return (
        <div
            className={cn(
                "gutter-rule rounded-lg border border-fail/30 bg-fail-soft/60 px-4 py-3.5",
                className
            )}
            data-signal="fail"
            role="alert"
        >
            <p className="eyebrow text-fail-ink">Failed</p>
            <p className="mt-1 text-sm font-medium text-foreground">{title}</p>
            {description && (
                <p className="mt-1 text-[0.8125rem] leading-snug text-muted-foreground">
                    {description}
                </p>
            )}
            {onRetry && (
                <button
                    type="button"
                    onClick={onRetry}
                    className="mt-2.5 font-mono text-xs font-semibold text-foreground underline underline-offset-4 transition-colors hover:text-fail"
                >
                    Try again
                </button>
            )}
        </div>
    );
}
