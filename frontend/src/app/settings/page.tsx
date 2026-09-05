"use client";

import React from "react";
import { useRouter } from "next/navigation";
import {
    AlertTriangle,
    BookOpen,
    Database,
    FolderGit2,
    Github,
    KeyRound,
    Layers,
    Sparkles,
    Trash2,
} from "lucide-react";

import AppNav from "@/components/layout/AppNav";
import NoProjectSelected from "@/components/project/NoProjectSelected";
import PageHeader from "@/components/layout/PageHeader";
import { Button } from "@/components/ui/button";
import { ConfirmModal } from "@/components/ui/confirm-modal";
import ModelMenu, { type ModelMenuGroup } from "@/components/model/ModelMenu";
import { Field, Input } from "@/components/ui/field";
import { InlineLoader } from "@/components/ui/loaders";
import { useToast } from "@/components/ui/toast";
import {
    useDeleteProject,
    useProject,
    useUpdateProject,
} from "@/lib/hooks/useProjects";
import { useProjectStore } from "@/lib/stores/projectStore";
import { findModelOption, LLM_PROVIDER_GROUPS, modelIdOf } from "@/lib/types/llm";
import type { ProjectConfigUpdate } from "@/lib/types/project";

/** Sentinel for "this project has no model preference". */
const NO_PREFERENCE = "";

export default function SettingsPage() {
    // No useSyncActiveProject() here: AppNav below mounts ProjectSwitcher, which
    // owns that reconciliation for every page. A second copy would be a second
    // place deciding what the active project is.
    const router = useRouter();
    const activeProjectId = useProjectStore((s) => s.activeProjectId);
    const { data: project, isLoading } = useProject(activeProjectId);
    const updateProject = useUpdateProject(activeProjectId);
    const deleteProject = useDeleteProject();
    const toast = useToast();

    const [confirmingDelete, setConfirmingDelete] = React.useState(false);

    const handleDeleteProject = () => {
        if (!project) return;
        deleteProject.mutate(project.id, {
            onSuccess: () => {
                toast.success("Project deleted", {
                    description: `${project.name} and its sessions are gone.`,
                });
                // The settings page has nothing left to configure once its
                // project is gone — home is where every other exit (logo,
                // "Start a session") already lands.
                router.push("/");
            },
            onError: () => {
                toast.error("Could not delete project", {
                    description: "It is still there. Try again.",
                });
            },
            onSettled: () => setConfirmingDelete(false),
        });
    };

    // Non-secret fields are seeded from the server copy. Secrets are not: the
    // server never sends them, so these boxes start empty and mean "set a new
    // one". A blank box is NOT sent, so leaving it alone keeps what is stored.
    const [form, setForm] = React.useState<ProjectConfigUpdate>({});
    const [tokens, setTokens] = React.useState<Record<string, string>>({});

    // Adjust-during-render rather than an effect: switching projects must not
    // paint the previous project's values into this project's form for a frame.
    const [seededId, setSeededId] = React.useState<string | null>(null);
    if (project && project.id !== seededId) {
        setSeededId(project.id);
        setForm({
            name: project.name,
            jira_base_url: project.jira_base_url,
            jira_user_email: project.jira_user_email,
            confluence_base_url: project.confluence_base_url,
            confluence_user_email: project.confluence_user_email,
            github_repo: project.github_repo,
            llm_provider: project.llm_provider,
            llm_model: project.llm_model,
            embedding_provider: project.embedding_provider,
            pinecone_index_name: project.pinecone_index_name,
        });
        setTokens({});
    }

    const set = (key: keyof ProjectConfigUpdate) => (value: string) =>
        setForm((prev) => ({ ...prev, [key]: value }));

    const selectedModelId = modelIdOf(form.llm_provider, form.llm_model) || NO_PREFERENCE;

    // Vendor choices for the knowledge base. The dimension is part of the label
    // because it is the number that has to match the Pinecone index — the one
    // fact someone needs while choosing, not after the ingest fails.
    const EMBEDDING_PROVIDER_LABELS: Record<string, string> = {
        [NO_PREFERENCE]: "No preference",
        openai: "OpenAI · text-embedding-3-small",
        voyage: "Voyage · voyage-4",
    };

    const embeddingProviderGroups: ModelMenuGroup[] = [
        {
            id: "unset",
            label: "Project default",
            items: [
                {
                    id: NO_PREFERENCE,
                    label: "No preference",
                    note: "Falls back to the vendor the server is configured with.",
                },
            ],
        },
        {
            id: "vendors",
            label: "Embedding vendor",
            items: [
                {
                    id: "openai",
                    label: "OpenAI · text-embedding-3-small",
                    note: "1536 dimensions. Billed to the server's OpenAI account.",
                },
                {
                    id: "voyage",
                    label: "Voyage · voyage-4",
                    note: "1024 dimensions. 200M tokens free, then $0.06/1M.",
                },
            ],
        },
    ];

    // "No preference" leads, as its own group: it is not one of the models, it
    // is the choice to defer to the server's own default.
    const projectModelGroups: ModelMenuGroup[] = [
        {
            id: "unset",
            label: "Project default",
            items: [
                {
                    id: NO_PREFERENCE,
                    label: "No preference",
                    note: "Falls back to the model the server is configured with.",
                },
            ],
        },
        ...LLM_PROVIDER_GROUPS.map((group) => ({
            id: group.id,
            label: group.label,
            items: group.models.map((option) => ({
                id: option.id,
                label: option.label,
                note: option.note,
                warning: option.supportsTools ? undefined : "Cannot run verification",
            })),
        })),
    ];

    const handleSave = async () => {
        // Only non-empty token boxes are sent. An untouched box must stay out of
        // the payload entirely — sending "" would CLEAR the stored credential.
        const payload: ProjectConfigUpdate = { ...form };
        for (const [key, value] of Object.entries(tokens)) {
            if (value.trim()) {
                (payload as Record<string, string>)[key] = value.trim();
            }
        }

        // Read BEFORE the request: a successful save writes the new project into
        // the query cache, so comparing afterwards would compare it with itself.
        const previousModelId = modelIdOf(project?.llm_provider, project?.llm_model);
        const savedModelId = modelIdOf(payload.llm_provider, payload.llm_model);
        const modelChanged = Boolean(savedModelId) && savedModelId !== previousModelId;

        try {
            await updateProject.mutateAsync(payload);
            setTokens({});

            // Nothing to sync any more: the project IS the model, so saving it
            // is the whole act. The message still calls out a model change,
            // because that is the edit most likely to surprise someone who came
            // here to change something else.
            toast.success("Project saved", {
                description: modelChanged
                    ? `Now using ${findModelOption(savedModelId)?.label ?? savedModelId} for this project.`
                    : "New sessions will pick these up automatically.",
            });
        } catch {
            toast.error("Could not save project", {
                description: "Your changes were not stored. Try again.",
            });
        }
    };

    if (!activeProjectId) {
        return (
            <div className="flex min-h-screen flex-col">
                <AppNav />
                <main id="main" className="app-shell flex-1 py-10">
                    {/* Deleting the last project lands here. Pointing at the
                        switcher was an instruction, not an option: the one
                        control that resolves this was a create row hidden
                        inside a nav dropdown, so the page named the fix and
                        then made the user go and find it. */}
                    <PageHeader
                        eyebrow="Settings"
                        title="No project selected"
                        description="Sessions, knowledge and settings all belong to a project. Create one to carry on."
                    />
                    <NoProjectSelected purpose="configure" />
                </main>
            </div>
        );
    }

    return (
        <div className="flex min-h-screen flex-col">
            <AppNav />
            <main id="main" className="app-shell flex-1 py-10">
                <PageHeader
                    eyebrow="Settings"
                    title="Project configuration"
                    description="These become the defaults for this project's sessions. Every one can still be overridden at the moment you run something."
                />

                {isLoading || !project ? (
                    <InlineLoader>Loading project</InlineLoader>
                ) : (
                    <div className="flex flex-col gap-5">
                        <Section title="Project" icon={FolderGit2}>
                            <Field label="Name" htmlFor="project-name">
                                <Input
                                    id="project-name"
                                    value={form.name ?? ""}
                                    onChange={(e) => set("name")(e.target.value)}
                                />
                            </Field>
                        </Section>

                        {/* Side by side, mirroring the two Atlassian ingestion
                            cards on /knowledge: they are the same pair of
                            integrations and are usually configured together. */}
                        <div className="grid gap-5 lg:grid-cols-2">
                            <Section
                                title="Jira"
                                icon={Database}
                                description="Where tickets are fetched from."
                            >
                                <Field label="Base URL" htmlFor="jira-url">
                                    <Input
                                        id="jira-url"
                                        mono
                                        placeholder="https://acme.atlassian.net"
                                        value={form.jira_base_url ?? ""}
                                        onChange={(e) => set("jira_base_url")(e.target.value)}
                                    />
                                </Field>
                                <Field label="User email" htmlFor="jira-email">
                                    <Input
                                        id="jira-email"
                                        value={form.jira_user_email ?? ""}
                                        onChange={(e) =>
                                            set("jira_user_email")(e.target.value)
                                        }
                                    />
                                </Field>
                                <SecretField
                                    id="jira-token"
                                    label="API token"
                                    isSet={project.has_jira_token}
                                    value={tokens.jira_api_token ?? ""}
                                    onChange={(v) =>
                                        setTokens((t) => ({ ...t, jira_api_token: v }))
                                    }
                                />
                            </Section>

                            <Section
                                title="Confluence"
                                icon={BookOpen}
                                description="Pages ingested into this project's knowledge base."
                            >
                                <Field label="Base URL" htmlFor="confluence-url">
                                    <Input
                                        id="confluence-url"
                                        mono
                                        placeholder="https://acme.atlassian.net/wiki"
                                        value={form.confluence_base_url ?? ""}
                                        onChange={(e) =>
                                            set("confluence_base_url")(e.target.value)
                                        }
                                    />
                                </Field>
                                <Field label="User email" htmlFor="confluence-email">
                                    <Input
                                        id="confluence-email"
                                        value={form.confluence_user_email ?? ""}
                                        onChange={(e) =>
                                            set("confluence_user_email")(e.target.value)
                                        }
                                    />
                                </Field>
                                <SecretField
                                    id="confluence-token"
                                    label="API token"
                                    isSet={project.has_confluence_token}
                                    value={tokens.confluence_api_token ?? ""}
                                    onChange={(v) =>
                                        setTokens((t) => ({ ...t, confluence_api_token: v }))
                                    }
                                />
                            </Section>
                        </div>

                        <Section
                            title="GitHub"
                            icon={Github}
                            description="The repository verification reads by default."
                        >
                            <Field label="Repository" htmlFor="github-repo">
                                <Input
                                    id="github-repo"
                                    mono
                                    placeholder="owner/repo"
                                    value={form.github_repo ?? ""}
                                    onChange={(e) => set("github_repo")(e.target.value)}
                                />
                            </Field>
                            <SecretField
                                id="github-token"
                                label="Access token"
                                isSet={project.has_github_token}
                                value={tokens.github_access_token ?? ""}
                                onChange={(v) =>
                                    setTokens((t) => ({ ...t, github_access_token: v }))
                                }
                            />
                        </Section>

                        <Section
                            title="Model"
                            icon={Sparkles}
                            description="The default this project starts from. API keys are set by whoever runs the server, not here."
                        >
                            {/* The same dropdown the top bar uses, not a native
                                <select>. Picking a model is the identical act in
                                both places, and a native control drops an
                                OS-drawn list that ignores the app's radius,
                                grouping and check marks — which is what made
                                this one look foreign. Reusing ModelMenu also
                                brings the per-model notes and the "cannot run
                                verification" warning along for free. */}
                            <Field label="Default model" htmlFor="project-model">
                                <ModelMenu
                                    id="project-model"
                                    groups={projectModelGroups}
                                    activeId={selectedModelId}
                                    onSelect={(id) => {
                                        const option = findModelOption(id);
                                        setForm((prev) => ({
                                            ...prev,
                                            // An unknown id is the "no preference"
                                            // row, which clears both halves — they
                                            // are only meaningful as a pair.
                                            llm_provider: option?.llm_provider ?? "",
                                            llm_model: option?.llm_model ?? "",
                                        }));
                                    }}
                                    triggerLabel={
                                        findModelOption(selectedModelId)?.label ??
                                        "No preference"
                                    }
                                    srLabel="Default model"
                                    hint="Every session in this project runs on it — chat, verification and BDD generation."
                                    className="w-full"
                                />
                            </Field>
                        </Section>

                        <Section
                            title="Knowledge base"
                            icon={Layers}
                            description="Which vendor embeds this project's documents, and the index they live in. API keys are set by whoever runs the server, not here."
                        >
                            {/* Deliberately one card. The vendor and the index are
                                a pair: an index holds vectors from exactly one
                                embedding model, and a query only matches vectors
                                that same model produced. Splitting them across two
                                cards would invite changing one alone, which is the
                                single way to break retrieval here. */}
                            {/* ModelMenu again, not a native <select>. It is
                                presentational and caller-driven despite the
                                name, and an OS-drawn list here would look as
                                foreign as it did on the model picker. */}
                            <Field label="Embedding provider" htmlFor="embedding-provider">
                                <ModelMenu
                                    id="embedding-provider"
                                    groups={embeddingProviderGroups}
                                    activeId={form.embedding_provider || NO_PREFERENCE}
                                    onSelect={(id) =>
                                        set("embedding_provider")(
                                            id === NO_PREFERENCE ? "" : id
                                        )
                                    }
                                    triggerLabel={
                                        EMBEDDING_PROVIDER_LABELS[
                                            form.embedding_provider || NO_PREFERENCE
                                        ] ?? "No preference"
                                    }
                                    srLabel="Embedding provider"
                                    hint="Applies to every source ingested into this project's knowledge base."
                                    className="w-full"
                                />
                            </Field>

                            <Field label="Pinecone index" htmlFor="pinecone-index">
                                <Input
                                    id="pinecone-index"
                                    placeholder="qe-agent"
                                    value={form.pinecone_index_name ?? ""}
                                    onChange={(e) =>
                                        set("pinecone_index_name")(e.target.value)
                                    }
                                />
                            </Field>

                            {/* Stated up front rather than discovered on the next
                                ingest. The dimension mismatch does fail loudly, but
                                only after someone has already changed the setting
                                and moved on. */}
                            <div
                                className="gutter-rule rounded-md border border-pending/30 bg-pending-soft/60 px-3 py-2.5"
                                data-signal="pending"
                            >
                                <p className="eyebrow text-pending-ink">
                                    Changing either field needs a new index
                                </p>
                                <p className="mt-1.5 text-[0.8125rem] leading-relaxed text-foreground">
                                    Vectors from one embedding model cannot be read by
                                    another, and an index&apos;s dimension is fixed when
                                    it is created — 1536 for OpenAI, 1024 for Voyage.
                                    Point this at a new index of the matching size and
                                    re-ingest this project&apos;s sources; anything
                                    already indexed stays in the old one and will not be
                                    found.
                                </p>
                            </div>
                        </Section>

                        {/* One save for the whole form: the cards are sections of
                            a single PATCH, not five independently saved things,
                            so a per-card button would imply an autonomy they do
                            not have. Carries the card's own ground so it reads
                            as part of the stack rather than loose page furniture. */}
                        <div className="flex flex-wrap items-center gap-3 rounded-lg border border-rule bg-card p-4">
                            <Button
                                onClick={() => void handleSave()}
                                loading={updateProject.isPending}
                            >
                                {updateProject.isPending ? "Saving" : "Save changes"}
                            </Button>
                            <p className="text-xs leading-relaxed text-muted-foreground">
                                Credentials are encrypted before storage and never sent
                                back to the browser.
                            </p>
                        </div>

                        <section className="flex flex-col gap-3 rounded-lg border border-fail/30 bg-fail-soft/40 p-4">
                            <div className="flex items-center gap-2">
                                <AlertTriangle
                                    className="h-4 w-4 text-fail-ink"
                                    aria-hidden="true"
                                />
                                <h2 className="text-sm text-fail-ink">Danger zone</h2>
                            </div>
                            <p className="text-xs leading-relaxed text-muted-foreground">
                                Deleting this project removes every session inside it —
                                scenarios, verdicts, and chat history. This can&apos;t be
                                undone.
                            </p>
                            <div>
                                <Button
                                    variant="destructive"
                                    size="sm"
                                    onClick={() => setConfirmingDelete(true)}
                                >
                                    <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
                                    Delete this project
                                </Button>
                            </div>
                        </section>
                    </div>
                )}
            </main>

            <ConfirmModal
                open={confirmingDelete}
                destructive
                title="Delete project"
                description={
                    <>
                        Delete{" "}
                        <span className="font-semibold text-foreground">
                            {project?.name ?? "this project"}
                        </span>
                        ? Every session inside it — scenarios, verdicts, and chat
                        history — will be gone. This can&apos;t be undone.
                    </>
                }
                confirmLabel="Delete"
                isConfirming={deleteProject.isPending}
                onConfirm={handleDeleteProject}
                onCancel={() => setConfirmingDelete(false)}
            />
        </div>
    );
}

/**
 * One settings card.
 *
 * Same shape as the ingestion cards on /knowledge — hairline rule, card ground,
 * an icon-led heading — so a project's Jira section and the Jira ingestion form
 * it feeds read as the same kind of object. The icons are matched deliberately:
 * Confluence is a book and Jira a database on both pages, so the pairing is
 * recognisable rather than merely decorative.
 */
function Section({
    title,
    description,
    icon: Icon,
    children,
}: {
    title: string;
    description?: string;
    icon: React.ComponentType<{ className?: string; "aria-hidden"?: boolean }>;
    children: React.ReactNode;
}) {
    return (
        <section className="flex flex-col gap-3 rounded-lg border border-rule bg-card p-4">
            <div className="flex items-center gap-2">
                <Icon className="h-4 w-4 text-keyword" aria-hidden={true} />
                <h2 className="text-sm">{title}</h2>
            </div>
            {description && (
                <p className="text-xs leading-relaxed text-muted-foreground">
                    {description}
                </p>
            )}
            <div className="space-y-3">{children}</div>
        </section>
    );
}

/**
 * A credential box that never displays what is stored.
 *
 * The server does not send secrets back, so there is nothing to prefill — and
 * a masked placeholder would be a lie about what typing here does. Instead the
 * label says whether one is set, and an empty box means "leave it alone".
 */
function SecretField({
    id,
    label,
    isSet,
    value,
    onChange,
}: {
    id: string;
    label: string;
    isSet: boolean;
    value: string;
    onChange: (value: string) => void;
}) {
    return (
        <Field
            label={label}
            htmlFor={id}
            hint={
                <span className="inline-flex items-center gap-1">
                    <KeyRound className="h-3 w-3" aria-hidden="true" />
                    {isSet ? "Configured" : "Not set"}
                </span>
            }
            description={
                isSet
                    ? "Leave blank to keep the stored token, or type a new one to replace it."
                    : "Stored encrypted. It is never sent back to the browser."
            }
        >
            <Input
                id={id}
                type="password"
                autoComplete="off"
                mono
                placeholder={isSet ? "••••••••  (unchanged)" : "Paste a token"}
                value={value}
                onChange={(e) => onChange(e.target.value)}
            />
        </Field>
    );
}
