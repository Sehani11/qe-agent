"use client";

import { cn } from "@/lib/utils";

/**
 * A two-state switch for turning a capability on or off.
 *
 * Use it wherever the question is "is this mode on?", whether the answer
 * outlives the page or not — `use-knowledge-base` in `GitHubSourceSelector` is
 * per-run component state and still reads as a switch, because what the control
 * expresses is a mode being enabled, not an item being ticked. Reach for a
 * checkbox when the sense is selection or agreement: picking items from a list,
 * or accepting terms.
 *
 * Built on `role="switch"` rather than a restyled `<input type="checkbox">` so
 * assistive tech announces on/off instead of checked/unchecked. The track
 * turns pass-green when on, reusing the signal colour the rest of the app uses
 * for "this is live" rather than introducing an accent.
 */
export function Switch({
    checked,
    onCheckedChange,
    label,
    description,
    disabled,
    className,
}: {
    checked: boolean;
    onCheckedChange: (checked: boolean) => void;
    /** Visible text; also the accessible name. */
    label: React.ReactNode;
    /** Optional second line under the label. */
    description?: React.ReactNode;
    disabled?: boolean;
    className?: string;
}) {
    return (
        <label
            className={cn(
                "inline-flex items-center gap-2.5",
                disabled ? "cursor-not-allowed opacity-55" : "cursor-pointer",
                "select-none",
                className
            )}
        >
            <button
                type="button"
                role="switch"
                aria-checked={checked}
                disabled={disabled}
                onClick={() => onCheckedChange(!checked)}
                className={cn(
                    "relative inline-flex h-5 w-9 shrink-0 items-center rounded-full border transition-colors duration-200",
                    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/25",
                    "disabled:cursor-not-allowed",
                    checked
                        ? "border-pass bg-pass"
                        : "border-rule-strong bg-muted hover:border-rule-strong/80"
                )}
            >
                <span
                    aria-hidden="true"
                    className={cn(
                        "pointer-events-none absolute left-0.5 h-3.5 w-3.5 rounded-full bg-white shadow-sm transition-transform duration-200",
                        // Dark mode paints the thumb in the paper colour so it
                        // reads as a cut-out rather than a bright dot.
                        "dark:bg-[color:var(--paper)]",
                        checked ? "translate-x-4" : "translate-x-0"
                    )}
                />
            </button>

            <span className="text-[0.8125rem] text-muted-foreground">
                <span className="font-medium text-foreground">{label}</span>
                {description && <span className="mt-0.5 block text-xs">{description}</span>}
            </span>
        </label>
    );
}
