"use client";

import { Switch } from "@/components/ui/switch";
import { useTrainingOptIn } from "@/lib/hooks/useTrainingOptIn";

/**
 * Consent for this session's scenarios to be used in future fine-tuning.
 *
 * Sits beside generation because that is where the content is captured, and
 * consent belongs to the moment of capture — the flag is stamped onto each row
 * as it is written and never re-evaluated, so flipping this changes what
 * happens next, not what already exists.
 *
 * Disabled when the deployment forbids training outright: the backend ANDs
 * this with its own policy, so leaving the control live there would offer a
 * choice that cannot take effect.
 */
export default function TrainingOptInToggle({ className }: { className?: string }) {
    // `effective` rather than re-deriving `allowed && requested` here: the hook
    // owns how the request and the policy combine, and a second spelling of that
    // rule is one the next change to it would miss.
    const { setRequested, allowed, effective } = useTrainingOptIn();

    return (
        <Switch
            checked={effective}
            onCheckedChange={setRequested}
            disabled={!allowed}
            label="Allow use for fine-tuning"
            className={className}
        />
    );
}
