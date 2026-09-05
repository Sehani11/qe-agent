/**
 * RAGContextPanel.test.tsx
 *
 * Unit tests for Story 4.4 — RAG Context Display Panel
 * Covers AC1 (expandable snippets), AC2 (clickable sources + title),
 * AC3 (empty state message).
 */
import React from "react";
import { render, screen, fireEvent } from '@/test/test-utils';
import { describe, it, expect } from "vitest";
import RAGContextPanel from "@/components/pipeline/RAGContextPanel";
import type { RagContextItem } from "@/lib/types/verification";

function makeItem(overrides: Partial<RagContextItem> = {}): RagContextItem {
    return {
        source: "confluence",
        source_id: "12345",
        // >100 chars so the expand/collapse affordance renders (see isTruncatable).
        snippet:
            "Architecture decision: the auth service uses JWT with RS256 signing keys, rotated every 90 days via the platform key-management service and validated at the gateway.",
        title: "Auth Design",
        url: "https://wiki.example.com/pages/12345",
        ...overrides,
    };
}

describe("RAGContextPanel", () => {
    // ---- AC3: empty state ------------------------------------------------

    it("renders 'No additional project context available' when items is empty (AC3)", () => {
        render(<RAGContextPanel items={[]} />);
        expect(
            screen.getByText(/no additional project context available/i)
        ).toBeInTheDocument();
    });

    it("renders the empty state (not null) so the section is always visible (AC3)", () => {
        const { container } = render(<RAGContextPanel items={[]} />);
        expect(container.firstChild).not.toBeNull();
    });

    // ---- AC2: clickable Confluence source --------------------------------

    it("renders a Confluence item as a link opening in a new tab (AC2)", () => {
        render(<RAGContextPanel items={[makeItem()]} />);

        const link = screen.getByRole("link", { name: /auth design/i });
        expect(link).toHaveAttribute("href", "https://wiki.example.com/pages/12345");
        expect(link).toHaveAttribute("target", "_blank");
        expect(link).toHaveAttribute("rel", "noopener noreferrer");
    });

    // ---- AC2: Jira shows ticket ID + title -------------------------------

    it("renders a Jira item with ticket ID and title (AC2)", () => {
        const jira = makeItem({
            source: "jira",
            source_id: "PROJ-42",
            title: "User can reset password",
            url: "https://jira.example.com/browse/PROJ-42",
            snippet: "AC: Given a valid email, When reset requested, Then email sent.",
        });
        render(<RAGContextPanel items={[jira]} />);

        const link = screen.getByRole("link", { name: /PROJ-42 — User can reset password/i });
        expect(link).toHaveAttribute("href", "https://jira.example.com/browse/PROJ-42");
    });

    // ---- AC2: no url => plain text, not a dead link ----------------------

    it("renders source as plain text (no anchor) when url is absent (AC2)", () => {
        const noUrl = makeItem({ url: "", title: "Legacy Page" });
        render(<RAGContextPanel items={[noUrl]} />);

        expect(screen.queryByRole("link")).not.toBeInTheDocument();
        expect(screen.getByText("Legacy Page")).toBeInTheDocument();
    });

    it("falls back to source_id when title is absent", () => {
        const noTitle = makeItem({ source: "confluence", title: "", url: "" });
        render(<RAGContextPanel items={[noTitle]} />);
        expect(screen.getByText("12345")).toBeInTheDocument();
    });

    // ---- AC1: expandable snippet -----------------------------------------

    it("toggles snippet expansion when 'Show more' is clicked (AC1)", () => {
        render(<RAGContextPanel items={[makeItem()]} />);

        const snippet = screen.getByText(/JWT with RS256/i);
        // Collapsed by default (line-clamp-2 applied)
        expect(snippet.className).toMatch(/line-clamp-2/);

        fireEvent.click(screen.getByRole("button", { name: /show more/i }));
        expect(snippet.className).not.toMatch(/line-clamp-2/);

        fireEvent.click(screen.getByRole("button", { name: /show less/i }));
        expect(snippet.className).toMatch(/line-clamp-2/);
    });

    // ---- Multiple items --------------------------------------------------

    it("renders one entry per item with the source badge", () => {
        render(
            <RAGContextPanel
                items={[
                    makeItem({ source_id: "1", title: "A", url: "https://x/1" }),
                    makeItem({ source: "jira", source_id: "PROJ-2", title: "B", url: "https://x/2" }),
                ]}
            />
        );
        expect(screen.getAllByRole("link")).toHaveLength(2);
    });
});
