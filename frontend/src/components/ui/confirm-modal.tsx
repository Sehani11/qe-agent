"use client";

import React, { useEffect, useRef, useSyncExternalStore } from "react";
import { createPortal } from "react-dom";
import { AlertTriangle, X } from "lucide-react";

import { Button } from "@/components/ui/button";

/* Hydration check without a mount effect: the server snapshot is false, the
   client snapshot true, so `createPortal` is only ever called where there is a
   document to portal into. */
const neverChanges = () => () => {};
const onClient = () => true;
const onServer = () => false;

export interface ConfirmModalProps {
    /** Whether the modal is visible. */
    open: boolean;
    /** Short title, e.g. "Delete source". */
    title: string;
    /** Body copy — string or nodes. */
    description?: React.ReactNode;
    /** Confirm button label (default "Confirm"). */
    confirmLabel?: string;
    /** Cancel button label (default "Cancel"). */
    cancelLabel?: string;
    /** Style the confirm action as destructive. Default false. */
    destructive?: boolean;
    /** Disables actions and shows a spinner on confirm while in flight. */
    isConfirming?: boolean;
    /** Invoked when the user confirms. */
    onConfirm: () => void;
    /** Invoked on cancel, backdrop click, Escape, or the close button. */
    onCancel: () => void;
}

/**
 * A reusable, accessible confirmation modal. Closes on Escape, backdrop click,
 * the close button, or Cancel; focuses the confirm button on open and locks
 * body scroll while shown. Renders nothing when `open` is false.
 *
 * Rendered through a portal into <body>. It is `position: fixed`, and several
 * of the app's chrome elements (the nav, the ingest bar) use `backdrop-blur`,
 * which makes them a containing block for fixed descendants — mounted in place,
 * the modal would centre itself inside that strip and get clipped rather than
 * covering the viewport.
 */
export function ConfirmModal({
    open,
    title,
    description,
    confirmLabel = "Confirm",
    cancelLabel = "Cancel",
    destructive = false,
    isConfirming = false,
    onConfirm,
    onCancel,
}: ConfirmModalProps) {
    const confirmRef = useRef<HTMLButtonElement>(null);
    const titleId = React.useId();
    const isMounted = useSyncExternalStore(neverChanges, onClient, onServer);

    // Escape to cancel (ignored while a confirm is in flight).
    useEffect(() => {
        if (!open) return;
        const onKey = (e: KeyboardEvent) => {
            if (e.key === "Escape" && !isConfirming) onCancel();
        };
        window.addEventListener("keydown", onKey);
        return () => window.removeEventListener("keydown", onKey);
    }, [open, isConfirming, onCancel]);

    // Lock body scroll while open; focus the confirm button.
    useEffect(() => {
        if (!open) return;
        const previousOverflow = document.body.style.overflow;
        document.body.style.overflow = "hidden";
        confirmRef.current?.focus();
        return () => {
            document.body.style.overflow = previousOverflow;
        };
    }, [open]);

    if (!open || !isMounted) return null;

    return createPortal(
        <div
            className="fixed inset-0 z-50 flex items-center justify-center p-4"
            role="dialog"
            aria-modal="true"
            aria-labelledby={titleId}
        >
            {/* Backdrop */}
            <div
                className="absolute inset-0 bg-black/45 backdrop-blur-[2px]"
                onClick={isConfirming ? undefined : onCancel}
                aria-hidden="true"
            />

            {/* Card */}
            <div className="relative w-full max-w-md rounded-lg border border-rule bg-popover p-5 shadow-2xl shadow-black/20 dark:shadow-black/60">
                <button
                    type="button"
                    onClick={onCancel}
                    disabled={isConfirming}
                    aria-label="Close"
                    className="absolute right-3.5 top-3.5 rounded p-1 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:opacity-50"
                >
                    <X className="h-4 w-4" aria-hidden="true" />
                </button>

                <div className="flex items-start gap-3 pr-8">
                    {destructive && (
                        <span
                            className="mt-0.5 grid h-8 w-8 shrink-0 place-items-center rounded border border-fail/30 bg-fail-soft text-fail-ink"
                            aria-hidden="true"
                        >
                            <AlertTriangle className="h-4 w-4" />
                        </span>
                    )}
                    <div className="min-w-0">
                        <h2 id={titleId} className="text-base">
                            {title}
                        </h2>
                        {description && (
                            <div className="mt-1.5 text-[0.8125rem] leading-relaxed text-muted-foreground">
                                {description}
                            </div>
                        )}
                    </div>
                </div>

                <div className="mt-6 flex justify-end gap-2">
                    <Button
                        type="button"
                        variant="outline"
                        onClick={onCancel}
                        disabled={isConfirming}
                    >
                        {cancelLabel}
                    </Button>
                    <Button
                        ref={confirmRef}
                        type="button"
                        variant={destructive ? "destructive" : "default"}
                        onClick={onConfirm}
                        disabled={isConfirming}
                        loading={isConfirming}
                    >
                        {confirmLabel}
                    </Button>
                </div>
            </div>
        </div>,
        document.body
    );
}
