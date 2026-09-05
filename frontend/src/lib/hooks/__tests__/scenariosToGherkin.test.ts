/**
 * scenariosToGherkin — the app's canonical JSON→Gherkin rendering.
 *
 * Its output is not just displayed: it is saved to `bdd_files`, downloaded as a
 * `.feature` file, and parsed by the training pipeline. It therefore has to be
 * real Gherkin, not something that merely looks like it.
 */
import { describe, it, expect, vi } from "vitest";

// The module pulls in the API client (and through it Supabase) purely to export
// the mutation hook; this test only exercises the pure renderer beside it.
vi.mock("@/lib/api/client", () => ({ default: { post: vi.fn() } }));

import { scenariosToGherkin } from "@/lib/hooks/useBDDGenerate";

const scenario = (over: Partial<Parameters<typeof scenariosToGherkin>[0][0]> = {}) => ({
    source_ac_clause: "Users must verify their email.",
    feature: "Registration",
    scenario: "New user registers",
    given: "a visitor is on the registration page",
    when: "they submit valid details",
    then: "a verification email is sent",
    ...over,
});

describe("scenariosToGherkin", () => {
    it("adds the step keywords the model leaves out", () => {
        // Without these the output is unrunnable in Cucumber and the training
        // parser discards the scenario as having incomplete steps.
        const out = scenariosToGherkin([scenario()]);

        expect(out).toContain("    Given a visitor is on the registration page");
        expect(out).toContain("    When they submit valid details");
        expect(out).toContain("    Then a verification email is sent");
    });

    it("does not double up a keyword the model already supplied", () => {
        const out = scenariosToGherkin([
            scenario({ given: "Given a visitor is on the page" }),
        ]);

        expect(out).toContain("    Given a visitor is on the page");
        expect(out).not.toContain("Given Given");
    });

    it("keeps And/But continuations as part of the same clause", () => {
        const out = scenariosToGherkin([
            scenario({ given: "a visitor exists\nAnd they are signed out" }),
        ]);

        expect(out).toContain("    Given a visitor exists");
        expect(out).toContain("    And they are signed out");
    });

    it("turns an unkeyworded second line into an And", () => {
        const out = scenariosToGherkin([
            scenario({ then: "an email is sent\nthe user sees a notice" }),
        ]);

        expect(out).toContain("    Then an email is sent");
        expect(out).toContain("    And the user sees a notice");
    });

    it("still carries the AC clause and the feature header", () => {
        // The clause comment is how attribution survives a round trip through
        // the editor — the training pipeline reads it back.
        const out = scenariosToGherkin([scenario()]);

        expect(out).toContain("Feature: Registration");
        expect(out).toContain("  # Source AC: Users must verify their email.");
        expect(out).toContain("  Scenario: New user registers");
    });

    it("omits a step that has no text rather than emitting a bare keyword", () => {
        const out = scenariosToGherkin([scenario({ when: "   " })]);
        expect(out).not.toMatch(/^\s*When\s*$/m);
    });
});
