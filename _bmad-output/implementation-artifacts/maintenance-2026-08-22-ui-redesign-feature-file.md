# Maintenance Record — Frontend Redesign ("feature file" direction)

**Date:** 2026-08-22
**Kind:** Cross-cutting frontend redesign (visual system + feedback primitives), performed outside the story workflow
**Status:** ✅ Implemented. `tsc --noEmit` clean, ESLint clean, vitest **153 passed / 2 skipped / 0 failed**
**Amends:** Stories 1.5, 2.4, 3.1, 4.5, 6.7 (each carries a pointer to this record)
**Also amends:** `_bmad-output/planning-artifacts/ux-design-specification.md` — its Design System, Visual Design Foundation, Design Direction, Component Strategy and UX Consistency sections described the superseded sky/blue system and have been rewritten.

This record exists so future agents reading the story artifacts do not style new
work against a palette, typeface or feedback pattern the codebase no longer has.
Every Tailwind colour utility quoted in a story artifact predating this date is
stale. `frontend/src/app/globals.css` is the source of truth for the token set.

---

## 1. Why

The UI was generic Tailwind default — sky/blue/indigo gradients, Poppins, soft
shadows, pill radii — and carried three concrete problems beyond taste:

- **Dead dark mode.** `globals.css` defined a complete `.dark` token set, but
  every page hard-coded light `slate-*` / `sky-*` utilities, so those tokens were
  unreachable. There was no theme control anywhere in the app.
- **No transient feedback.** Success and failure were reported only by inline
  banners inside whichever panel triggered them. An action whose result landed
  off-screen (verification finishing, a save made while scrolled into the editor)
  reported nothing the user would see.
- **Ad-hoc loaders.** Every waiting state hand-rolled its own spinner markup;
  several had no accessible name, and no list had a skeleton.

## 2. Design direction

**"Feature file"** — the UI borrows the vocabulary of the artifact the product
produces. Chosen over a dark engineering-console direction and a conventional
light SaaS direction. Recorded here because the choice constrains everything
below.

- **Signature: a keyword gutter.** Panels, pipeline stages, verdict rows and
  toasts all carry the same hairline left rule, tinted by signal.
  `.gutter-rule` + `data-signal="pass|fail|pending"` in `globals.css`.
- **Colour is the verdict, not a brand hue.** Pass-green is the primary accent —
  the colour that means "verified" is the colour of the primary button — with
  fail-red, pending-amber, and a keyword-violet reserved *only* for Gherkin
  keywords and knowledge-base context.
- **Structure over elevation.** Hairline rules replace drop shadows; radii drop
  from `rounded-3xl` (24px) to `--radius: 0.375rem` (6px).
- **Monospace headings.** The type that names things matches the type that shows
  them.

## 3. Token system

`frontend/src/app/globals.css` was rewritten. The shadcn/base-ui contract
(`--color-primary`, `--color-border`, …) is preserved and remapped onto the new
palette, so existing primitives keep working; semantic tokens sit alongside it:

| Group | Tokens |
|---|---|
| Surfaces | `--paper` `--surface` `--surface-raised` `--rule` `--rule-strong` |
| Signals | `--pass` / `-soft` / `-ink`, `--fail` / `-soft` / `-ink`, `--pending` / `-soft` / `-ink` |
| Syntax | `--keyword` `--keyword-soft` |

All values are `oklch()`. Both palettes are complete — light on `:root`, dark
overriding on `.dark`. **Do not hard-code a `slate-*` / `sky-*` / `emerald-*` /
`rose-*` utility again**; use a semantic token or one of the component classes
`.gutter-rule` / `.eyebrow` / `.kw` / `.panel`.

Typography: **JetBrains Mono** (headings, keywords, data, machine output) plus
**Instrument Sans** (prose), replacing Poppins + Geist Mono. Base size 15px
(was 17px) — the redesign is denser.

Reduced motion is honoured globally by a `prefers-reduced-motion` block that
collapses every animation, plus a JS check inside `RunSpinner`.

## 4. New primitives

| File | Provides |
|---|---|
| `components/ui/toast.tsx` | `ToastProvider`, `useToast()`, portalled `Toaster` |
| `components/ui/loaders.tsx` | `Spinner` `RunSpinner` `InlineLoader` `Skeleton` `SkeletonText` `SkeletonRows` `ProgressBar` `LoadingOverlay` |
| `components/ui/panel.tsx` | `Panel` `PanelHeader` `PanelBody` |
| `components/ui/field.tsx` | `Input` `Textarea` `Label` `Field` |
| `components/ui/badge.tsx` | `Badge`, `VerdictBadge` |
| `components/ui/empty-state.tsx` | `EmptyState`, `ErrorState` |
| `components/layout/AppNav.tsx` | shared top bar + `Wordmark` |
| `components/layout/PageHeader.tsx` | title block for secondary pages |
| `components/layout/ThemeToggle.tsx` | light / dark / system segmented control |
| `providers/ThemeProvider.tsx` | `ThemeProvider`, `useTheme()`, `themeInitScript` |
| `test/test-utils.tsx` | RTL `render` pre-wrapped in the app's providers |

`RunSpinner` cycles the braille frames a test runner uses, and is the loader for
work the agent does on the user's behalf; `Spinner` (a plain ring) is for a
control that is busy. Skeletons stand in for content whose shape is known; a
spinner is for work whose duration is not.

### Toasts

Built in-house rather than adding `sonner` — keeps the signal vocabulary and adds
no dependency. Five variants (`success` `error` `warning` `info` `loading`),
per-variant default durations (errors 8s, `loading` never auto-dismisses), a
four-toast cap, pause-on-hover/focus, `role="alert"` for errors and
`role="status"` otherwise, and `toast.promise()` for swap-in-place flows.

**Portalled into `document.body`.** Required, not cosmetic: the region is
`position: fixed`, and the nav and ingest bar use `backdrop-blur`, which makes
them a containing block for fixed descendants. Portalling also keeps the toast
region out of the subtree a caller renders — otherwise a test asserting
`container.firstChild` sees the toast region. `ConfirmModal` is portalled for the
same containing-block reason.

**Copy rule:** an action keeps its name through the whole flow — "Generate BDD"
resolves to "BDD generated", never "Success!". Errors say what broke and what to
do next, and do not apologise. A toast title must not repeat an inline note's
text in the same panel, or `getByText` matches both.

### Where toasts fire

| Action | Outcome |
|---|---|
| Ticket ingest | success / error (`session/[sessionId]/page.tsx`) |
| BDD generate | success with scenario count / error |
| Verification complete | success when all pass, **warning** when any fail — a run with gaps is the answer the user asked for, not an error (`lib/hooks/useRunVerification.ts`) |
| BDD save, upload, `.feature` / CSV export | success / error (`BDDEditorPanel`) |
| Confluence / Jira / document ingest, source delete | success / warning / error (`KnowledgeBasePanel`) |
| Training upload, dataset delete | success / warning / error (`TrainingDataPanel`) |
| Comparison run, batch run, run delete | success / warning / error (`ModelComparisonPanel`, `SavedRunsPanel`) |

Inline banners were **kept alongside** toasts wherever the message belongs to a
form or must survive a reload — auth errors on `/login`, `globalError` in the
workspace, per-file rejection reasons, `uploadError` in the editor. Toasts are
additive; they did not replace inline validation.

## 4a. Layout shell and navigation

**One container, one gutter.** `.app-shell` in `globals.css`
(`w-full px-6 sm:px-10 lg:px-20 xl:px-32 2xl:px-40`) is used by `AppNav`, the session
ingest bar, the login header, and every page's `<main>`. Before this, page
shells were `max-w-5xl` / `max-w-6xl` / `max-w-7xl` with `px-4 lg:px-6`, so the
nav's wordmark and the page's first column sat on different left edges on any
wide screen.

The gutter is the only constant, and it steps 24 → 40 → 80 → 128 → 160px with
the viewport. Mobile stays at 24px — a phone has no width to spare, so the
growth is all at `sm` and above. It has been widened several times on
request; treat the current values as the intent, not a default.

**Full-bleed, by decision.** The shell has **no max-width**. An intermediate
version capped it at a centred `max-w-7xl`; that was rejected because it left a
dead margin down both sides of a wide monitor while the editor, the results
table and the two-column comparison are exactly the surfaces that want the
room. This is a workbench, not an article. The gutter is the only constant, and
it steps 20 → 24 → 32 → 40px with the viewport so content never touches the
screen edge.

**Measure belongs to the paragraph, not the shell.** Prose keeps a readable
line length via `max-w-*` on the `<p>` itself (`PageHeader` description,
landing hero copy). Do not reintroduce a width cap on a page's layout container
to achieve it — that is what made the edges disagree in the first place. The
centred `max-w-sm` auth card on `/login` is the one deliberate exception.

**Nav active state is a rule, not a pill.** The active link draws a 2px
pass-green indicator at `-bottom-px`, so it lands on the header's own bottom
border and the two read as one continuous line. The previous filled grey chip
was the one place the UI reverted to a generic component instead of the
hairline-rule language. Hovering an inactive link previews the indicator in
`--rule-strong`.

**Mobile is a hamburger drawer.** Below `md` the links, `ThemeToggle` and
`LogoutButton` move into a right-hand sheet; the bar keeps only the wordmark and
whatever the page passed as `actions`. An earlier version put the links in a
second scrollable row under the bar, which cost 40px of vertical space on every
screen and still could not hold the account controls.

The drawer is a real modal: backdrop, body scroll lock, Escape to close, focus
moved into the panel and returned to the trigger, and Tab trapped inside. It is
**portalled to `<body>`** for the same reason the toasts are — the header sets
`backdrop-blur`, which makes it a containing block for fixed descendants, so a
panel mounted inside it would be clipped to the 56px bar instead of covering the
viewport. Active rows reuse the `.gutter-rule` signal device.

Two React details worth keeping: the menu closes on `pathname` change via the
*adjust-state-during-render* pattern rather than an effect, and the exit
animation runs off a `phase` state whose only effect-driven write happens in a
`setTimeout` callback. Both exist because ESLint's `react-hooks/set-state-in-effect`
is enforced here — synchronous `setState` in an effect body is a build failure,
not a style note. `useSyncExternalStore` supplies the hydration flag, mirroring
`confirm-modal.tsx`.

The header also takes a firmer border once the page scrolls (`useScrolled`,
passive listener, no layout reads) so it reads as a layer above the content.
`h-14` is load-bearing — the session page's ingest bar is `sticky top-14`.

## 5. Dark mode

`ThemeProvider` writes `.dark` on `<html>` and persists the choice under
`localStorage["qe-agent-theme"]` (`light | dark | system`; default `system`,
following OS changes live while unpinned). `themeInitScript` is inlined in
`<head>` in `app/layout.tsx` so the stored theme paints before first paint —
without it a dark-mode machine flashes the light palette. Storage access is
wrapped in try/catch for private-mode browsers.

Monaco follows the app theme: `registerGherkinLanguage` now defines **both**
`gherkin-light` and `gherkin-dark`, and `BDDEditorPanel` selects by
`useTheme().resolved`. Editor colours mirror the app palette — structural
keywords in keyword-violet, `Given/When/Then` in pass-green.

## 6. Accessibility

- Skip-to-content link in the root layout; `<main id="main">` on every page.
- `VerdictBadge` carries a glyph as well as a colour, so pass/fail survives a
  colour-vision difference or greyscale printing. This makes the UX spec's
  existing colour-independence rule a shared component rather than a convention.
- Focus ring is a global `:focus-visible` outline in the pass-green.
- Skeletons expose `role="status"` + `aria-label="Loading"`; `ProgressBar`
  exposes `role="progressbar"` with value/min/max, omitting them when
  indeterminate.
- `Button` gained `loading`, which sets `aria-busy` and disables the control
  while keeping its label readable rather than swapping it for "Loading…".
- `SubmitButton` (new) gives server-action forms a pending state via
  `useFormStatus`, so `/login` shows a loader without lifting the form into
  client state.

## 7. Test impact

Components now call `useToast()` / `useTheme()`, which throw without a provider.
Rather than wrapping ~80 `render()` call sites, `src/test/test-utils.tsx`
re-exports RTL with a `render` that wraps in `ThemeProvider` + `ToastProvider`;
the nine affected test files changed their import line only.

Four assertions were updated because they pinned superseded implementation
detail rather than behaviour:

- `VerificationResultsPanel.test.tsx` ×2 — asserted `className` matched
  `/emerald/` and `/rose/`; now `/pass/` and `/fail/`.
- `TrainingDataPanel.test.tsx` — asserted the literal text `"Loading datasets…"`;
  the list now renders `SkeletonRows`, so it asserts
  `getByRole("status", { name: /loading/i })`.
- `BDDEditorPanel.test.tsx` — selected the mobile textarea by placeholder copy;
  now selects by accessible name, which copy changes cannot break.

During this pass the suite carried two failures that were **already failing on
the parent commit** — `auth.test.ts > signUp > calls supabase.auth.signUp ...`
and `BDDEditorPanel.test.tsx > renders the editor panel and control buttons`.
Both have since been fixed by concurrent work on the same day; the suite is
green at **153 passed / 2 skipped**. Recorded here only so the redesign is not
later blamed for them.

One trap worth recording: `next/dynamic`'s `loading:` fallback for Monaco must
stay **hook-free**. A stateful fallback (the first attempt used `InlineLoader`,
which holds state and a `matchMedia` effect) delays the lazy-chunk swap by an
extra render — visible as a flash of the placeholder in the app, and as a missing
`monaco-editor-mock` in tests.

## 8. Files

**Rewritten:** `app/globals.css`, `app/layout.tsx`, `app/page.tsx`,
`app/login/page.tsx`, `app/sessions/page.tsx`, `app/session/[sessionId]/page.tsx`,
`app/knowledge/page.tsx`, `app/training/page.tsx`, `app/comparison/page.tsx`,
`components/ui/button.tsx`, `components/ui/confirm-modal.tsx`,
`components/layout/LogoutButton.tsx`, `components/auth/OAuthButton.tsx`,
`components/pipeline/{TerminalProgressLog,BDDEditorPanel,GitHubSourceSelector,VerificationResultsPanel,VerificationResultRow,RAGContextPanel,ChatPanel}.tsx`,
`components/knowledge/{KnowledgeBasePanel,KnowledgeChatPanel}.tsx`,
`components/training/TrainingDataPanel.tsx`,
`components/evaluation/{ModelComparisonPanel,SavedRunsPanel}.tsx`,
`lib/hooks/useRunVerification.ts`.

**Added:** the eleven files in §4, plus `components/auth/SubmitButton.tsx`.

**Later same day:** `.app-shell` added to `globals.css` and adopted by `AppNav`,
all eight pages and the login header, then changed from a centred `max-w-7xl`
to full-bleed; `AppNav` active state reworked to the underline indicator (§4a).

**Behaviour deliberately preserved:** every hook signature, the session
stale-context reset logic, the Monaco model-URI-per-session guard, the
`hasUserEdited` dirty-tracking rule from Story 6.4, the CSV fallback parser, and
all `role` / `aria` / `id` / `data-testid` contracts the tests rely on.

## 9. TerminalProgressLog is now literal

Story 1.5 deliberately made this component a friendly status card "rather than a
terminal log". That is reversed. It now renders the three pipeline stages
(`Given` ingest → `When` generate → `Then` verify) with per-stage state glyphs,
plus **the live SSE log tail** from `SessionContext.logs`, which the component
had been ignoring entirely despite its name. The stage list is derived state
only — no new context fields were added.
