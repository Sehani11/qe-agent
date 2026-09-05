"use client";

import { Switch } from "@/components/ui/switch";
import { useModel } from "@/providers/ModelProvider";

/**
 * Opts BDD generation into the fine-tuned model.
 *
 * A switch rather than an entry in the model picker: the fine-tuned endpoint
 * serves one fixed model and only generates scenarios — it cannot chat or
 * verify — so it is not an alternative to the app-wide model choice, it is a
 * switch on top of it. Off, generation uses whatever the nav selector holds;
 * on, that selection still stands for everything else on the page, and is what
 * the backend falls back to if the fine-tuned endpoint is unreachable.
 *
 * Both halves of what it shows come from `useModel`, and that is the point.
 * `bddProvider` there is already resolved against whether a model is being
 * served — the same value that goes into the request body — so the switch
 * cannot end up drawn one way while `/bdd/generate` is asked for the other.
 * Reading availability separately here is what previously allowed exactly that.
 */
export default function FineTunedToggle({ className }: { className?: string }) {
    const { bddProvider, setBddProvider, fineTuned } = useModel();

    return (
        <Switch
            checked={bddProvider === "fine_tuned"}
            onCheckedChange={(on) =>
                setBddProvider(on ? "fine_tuned" : "general_llm")
            }
            disabled={fineTuned.isLoading || !fineTuned.available}
            label="Use fine-tuned model"
            // Absent while loading rather than assumed either way: flashing
            // "no model" at someone who has one is as wrong as the state this
            // guards against.
            description={
                !fineTuned.isLoading && !fineTuned.available
                    ? fineTuned.detail
                    : undefined
            }
            className={className}
        />
    );
}
