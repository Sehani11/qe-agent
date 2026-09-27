"use client";

import AppNav from "@/components/layout/AppNav";
import PageHeader from "@/components/layout/PageHeader";
import ModelComparisonPanel from "@/components/evaluation/ModelComparisonPanel";
// TODO(evaluation-runs): section temporarily hidden — see TODO.md.
// import SavedRunsPanel from "@/components/evaluation/SavedRunsPanel";
import TrainingDataPanel from "@/components/training/TrainingDataPanel";
import TrainingRunsPanel from "@/components/training/TrainingRunsPanel";
import WipeTrainingDataPanel from "@/components/training/WipeTrainingDataPanel";
import { useTrainingDatasets } from "@/lib/hooks/useTrainingData";

/**
 * Everything about the fine-tuned BDD model, on one page.
 *
 * Training data and comparison used to be separate nav entries, which split one
 * task across two places: you supply scenarios, fine-tune on them, and the only
 * way to know whether it helped is to compare against the general model. Judging
 * the result is not a different job from producing it.
 *
 * The order follows that loop — supply the data, train on it, then measure what
 * it produced.
 */
export default function FineTunePage() {
    // Read here as well as inside the upload panel so the train button can say
    // "upload something first" instead of failing. Both calls share one React
    // Query cache entry, so this is the same request, not a second one.
    const { data: datasets } = useTrainingDatasets();

    return (
        <div className="flex min-h-screen flex-col">
            <AppNav />

            <main id="main" className="app-shell flex-1 py-10">
                <PageHeader
                    eyebrow="Fine tune"
                    title="Train a model on your Gherkin, then check it earned its place"
                    description="Supply your own scenarios so a domain-specific BDD model can be fine-tuned now, rather than waiting for captured corrections to accumulate — then put it against the general model and see whether the output is actually better."
                />

                <div className="space-y-10">
                    <section>
                        <h2>Training data</h2>
                        <p className="mb-4 mt-1.5 text-sm leading-relaxed text-muted-foreground">
                            Upload your own scenarios or ready-made training pairs. These
                            are what the fine-tuned model learns from.
                        </p>
                        <TrainingDataPanel />
                    </section>

                    {/* Between the data and the measurement, because that is
                        where it sits in the loop: nothing to train without the
                        section above, nothing to compare without this one. */}
                    <section>
                        <h2>Training</h2>
                        <p className="mb-4 mt-1.5 text-sm leading-relaxed text-muted-foreground">
                            Turn what you uploaded into a fine-tuned model, and bring the
                            result back here to download.
                        </p>
                        <TrainingRunsPanel
                            datasetCount={datasets?.length ?? 0}
                            featureCount={
                                datasets?.filter((d) => d.kind === "feature").length ?? 0
                            }
                        />
                    </section>

                    {/* TODO(evaluation-runs): temporarily hidden — see TODO.md.
                        Restore this section and the SavedRunsPanel import above.

                        It sat before the quick comparison on purpose: batches over
                        several tickets are what accumulates into a report, while
                        the check below is ad-hoc and saves nothing.

                    <section>
                        <h2>Evaluation runs</h2>
                        <p className="mb-4 mt-1.5 text-sm leading-relaxed text-muted-foreground">
                            Batch runs across several tickets, kept so the two models can
                            be compared on more than one example.
                        </p>
                        <SavedRunsPanel />
                    </section>
                    */}

                    <section>
                        <h2>Quick comparison</h2>
                        <p className="mb-4 mt-1.5 text-sm leading-relaxed text-muted-foreground">
                            Try a single ticket or pasted criteria and see both outputs side
                            by side. Nothing here is saved.
                        </p>
                        <ModelComparisonPanel />
                    </section>

                    {/* Last, and visually separated: the only control on this
                        page that destroys work rather than producing it. */}
                    <section>
                        <h2>Start over</h2>
                        <p className="mb-4 mt-1.5 text-sm leading-relaxed text-muted-foreground">
                            Clear everything above and begin again from an empty page.
                        </p>
                        <WipeTrainingDataPanel />
                    </section>
                </div>
            </main>
        </div>
    );
}
