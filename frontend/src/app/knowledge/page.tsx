"use client";

import AppNav from "@/components/layout/AppNav";
import PageHeader from "@/components/layout/PageHeader";
import KnowledgeBasePanel from "@/components/knowledge/KnowledgeBasePanel";
import KnowledgeChatPanel from "@/components/knowledge/KnowledgeChatPanel";

export default function KnowledgePage() {
    return (
        <div className="flex min-h-screen flex-col">
            <AppNav />

            <main id="main" className="app-shell flex-1 py-10">
                <PageHeader
                    eyebrow="Knowledge"
                    title="Project context"
                    description="Ingest Confluence pages and Jira tickets so verification can draw on what your team already wrote down, and index your repository so it can find the code that implements a scenario instead of hunting for it."
                />

                <div className="space-y-8">
                    <KnowledgeBasePanel />

                    <section>
                        <h2>Project Q&amp;A</h2>
                        <p className="mb-3 mt-1.5 text-sm leading-relaxed text-muted-foreground">
                            Ask a question and get an answer drawn from the sources above.
                        </p>
                        <KnowledgeChatPanel />
                    </section>
                </div>
            </main>
        </div>
    );
}
