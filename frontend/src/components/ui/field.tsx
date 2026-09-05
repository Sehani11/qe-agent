"use client";

import React from "react";

import { cn } from "@/lib/utils";

/* Inputs sit flush in the grid with a hairline border; focus lifts the ring in
   the pass-green so the same signal colour that means "verified" also means
   "you are here". */

const controlBase =
    "w-full rounded-md border border-input bg-surface px-3 py-2 text-sm text-foreground transition-colors placeholder:text-muted-foreground/70 focus-visible:border-ring focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/25 disabled:cursor-not-allowed disabled:opacity-55 aria-[invalid=true]:border-fail aria-[invalid=true]:focus-visible:ring-fail/25";

export const Input = React.forwardRef<
    HTMLInputElement,
    React.InputHTMLAttributes<HTMLInputElement> & { mono?: boolean }
>(function Input({ className, mono, ...props }, ref) {
    return (
        <input
            ref={ref}
            className={cn(controlBase, mono && "font-mono", className)}
            {...props}
        />
    );
});

export const Textarea = React.forwardRef<
    HTMLTextAreaElement,
    React.TextareaHTMLAttributes<HTMLTextAreaElement> & { mono?: boolean }
>(function Textarea({ className, mono, ...props }, ref) {
    return (
        <textarea
            ref={ref}
            className={cn(controlBase, "resize-y", mono && "font-mono", className)}
            {...props}
        />
    );
});

export function Label({
    className,
    children,
    hint,
    ...props
}: React.LabelHTMLAttributes<HTMLLabelElement> & { hint?: React.ReactNode }) {
    return (
        <label
            className={cn(
                "flex items-baseline justify-between gap-2 text-[0.8125rem] font-medium text-foreground",
                className
            )}
            {...props}
        >
            <span>{children}</span>
            {hint && <span className="text-xs font-normal text-muted-foreground">{hint}</span>}
        </label>
    );
}

/** Label + control + message, with the error wired to the control for a11y. */
export function Field({
    label,
    htmlFor,
    hint,
    error,
    description,
    children,
    className,
}: {
    label: string;
    htmlFor: string;
    hint?: React.ReactNode;
    error?: string | null;
    description?: React.ReactNode;
    children: React.ReactNode;
    className?: string;
}) {
    return (
        <div className={cn("space-y-1.5", className)}>
            <Label htmlFor={htmlFor} hint={hint}>
                {label}
            </Label>
            {children}
            {error ? (
                <p id={`${htmlFor}-error`} className="text-xs text-fail-ink" role="alert">
                    {error}
                </p>
            ) : (
                description && <p className="text-xs text-muted-foreground">{description}</p>
            )}
        </div>
    );
}
