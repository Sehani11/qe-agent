import React from "react";

/** The title block every secondary page opens with. Keeps them in step. */
export default function PageHeader({
    eyebrow,
    title,
    description,
    actions,
}: {
    eyebrow: string;
    title: string;
    description?: React.ReactNode;
    actions?: React.ReactNode;
}) {
    return (
        <div className="mb-7 flex flex-wrap items-end justify-between gap-4">
            <div className="min-w-0">
                <p className="eyebrow text-muted-foreground">{eyebrow}</p>
                <h1 className="mt-2 text-[1.75rem] leading-tight">{title}</h1>
                {description && (
                    <p className="mt-2 max-w-2xl text-sm leading-relaxed text-muted-foreground">
                        {description}
                    </p>
                )}
            </div>
            {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
        </div>
    );
}
