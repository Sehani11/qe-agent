"use client";

import { Check, ChevronDown, Sparkles, TriangleAlert } from "lucide-react";

import { usePopoverMenu } from "@/lib/hooks/usePopoverMenu";
import { cn } from "@/lib/utils";

export interface ModelMenuItem {
    id: string;
    label: string;
    note: string;
    /** Shown in fail ink under the note — a real limitation, not a hint. */
    warning?: string;
}

export interface ModelMenuGroup {
    id: string;
    label: string;
    items: ModelMenuItem[];
}

/**
 * The grouped dropdown the model picker is built from.
 *
 * Presentational only — no model knowledge, no context, no storage. The caller
 * supplies the groups and owns the choice, which keeps the catalog and the
 * popover mechanics (focus return, dismissal, roles) from tangling with each
 * other.
 */
export default function ModelMenu({
    groups,
    activeId,
    onSelect,
    triggerLabel,
    srLabel,
    hint,
    id,
    className,
}: {
    groups: ModelMenuGroup[];
    activeId: string;
    onSelect: (id: string) => void;
    /** What the closed control shows. */
    triggerLabel: string;
    /** Names the control for screen readers, e.g. "Verification model". */
    srLabel: string;
    /** One line above the list saying what the choice affects. */
    hint: string;
    /** Put on the trigger, so a visible <label htmlFor> can point at it. */
    id?: string;
    className?: string;
}) {
    // Escape closes; a press anywhere outside dismisses. Shared with
    // ProjectSwitcher, the other dropdown in the nav.
    const { open, toggle, close, menuId, containerRef, triggerRef } =
        usePopoverMenu();

    return (
        // Fixed width, for the same reason as ProjectSwitcher: model labels
        // differ in length ("GPT-4o" vs "Claude Haiku 4.5", and an env-supplied
        // model shows its raw id), and this sits in the right-hand cluster, so
        // a width change shifts everything beside it. On the wrapper so a caller
        // can override with `w-full`.
        <div ref={containerRef} className={cn("relative w-52", className)}>
            <button
                ref={triggerRef}
                id={id}
                type="button"
                onClick={toggle}
                aria-haspopup="menu"
                aria-expanded={open}
                aria-controls={open ? menuId : undefined}
                title={`${srLabel}: ${triggerLabel}`}
                className={cn(
                    "flex h-9 w-full items-center gap-1.5 rounded-md border border-rule bg-surface pl-2 pr-1.5",
                    "text-[0.8125rem] font-medium text-foreground transition-colors hover:border-rule-strong",
                    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/25"
                )}
            >
                <Sparkles
                    className="h-3.5 w-3.5 shrink-0 text-muted-foreground"
                    aria-hidden="true"
                />
                <span className="sr-only">{srLabel}: </span>
                {/* min-w-0 so `truncate` can actually shrink it inside the flex
                    row rather than overflowing the fixed box. */}
                <span className="min-w-0 flex-1 truncate text-left font-mono">
                    {triggerLabel}
                </span>
                <ChevronDown
                    className={cn(
                        "h-3.5 w-3.5 shrink-0 text-muted-foreground transition-transform duration-150",
                        open && "rotate-180"
                    )}
                    aria-hidden="true"
                />
            </button>

            {open && (
                <div
                    id={menuId}
                    role="menu"
                    aria-label={srLabel}
                    className={cn(
                        // Never narrower than the control it belongs to:
                        // `min-w-full` resolves against the relative wrapper,
                        // which is the trigger. A fixed width made the panel a
                        // stubby 19rem under the full-width trigger on the
                        // settings page, so the two edges did not line up.
                        //
                        // 19rem stays the FLOOR, for the nav where the trigger
                        // is deliberately narrow and a menu matching it would
                        // wrap every model note. max-w keeps that floor from
                        // overflowing a small screen — the mobile drawer is
                        // itself only min(19rem,85vw) wide.
                        "absolute right-0 z-50 mt-1.5 w-[19rem] min-w-full max-w-[calc(100vw-1.5rem)]",
                        "overflow-hidden rounded-lg border border-rule bg-popover shadow-xl shadow-black/10",
                        "animate-[overlay-in_120ms_ease-out] dark:shadow-black/40"
                    )}
                >
                    <p className="border-b border-rule px-3 py-2 text-xs text-muted-foreground">
                        {hint}
                    </p>

                    <div className="scrollbar-thin max-h-[min(24rem,60vh)] overflow-y-auto py-1">
                        {groups.map((group) => (
                            <div
                                key={group.id}
                                role="group"
                                aria-labelledby={`${menuId}-${group.id}`}
                            >
                                <p
                                    id={`${menuId}-${group.id}`}
                                    className="eyebrow px-3 pb-1 pt-2 text-muted-foreground"
                                >
                                    {group.label}
                                </p>
                                {group.items.map((item) => {
                                    const active = item.id === activeId;
                                    return (
                                        <button
                                            key={item.id}
                                            type="button"
                                            role="menuitemradio"
                                            aria-checked={active}
                                            onClick={() => {
                                                onSelect(item.id);
                                                close();
                                            }}
                                            className={cn(
                                                "flex w-full items-start gap-2 px-3 py-2 text-left transition-colors",
                                                active ? "bg-pass-soft" : "hover:bg-muted"
                                            )}
                                        >
                                            <Check
                                                className={cn(
                                                    "mt-0.5 h-3.5 w-3.5 shrink-0",
                                                    active ? "text-pass" : "text-transparent"
                                                )}
                                                aria-hidden="true"
                                            />
                                            <span className="min-w-0">
                                                <span
                                                    className={cn(
                                                        "block truncate font-mono text-[0.8125rem] font-medium",
                                                        active ? "text-pass-ink" : "text-foreground"
                                                    )}
                                                >
                                                    {item.label}
                                                </span>
                                                <span className="block text-xs text-muted-foreground">
                                                    {item.note}
                                                </span>
                                                {item.warning && (
                                                    <span className="mt-0.5 flex items-center gap-1 text-xs text-fail-ink">
                                                        <TriangleAlert
                                                            className="h-3 w-3 shrink-0"
                                                            aria-hidden="true"
                                                        />
                                                        {item.warning}
                                                    </span>
                                                )}
                                            </span>
                                        </button>
                                    );
                                })}
                            </div>
                        ))}
                    </div>
                </div>
            )}
        </div>
    );
}
