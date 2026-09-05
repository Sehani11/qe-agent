/**
 * settings/page.tsx — the card treatment, and the write-only credential boxes.
 *
 * The layout assertions are not decoration-checking for its own sake: the
 * settings sections are meant to read as the same kind of object as the
 * ingestion cards on /knowledge, and that similarity is carried entirely by a
 * shared set of utility classes. Nothing else would notice them drifting apart.
 */
import React from "react";
// The shared shell, not a bare RTL render: the page calls useToast, so it needs
// the same provider tree the app mounts it in.
import { render, screen, fireEvent, waitFor, act, within } from "@/test/test-utils";
import { describe, it, expect, vi, beforeEach } from "vitest";

const push = vi.fn();
vi.mock("next/navigation", () => ({
    useRouter: () => ({ push, replace: vi.fn() }),
    usePathname: () => "/settings",
}));

// The nav mounts the project switcher and model picker, both of which reach the
// API. None of that is what these assert, and a real AppNav would drag the
// whole provider tree in with it.
vi.mock("@/components/layout/AppNav", () => ({
    default: () => <nav data-testid="app-nav" />,
}));

vi.mock("@/lib/api/client", () => ({
    default: { get: vi.fn(), post: vi.fn(), patch: vi.fn() },
}));

const BASE_PROJECT = {
    id: "p1",
    name: "Acme",
    jira_base_url: "https://acme.atlassian.net",
    jira_user_email: "qa@acme.test",
    has_jira_token: true,
    confluence_base_url: "",
    confluence_user_email: "",
    has_confluence_token: false,
    github_repo: "acme/app",
    has_github_token: false,
    llm_provider: "",
    llm_model: "",
    embedding_provider: "",
    pinecone_index_name: "",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
};

// Mutable so a test can start from a project that already has a default set.
let currentProject = { ...BASE_PROJECT };
const mutateAsync = vi.fn();

/** Options passed to the last `deleteProject.mutate(id, options)` call. */
type MutateOptions = {
    onSuccess?: () => void;
    onError?: () => void;
    onSettled?: () => void;
};
// Simulates a successful delete by default, invoking the caller's callbacks
// the way react-query would — the page's own onSuccess (toast + redirect)
// only runs through those callbacks, so a bare vi.fn() would never exercise it.
const deleteProjectMutate = vi.fn((_id: string, options?: MutateOptions) => {
    options?.onSuccess?.();
    options?.onSettled?.();
});
// The page no longer reads the project list, but a test overrides this to
// prove the delete button stays enabled on a caller's only project.
const useProjectListMock = vi.fn<() => { data: { id: string; name?: string }[] }>(
    () => ({ data: [{ id: "p1" }, { id: "p2" }] })
);

const createProjectMutate = vi.fn();

vi.mock("@/lib/hooks/useProjects", () => ({
    useProject: () => ({ data: currentProject, isLoading: false }),
    useUpdateProject: () => ({
        mutateAsync: (payload: unknown) => mutateAsync(payload),
        isPending: false,
    }),
    useProjectList: () => useProjectListMock(),
    useDeleteProject: () => ({ mutate: deleteProjectMutate, isPending: false }),
    useCreateProject: () => ({ mutate: createProjectMutate, isPending: false }),
}));

// Null is the state deleting the last project leaves behind, and the one the
// page used to answer with a loader that never resolved.
let activeProjectId: string | null = "p1";

const setActiveProject = vi.fn();

vi.mock("@/lib/stores/projectStore", () => ({
    useProjectStore: (
        selector: (s: {
            activeProjectId: string | null;
            setActiveProject: (id: string | null) => void;
        }) => unknown
    ) => selector({ activeProjectId, setActiveProject }),
}));

import SettingsPage from "@/app/settings/page";
import { useModel } from "@/providers/ModelProvider";

/** Where ModelProvider keeps this browser's model choice. */
const STORAGE_KEY = "qe-agent-model";
/** And the deployment default it was chosen against. */
const DEFAULT_STAMP_KEY = "qe-agent-model-default";

/** The card contract, copied from the ingestion cards on /knowledge. */
const CARD_CLASSES = ["rounded-lg", "border", "border-rule", "bg-card", "p-4"];

function renderPage() {
    return render(<SettingsPage />);
}

/** The <section> wrapping a heading, which is the card element itself. */
function cardFor(heading: string): HTMLElement {
    const el = screen.getByRole("heading", { name: heading }).closest("section");
    expect(el, `no section wraps the "${heading}" heading`).not.toBeNull();
    return el as HTMLElement;
}

beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    currentProject = { ...BASE_PROJECT };
    mutateAsync.mockResolvedValue(currentProject);
    useProjectListMock.mockReturnValue({ data: [{ id: "p1" }, { id: "p2" }] });
    activeProjectId = "p1";
});

describe("settings page", () => {
    it("renders every configuration section", () => {
        renderPage();

        for (const heading of ["Project", "Jira", "Confluence", "GitHub", "Model"]) {
            expect(screen.getByRole("heading", { name: heading })).toBeInTheDocument();
        }
    });

    it("gives each section the same card treatment /knowledge uses", () => {
        renderPage();

        for (const heading of ["Project", "Jira", "Confluence", "GitHub", "Model"]) {
            const card = cardFor(heading);
            for (const cls of CARD_CLASSES) {
                expect(
                    card.className,
                    `"${heading}" card is missing ${cls}`
                ).toContain(cls);
            }
        }
    });

    it("leads each card with an icon, as the knowledge cards do", () => {
        renderPage();

        for (const heading of ["Project", "Jira", "Confluence", "GitHub", "Model"]) {
            const card = cardFor(heading);
            const icon = card.querySelector("svg");
            expect(icon, `"${heading}" card has no icon`).not.toBeNull();
            // Decorative: the heading beside it already names the section, so a
            // second announcement would just be noise for a screen reader.
            expect(icon).toHaveAttribute("aria-hidden", "true");
        }
    });

    it("pairs Jira and Confluence in one row, mirroring /knowledge", () => {
        renderPage();

        const jiraRow = cardFor("Jira").parentElement;
        expect(jiraRow?.className).toContain("lg:grid-cols-2");
        // The same row, not two rows that happen to be styled alike.
        expect(cardFor("Confluence").parentElement).toBe(jiraRow);
    });

    it("never renders a stored credential, only whether one is set", () => {
        // The server sends `has_*_token` and never the token, so there is
        // nothing to prefill — and a masked placeholder would misrepresent what
        // typing in the box does.
        renderPage();

        const jiraToken = screen.getByLabelText(/API token/i, {
            selector: "#jira-token",
        }) as HTMLInputElement;

        expect(jiraToken).toHaveAttribute("type", "password");
        expect(jiraToken.value).toBe("");
        expect(screen.getByText("Configured")).toBeInTheDocument();
        expect(screen.getAllByText("Not set").length).toBeGreaterThan(0);
    });
});

describe("saving a model default", () => {
    const save = () => screen.getByRole("button", { name: /save changes/i });
    const modelTrigger = () =>
        screen.getByRole("button", { name: /default model/i });

    /** Pick a model the way a person does: open the menu, click the row. */
    const pickModel = (name: string | RegExp) => {
        fireEvent.click(modelTrigger());
        fireEvent.click(screen.getByRole("menuitemradio", { name }));
    };

    it("sends the pair to the server, never half of it", async () => {
        renderPage();
        pickModel(/claude sonnet 5/i);
        fireEvent.click(save());

        await waitFor(() => expect(mutateAsync).toHaveBeenCalled());
        expect(mutateAsync.mock.calls[0][0]).toMatchObject({
            llm_provider: "claude",
            llm_model: "claude-sonnet-5",
        });
    });

    it("does not write the choice into this browser's storage", async () => {
        // The project row is now the only place a model preference lives. The
        // page used to also push the pick into localStorage so the removed nav
        // picker would follow it; a value written there now would be read by
        // nothing and could only ever go stale.
        renderPage();
        pickModel(/claude sonnet 5/i);
        fireEvent.click(save());

        await waitFor(() => expect(mutateAsync).toHaveBeenCalled());
        expect(localStorage.getItem(STORAGE_KEY)).toBeNull();
    });

    it("leaves the model alone when the save was about something else", async () => {
        // Editing a Jira URL must not send a model change with it.
        currentProject = {
            ...BASE_PROJECT,
            llm_provider: "openai",
            llm_model: "gpt-4o",
        };
        mutateAsync.mockResolvedValue(currentProject);
        renderPage();

        fireEvent.change(screen.getByLabelText(/base url/i, { selector: "#jira-url" }), {
            target: { value: "https://new.atlassian.net" },
        });
        fireEvent.click(save());

        await waitFor(() => expect(mutateAsync).toHaveBeenCalled());
        expect(mutateAsync.mock.calls[0][0]).toMatchObject({
            llm_provider: "openai",
            llm_model: "gpt-4o",
        });
    });
});

// ---------------------------------------------------------------------------
// Knowledge base — the embedding vendor and the index it writes to
// ---------------------------------------------------------------------------

describe("knowledge base settings", () => {
    const save = () => screen.getByRole("button", { name: /save changes/i });
    const providerTrigger = () =>
        screen.getByRole("button", { name: /embedding provider/i });

    it("sends the vendor and the index together", async () => {
        renderPage();

        fireEvent.click(providerTrigger());
        fireEvent.click(screen.getByRole("menuitemradio", { name: /voyage/i }));
        fireEvent.change(
            screen.getByLabelText(/pinecone index/i, { selector: "#pinecone-index" }),
            { target: { value: "qe-agent-voyage" } }
        );
        fireEvent.click(save());

        await waitFor(() => expect(mutateAsync).toHaveBeenCalled());
        expect(mutateAsync.mock.calls[0][0]).toMatchObject({
            embedding_provider: "voyage",
            pinecone_index_name: "qe-agent-voyage",
        });
    });

    it("clears the vendor back to the server default", async () => {
        currentProject = { ...BASE_PROJECT, embedding_provider: "voyage" };
        mutateAsync.mockResolvedValue(currentProject);
        renderPage();

        fireEvent.click(providerTrigger());
        fireEvent.click(
            screen.getByRole("menuitemradio", { name: /no preference/i })
        );
        fireEvent.click(save());

        await waitFor(() => expect(mutateAsync).toHaveBeenCalled());
        expect(mutateAsync.mock.calls[0][0]).toMatchObject({
            embedding_provider: "",
        });
    });

    it("warns that changing either field needs a new index", () => {
        // The dimension mismatch does fail loudly, but only on the NEXT ingest —
        // long after the person who changed the setting has moved on.
        renderPage();

        expect(screen.getByText(/needs a new index/i)).toBeInTheDocument();
        expect(screen.getByText(/1536 for OpenAI, 1024 for Voyage/i)).toBeInTheDocument();
    });

    it("uses the app's own dropdown for the vendor, not a native select", () => {
        renderPage();
        expect(document.querySelector("#embedding-provider")?.tagName).toBe("BUTTON");
    });
});

// ---------------------------------------------------------------------------
// The model dropdown is the SAME control as the one in the top bar
// ---------------------------------------------------------------------------

describe("project model picker", () => {
    const trigger = () => screen.getByRole("button", { name: /default model/i });

    it("is the app's own dropdown, not a native select", () => {
        // A native <select> drops an OS-drawn list that ignores the app's radius,
        // grouping and check marks — which is what made this look foreign beside
        // the picker in the top bar.
        renderPage();

        expect(document.querySelector("#project-model")?.tagName).toBe("BUTTON");
        expect(document.querySelector("select")).toBeNull();
    });

    it("keeps the visible label pointing at the control", () => {
        // Field renders <label htmlFor>, so the trigger has to carry that id or
        // clicking the label does nothing.
        renderPage();

        const label = document.querySelector('label[for="project-model"]');
        expect(label).not.toBeNull();
        expect(document.getElementById("project-model")).not.toBeNull();
    });

    it("shows No preference when the project has no model set", () => {
        renderPage();
        expect(trigger()).toHaveTextContent("No preference");
    });

    it("offers no-preference and the model catalog, grouped", () => {
        renderPage();
        fireEvent.click(trigger());

        expect(screen.getByRole("menu", { name: /default model/i })).toBeInTheDocument();
        expect(
            screen.getByRole("menuitemradio", { name: /no preference/i })
        ).toBeInTheDocument();
        // "GPT-4o" alone also matches "GPT-4o mini", and each row's accessible
        // name carries its note, so assert on the group headings plus a model
        // whose name is unambiguous.
        expect(screen.getByText("Project default")).toBeInTheDocument();
        expect(screen.getByText("OpenAI")).toBeInTheDocument();
        expect(
            screen.getByRole("menuitemradio", { name: /claude sonnet 5/i })
        ).toBeInTheDocument();
    });

    it("selects a model, and reflects it on the trigger", () => {
        renderPage();
        fireEvent.click(trigger());
        fireEvent.click(
            screen.getByRole("menuitemradio", { name: /claude sonnet 5/i })
        );

        expect(trigger()).toHaveTextContent("Claude Sonnet 5");
    });

    it("clears both halves when No preference is chosen", () => {
        // provider and model are only meaningful as a pair, so "no preference"
        // has to empty both rather than leave a half-set project.
        renderPage();
        fireEvent.click(trigger());
        fireEvent.click(screen.getByRole("menuitemradio", { name: /claude sonnet 5/i }));
        expect(trigger()).toHaveTextContent("Claude Sonnet 5");

        fireEvent.click(trigger());
        fireEvent.click(screen.getByRole("menuitemradio", { name: /no preference/i }));

        expect(trigger()).toHaveTextContent("No preference");
    });
});

describe("deleting the project", () => {
    const deleteButton = () =>
        screen.getByRole("button", { name: /delete this project/i });

    it("asks for confirmation before deleting", () => {
        renderPage();
        fireEvent.click(deleteButton());

        const dialog = screen.getByRole("dialog");
        expect(within(dialog).getByText(/delete project/i)).toBeInTheDocument();
        expect(within(dialog).getByText(currentProject.name)).toBeInTheDocument();
        expect(deleteProjectMutate).not.toHaveBeenCalled();
    });

    it("deletes the project and sends the user home on confirm", () => {
        renderPage();
        fireEvent.click(deleteButton());

        const dialog = screen.getByRole("dialog");
        fireEvent.click(within(dialog).getByRole("button", { name: /^delete$/i }));

        expect(deleteProjectMutate).toHaveBeenCalledWith(
            currentProject.id,
            expect.anything()
        );
        // Nothing left on this page to configure once its project is gone —
        // home is where every other exit from the app already lands.
        expect(push).toHaveBeenCalledWith("/");
    });

    it("stays enabled when this is the caller's only project", () => {
        useProjectListMock.mockReturnValue({ data: [{ id: "p1" }] });
        renderPage();

        expect(deleteButton()).toBeEnabled();
        expect(screen.queryByText(/only project/i)).not.toBeInTheDocument();
    });
});

describe("with no project selected", () => {
    // Where deleting the last project lands you. `useProject(null)` is a
    // disabled query, so `isLoading` never turns false: the page answered with
    // "Loading project" forever, at exactly the person who had nothing to load.
    beforeEach(() => {
        activeProjectId = null;
    });

    it("goes to settings after creating, like the switcher does", () => {
        useProjectListMock.mockReturnValue({ data: [] });
        createProjectMutate.mockImplementation(
            (_name: string, options?: { onSuccess?: (p: unknown) => void }) =>
                options?.onSuccess?.({ id: "new", name: "Checkout" })
        );
        renderPage();

        fireEvent.change(screen.getByLabelText(/project name/i), {
            target: { value: "Checkout" },
        });
        fireEvent.click(screen.getByRole("button", { name: /create project/i }));

        expect(push).toHaveBeenCalledWith("/settings");
    });

    it("offers the surviving projects, not only a fresh one", () => {
        // Deleting one of several lands here too. Telling that user to create
        // a fourth project answers a question they did not ask.
        useProjectListMock.mockReturnValue({
            data: [
                { id: "p2", name: "Checkout" },
                { id: "p3", name: "Billing" },
            ],
        });
        renderPage();

        fireEvent.click(screen.getByRole("button", { name: /checkout/i }));

        expect(setActiveProject).toHaveBeenCalledWith("p2");
    });

    it("offers only creation when nothing survived", () => {
        useProjectListMock.mockReturnValue({ data: [] });
        renderPage();

        expect(screen.queryByText(/your projects/i)).not.toBeInTheDocument();
        expect(
            screen.getByRole("button", { name: /create project/i })
        ).toBeInTheDocument();
    });

    it("does not ask for a project it cannot show", () => {
        renderPage();

        expect(screen.queryByLabelText(/^name$/i)).not.toBeInTheDocument();
    });

    it("offers the create form, not directions to the switcher", () => {
        // It named the fix and then sent the user to find it — the only control
        // that resolves this state was a row hidden inside a nav dropdown.
        renderPage();

        expect(
            screen.getByRole("heading", { name: /no project selected/i })
        ).toBeInTheDocument();
        expect(
            screen.getByRole("button", { name: /create project/i })
        ).toBeInTheDocument();
        expect(screen.queryByText(/from the switcher/i)).not.toBeInTheDocument();
    });

    it("creates the project the name field was given", () => {
        renderPage();

        fireEvent.change(screen.getByLabelText(/project name/i), {
            target: { value: "Checkout revamp" },
        });
        fireEvent.click(screen.getByRole("button", { name: /create project/i }));

        expect(createProjectMutate).toHaveBeenCalledWith(
            "Checkout revamp",
            expect.anything()
        );
    });

    it("will not create a project with a blank name", () => {
        renderPage();

        expect(screen.getByRole("button", { name: /create project/i })).toBeDisabled();
        fireEvent.change(screen.getByLabelText(/project name/i), {
            target: { value: "   " },
        });
        expect(screen.getByRole("button", { name: /create project/i })).toBeDisabled();
        expect(createProjectMutate).not.toHaveBeenCalled();
    });

    it("hides the danger zone — there is nothing to delete", () => {
        renderPage();

        expect(
            screen.queryByRole("button", { name: /delete this project/i })
        ).not.toBeInTheDocument();
    });
});
