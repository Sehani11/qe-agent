import React from "react";

import { cn } from "@/lib/utils";

/* ---------------------------------------------------------------------------
   Panels are defined by hairline rules rather than drop shadows, so a dense
   workspace reads as one printed listing instead of a pile of floating cards.
--------------------------------------------------------------------------- */

export function Panel({
    className,
    children,
    ...props
}: React.HTMLAttributes<HTMLDivElement>) {
    return (
        <section
            className={cn("relative rounded-lg border border-rule bg-card", className)}
            {...props}
        >
            {children}
        </section>
    );
}

export function PanelHeader({
    eyebrow,
    title,
    description,
    actions,
    className,
}: {
    /** The keyword that names this panel's role, e.g. "Feature", "Verify". */
    eyebrow?: string;
    title: React.ReactNode;
    description?: React.ReactNode;
    actions?: React.ReactNode;
    className?: string;
}) {
    return (
        <header
            className={cn(
                "flex flex-wrap items-start justify-between gap-x-4 gap-y-3 border-b border-rule px-4 py-3.5",
                className
            )}
        >
            <div className="min-w-0">
                {eyebrow && <p className="eyebrow text-muted-foreground">{eyebrow}</p>}
                <h2 className={cn("truncate text-base", eyebrow && "mt-1")}>{title}</h2>
                {description && (
                    <p className="mt-1 text-[0.8125rem] leading-snug text-muted-foreground">
                        {description}
                    </p>
                )}
            </div>
            {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
        </header>
    );
}

export function PanelBody({
    className,
    children,
    ...props
}: React.HTMLAttributes<HTMLDivElement>) {
    return (
        <div className={cn("p-4", className)} {...props}>
            {children}
        </div>
    );
}
