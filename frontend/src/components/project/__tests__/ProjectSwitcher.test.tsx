/**
 * ProjectSwitcher — the trigger's width must not follow the project name.
 *
 * It sits between the wordmark and the nav links, so a content-sized trigger
 * moves every link to its right: once when the name arrives (it renders
 * "Loading…" first, which is narrower than most names) and again on every
 * switch. jsdom does not lay out, so these assert the mechanism that keeps the
 * box stable rather than a measured pixel width.
 */
import React from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";

import { cn } from "@/lib/utils";

const push = vi.fn();
vi.mock("next/navigation", () => ({
    useRouter: () => ({ push, replace: vi.fn() }),
    usePathname: () => "/",
}));

const createProject = vi.fn().mockResolvedValue({ id: "new", name: "New" });
const projects = vi.fn();
vi.mock("@/lib/hooks/useProjects", () => ({
    useProjectList: () => projects(),
    useSyncActiveProject: () => ({}),
    useCreateProject: () => ({ mutateAsync: createProject, isPending: false }),
}));

vi.mock("@/lib/stores/projectStore", () => ({
    useProjectStore: (selector: (s: Record<string, unknown>) => unknown) =>
        selector({ activeProjectId: "p1", setActiveProject: vi.fn() }),
}));

import ProjectSwitcher from "@/components/project/ProjectSwitcher";

const SHORT = [{ id: "p1", name: "P1", created_at: "2026-01-01T00:00:00Z" }];
const LONG = [
    {
        id: "p1",
        name: "Acme Corporation Platform Modernisation Programme",
        created_at: "2026-01-01T00:00:00Z",
    },
];

/** The element carrying the width — the wrapper around the trigger. */
function wrapper(): HTMLElement {
    return screen.getByRole("button", { name: /project/i })
        .parentElement as HTMLElement;
}

beforeEach(() => {
    vi.clearAllMocks();
    projects.mockReturnValue({ data: SHORT, isLoading: false });
});

describe("ProjectSwitcher trigger width", () => {
    it("is the same regardless of how long the project name is", () => {
        const short = render(<ProjectSwitcher />);
        const shortClasses = wrapper().className;
        short.unmount();

        projects.mockReturnValue({ data: LONG, isLoading: false });
        render(<ProjectSwitcher />);

        expect(wrapper().className).toBe(shortClasses);
    });

    it("carries a fixed width, not a max-width", () => {
        // max-w leaves the box content-sized up to a cap, which is exactly the
        // shifting behaviour being prevented.
        render(<ProjectSwitcher />);

        expect(wrapper().className).toContain("w-48");
        expect(wrapper().className).not.toMatch(/\bmax-w-/);
    });

    it("keeps that width while the project list is still loading", () => {
        // The first paint says "Loading…" and the second a real name. If the box
        // sized to its text, that alone would shift the nav on every page load.
        projects.mockReturnValue({ data: undefined, isLoading: true });
        const loading = render(<ProjectSwitcher />);
        const loadingClasses = wrapper().className;
        expect(screen.getByRole("button", { name: /project/i })).toHaveTextContent(
            "Loading…"
        );
        loading.unmount();

        projects.mockReturnValue({ data: LONG, isLoading: false });
        render(<ProjectSwitcher />);

        expect(wrapper().className).toBe(loadingClasses);
    });

    it("lets the label truncate instead of overflowing the box", () => {
        // `truncate` alone does nothing to a flex item: min-width defaults to
        // auto, so it refuses to shrink below its text and overflows instead.
        render(<ProjectSwitcher />);

        const label = screen.getByText(SHORT[0].name);
        expect(label.className).toContain("truncate");
        expect(label.className).toContain("min-w-0");
    });

    it("can be widened by a caller, for the mobile drawer", () => {
        render(<ProjectSwitcher className="w-full" />);

        // twMerge resolves the conflict in the caller's favour — both classes
        // surviving would leave the fixed width to win by source order.
        expect(wrapper().className).toContain("w-full");
        expect(wrapper().className).not.toContain("w-48");
    });
});

describe("cn width overriding", () => {
    it("resolves conflicting width utilities to the last one", () => {
        // The property the drawer override depends on.
        expect(cn("relative w-48", "w-full")).toBe("relative w-full");
        expect(cn("relative w-52", "w-full")).toBe("relative w-full");
    });
});

describe("popover width", () => {
    it("is never narrower than the trigger it belongs to", () => {
        // A fixed panel width leaves the two edges misaligned wherever the
        // trigger is wider — which is what a `w-full` trigger on the settings
        // page produced. min-w-full resolves against the relative wrapper, i.e.
        // the trigger.
        render(<ProjectSwitcher />);
        fireEvent.click(screen.getByRole("button", { name: /project/i }));

        const panel = screen.getByRole("menu", { name: "Project" });
        expect(panel.className).toContain("min-w-full");
    });

    it("keeps a floor for the narrow nav trigger, capped to the viewport", () => {
        // The floor stops the nav menu collapsing to the 12rem trigger; the cap
        // stops that floor overflowing the mobile drawer, itself only
        // min(19rem,85vw) wide.
        render(<ProjectSwitcher />);
        fireEvent.click(screen.getByRole("button", { name: /project/i }));

        const panel = screen.getByRole("menu", { name: "Project" });
        expect(panel.className).toContain("w-[17rem]");
        expect(panel.className).toContain("max-w-[calc(100vw-1.5rem)]");
    });
});

describe("creating a project from the switcher", () => {
    it("sends the user to settings to configure it", async () => {
        // A new project has no Jira URL, no repository and no credentials, so
        // every other page has nothing to show for it. Landing back where you
        // were leaves an empty project selected and no explanation for it.
        projects.mockReturnValue({ data: SHORT, isLoading: false });
        render(<ProjectSwitcher />);

        fireEvent.click(screen.getByRole("button", { name: /project:/i }));
        fireEvent.click(screen.getByRole("button", { name: /new project/i }));
        fireEvent.change(screen.getByLabelText(/new project name/i), {
            target: { value: "Checkout revamp" },
        });
        fireEvent.click(screen.getByRole("button", { name: /^create$/i }));

        await waitFor(() =>
            expect(createProject).toHaveBeenCalledWith("Checkout revamp")
        );
        await waitFor(() => expect(push).toHaveBeenCalledWith("/settings"));
    });

    it("does not navigate when the name is blank", async () => {
        projects.mockReturnValue({ data: SHORT, isLoading: false });
        render(<ProjectSwitcher />);

        fireEvent.click(screen.getByRole("button", { name: /project:/i }));
        fireEvent.click(screen.getByRole("button", { name: /new project/i }));
        fireEvent.click(screen.getByRole("button", { name: /^create$/i }));

        expect(createProject).not.toHaveBeenCalled();
        expect(push).not.toHaveBeenCalled();
    });
});
