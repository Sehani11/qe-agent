"use client";

import { useSessionContext } from "@/context/SessionContext";

export default function TerminalProgressLog() {
    const { jiraTicketId, bddContent, isIngesting, isGeneratingBDD, isVerifying, globalError } = useSessionContext();

    const isWorking = isIngesting || isGeneratingBDD || isVerifying;
    const progressWidth = isIngesting ? "w-2/5" : isGeneratingBDD ? "w-4/5" : bddContent ? "w-full" : jiraTicketId ? "w-1/2" : "w-0";

    let statusTitle = "Ready to Start";
    let statusDescription = "Fetch a Jira ticket to begin generating BDD scenarios.";
    let accentClasses = "bg-sky-100 text-sky-800 border-sky-200";

    if (globalError) {
        statusTitle = "Something needs attention";
        statusDescription = globalError;
        accentClasses = "bg-rose-100 text-rose-800 border-rose-200";
    } else if (isIngesting) {
        statusTitle = "Fetching Jira ticket";
        statusDescription = "We are pulling the latest ticket details and preparing them for BDD generation.";
        accentClasses = "bg-sky-100 text-sky-800 border-sky-200";
    } else if (isGeneratingBDD) {
        statusTitle = "Generating BDD scenarios";
        statusDescription = "The model is turning the ticket details into structured Gherkin scenarios.";
        accentClasses = "bg-blue-100 text-blue-800 border-blue-200";
    } else if (bddContent) {
        statusTitle = "BDD is ready";
        statusDescription = "Review the generated scenarios in the editor, then download a feature file or CSV.";
        accentClasses = "bg-indigo-100 text-indigo-800 border-indigo-200";
    } else if (jiraTicketId) {
        statusTitle = "Ticket loaded";
        statusDescription = "You can now generate BDD scenarios from the fetched Jira content.";
        accentClasses = "bg-sky-100 text-sky-800 border-sky-200";
    }

    return (
        <div className="flex h-full flex-col overflow-hidden rounded-3xl border border-sky-100 bg-white shadow-[0_20px_70px_-30px_rgba(14,116,144,0.22)]">
            <div className="border-b border-slate-200 px-5 py-4">
                <div className="flex items-center justify-between gap-4">
                    <div>
                        <h2 className="text-base font-semibold text-slate-900">Status</h2>
                        <p className="text-sm text-slate-500">A simple overview of where the flow is right now.</p>
                    </div>
                    <span className={`rounded-full px-3 py-1 text-xs font-semibold ${isWorking ? "bg-sky-100 text-sky-800" : "bg-blue-100 text-blue-800"}`}>
                        {isWorking ? "Working" : "Ready"}
                    </span>
                </div>
            </div>

            <div className="min-h-0 flex-1 overflow-y-auto px-5 py-5">
                <div className={`rounded-3xl border px-5 py-5 ${accentClasses}`}>
                    <div className="flex items-start gap-3">
                        <span className={`mt-1 inline-block h-3 w-3 rounded-full ${isWorking ? "animate-ping bg-current" : "bg-current"}`}></span>
                        <div>
                            <h3 className="text-sm font-semibold">{statusTitle}</h3>
                            <p className="mt-1 text-sm leading-6 opacity-90">{statusDescription}</p>
                        </div>
                    </div>

                    <div className="mt-4 space-y-3">
                        <div className="flex items-center gap-3">
                            <span className={`inline-block h-5 w-5 rounded-full border-2 border-current/30 border-t-current ${isWorking ? "animate-spin" : "opacity-70"}`}></span>
                            <span className="text-xs font-medium uppercase tracking-wide opacity-80">
                                {isWorking ? "Processing" : globalError ? "Paused" : "Idle"}
                            </span>
                        </div>

                        <div className="h-2 overflow-hidden rounded-full bg-white/50">
                            <div className={`h-full rounded-full bg-current transition-all duration-500 ${progressWidth} ${isWorking ? "animate-pulse" : ""}`}></div>
                        </div>
                    </div>
                </div>

                <div className="mt-4 grid gap-2">
                    <div className="rounded-2xl bg-sky-50 px-3 py-2.5">
                        <p className="text-xs font-semibold uppercase tracking-wide text-sky-800">Step 1</p>
                        <p className="mt-0.5 text-xs text-slate-700">Fetch the Jira ticket details.</p>
                    </div>
                    <div className="rounded-2xl bg-blue-50 px-3 py-2.5">
                        <p className="text-xs font-semibold uppercase tracking-wide text-blue-800">Step 2</p>
                        <p className="mt-0.5 text-xs text-slate-700">Generate BDD scenarios from the ticket content.</p>
                    </div>
                    <div className="rounded-2xl bg-indigo-50 px-3 py-2.5">
                        <p className="text-xs font-semibold uppercase tracking-wide text-indigo-800">Step 3</p>
                        <p className="mt-0.5 text-xs text-slate-700">Review and download the final output.</p>
                    </div>
                </div>
            </div>
        </div>
    );
}
