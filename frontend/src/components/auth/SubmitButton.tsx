"use client";

import { useFormStatus } from "react-dom";

import { Button } from "@/components/ui/button";

/**
 * Submit control for a server action. `useFormStatus` gives us the pending
 * state without lifting the form into client state, so the button can show a
 * loader the moment the action starts.
 */
export default function SubmitButton({
    children,
    pendingLabel,
    variant = "default",
    className,
}: {
    children: React.ReactNode;
    /** Shown while in flight. Keep the same verb: "Sign in" → "Signing in". */
    pendingLabel: string;
    variant?: "default" | "outline";
    className?: string;
}) {
    const { pending } = useFormStatus();

    return (
        <Button
            type="submit"
            size="lg"
            variant={variant}
            loading={pending}
            className={className}
        >
            {pending ? pendingLabel : children}
        </Button>
    );
}
