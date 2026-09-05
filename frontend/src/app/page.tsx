"use client";

import { useRouter } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { ArrowRight } from "lucide-react";

import AppNav from "@/components/layout/AppNav";
import { Button, buttonVariants } from "@/components/ui/button";
import { useSessionContext } from "@/context/SessionContext";

/* The three stages are a genuine sequence — you cannot verify before you
   generate — so numbering them carries real information rather than decoration. */
const STAGES = [
    {
        keyword: "Given",
        title: "A ticket with acceptance criteria",
        body: "Paste a Jira ID or URL. The agent pulls the criteria and keeps them beside your scenarios.",
    },
    {
        keyword: "When",
        title: "The agent writes the Gherkin",
        body: "Each criterion becomes a scenario you can edit in place, with the clause it came from still attached.",
    },
    {
        keyword: "Then",
        title: "Every scenario meets your code",
        body: "Point it at a repo. Each scenario comes back passed or failed, with the file and line that decided it.",
    },
];

export default function Home() {
    const router = useRouter();
    const queryClient = useQueryClient();
    const { resetSession } = useSessionContext();

    const handleStartNewSession = () => {
        resetSession();
        // Drop any cached session/BDD/verification data from past sessions so it
        // can't be served back to the new session page on mount.
        queryClient.removeQueries({ queryKey: ["sessions"] });
        router.push(`/session/${crypto.randomUUID()}`);
    };

    return (
        <div className="flex min-h-screen flex-col">
            <AppNav />

            <main id="main" className="flex-1">
                {/* Hero — the product's own artifact, written out. A .feature file
                    is the most characteristic thing in this subject's world, so
                    it opens the page instead of a stat tile. */}
                <section className="border-b border-rule">
                    <div className="app-shell grid items-center gap-10 py-14 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.05fr)] lg:gap-14 lg:py-20">
                        <div>
                            <p className="eyebrow text-pass">Acceptance criteria → verified coverage</p>

                            <h1 className="mt-4">
                                Your ticket already
                                <br />
                                describes the tests.
                            </h1>

                            <p className="mt-5 max-w-md text-base leading-relaxed text-muted-foreground">
                                qe-agent turns the acceptance criteria on a Jira ticket into
                                Gherkin scenarios, then checks each one against your repository
                                and tells you which are actually implemented.
                            </p>

                            <div className="mt-8 flex flex-col gap-3 sm:flex-row">
                                <Button size="lg" onClick={handleStartNewSession}>
                                    Start a session
                                    <ArrowRight className="h-4 w-4" aria-hidden="true" />
                                </Button>
                                {/* A navigation target, so it stays a real link:
                                    styled with the button's own variants rather
                                    than wrapped in Button, which would put
                                    role="button" on the anchor. */}
                                <Link
                                    href="/sessions"
                                    className={buttonVariants({ variant: "outline", size: "lg" })}
                                >
                                    Open past sessions
                                </Link>
                            </div>
                        </div>

                        {/* The block is decorative in the sense that it is not
                            editable, but its content is the real product copy —
                            so it stays in the reading order rather than being
                            hidden from assistive tech. */}
                        <figure className="min-w-0">
                            <div className="overflow-hidden rounded-lg border border-rule bg-card">
                                <div className="flex items-center gap-2 border-b border-rule bg-surface-raised px-4 py-2.5">
                                    <span className="h-2 w-2 rounded-full bg-pass" aria-hidden="true" />
                                    <span className="font-mono text-xs text-muted-foreground">
                                        coverage.feature
                                    </span>
                                </div>

                                <pre className="scrollbar-thin overflow-x-auto px-4 py-5 font-mono text-[0.8125rem] leading-[1.85] sm:px-6 sm:text-sm">
                                    <code>
                                        <span className="kw">Feature:</span>{" "}
                                        <span className="text-foreground">
                                            Turn a ticket into verified coverage
                                        </span>
                                        {"\n\n  "}
                                        <span className="kw">Scenario:</span>{" "}
                                        <span className="text-foreground">
                                            A criterion becomes a checked scenario
                                        </span>
                                        {"\n    "}
                                        <span className="kw">Given</span>{" "}
                                        <span className="text-muted-foreground">
                                            a Jira ticket with acceptance criteria
                                        </span>
                                        {"\n    "}
                                        <span className="kw">When</span>{" "}
                                        <span className="text-muted-foreground">
                                            the agent writes the Gherkin
                                        </span>
                                        {"\n    "}
                                        <span className="kw">Then</span>{" "}
                                        <span className="text-muted-foreground">
                                            each scenario is checked against the repo
                                        </span>
                                        {"\n    "}
                                        <span className="kw">And</span>{" "}
                                        <span className="text-muted-foreground">
                                            the gaps come back with a file and a line
                                        </span>
                                        <span className="caret" />
                                    </code>
                                </pre>
                            </div>
                        </figure>
                    </div>
                </section>

                {/* Stages — the keyword gutter carries the sequence. */}
                <section className="app-shell py-14 lg:py-16">
                    <h2 className="sr-only">How a session runs</h2>
                    <ol className="grid gap-px overflow-hidden rounded-lg border border-rule bg-rule sm:grid-cols-3">
                        {STAGES.map(({ keyword, title, body }, index) => (
                            <li key={keyword} className="bg-card p-5 lg:p-6">
                                <div className="flex items-baseline gap-2.5">
                                    <span className="font-mono text-xs text-muted-foreground/70 tabular-nums">
                                        {String(index + 1).padStart(2, "0")}
                                    </span>
                                    <span className="kw text-sm">{keyword}</span>
                                </div>
                                <h3 className="mt-3">{title}</h3>
                                <p className="mt-2 text-[0.8125rem] leading-relaxed text-muted-foreground">
                                    {body}
                                </p>
                            </li>
                        ))}
                    </ol>
                </section>
            </main>

            <footer className="border-t border-rule">
                <div className="app-shell flex flex-wrap items-center justify-between gap-3 py-5">
                    <p className="font-mono text-xs text-muted-foreground">qe-agent</p>
                    <p className="text-xs text-muted-foreground">
                        Gherkin generation and code verification for QE teams.
                    </p>
                </div>
            </footer>
        </div>
    );
}
