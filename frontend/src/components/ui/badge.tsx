import React from "react";
import { Check, CircleDashed, Minus, X } from "lucide-react";

import { cn } from "@/lib/utils";

export type Signal = "pass" | "fail" | "pending" | "neutral" | "keyword";

const TONES: Record<Signal, string> = {
    pass: "border-pass/30 bg-pass-soft text-pass-ink",
    fail: "border-fail/30 bg-fail-soft text-fail-ink",
    pending: "border-pending/30 bg-pending-soft text-pending-ink",
    keyword: "border-keyword/30 bg-keyword-soft text-keyword",
    neutral: "border-rule bg-muted text-muted-foreground",
};

export function Badge({
    signal = "neutral",
    className,
    children,
}: {
    signal?: Signal;
    className?: string;
    children: React.ReactNode;
}) {
    return (
        <span
            className={cn(
                "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 font-mono text-[0.6875rem] font-semibold whitespace-nowrap",
                TONES[signal],
                className
            )}
        >
            {children}
        </span>
    );
}

const VERDICT_ICON: Record<Signal, React.ElementType> = {
    pass: Check,
    fail: X,
    pending: CircleDashed,
    neutral: Minus,
    keyword: Minus,
};

/**
 * A verdict chip. The glyph carries the meaning as well as the colour, so the
 * pass/fail distinction survives a colour-vision difference or a greyscale print.
 */
export function VerdictBadge({
    status,
    className,
}: {
    status: string | null | undefined;
    className?: string;
}) {
    const normalized = (status ?? "").toLowerCase();
    const signal: Signal =
        normalized === "pass"
            ? "pass"
            : normalized === "fail"
              ? "fail"
              : normalized
                ? "pending"
                : "neutral";
    const Icon = VERDICT_ICON[signal];

    return (
        <Badge signal={signal} className={cn("uppercase", className)}>
            <Icon className="h-3 w-3" aria-hidden="true" />
            {status || "unknown"}
        </Badge>
    );
}
