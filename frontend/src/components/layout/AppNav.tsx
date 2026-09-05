"use client";

import {
    useCallback,
    useEffect,
    useId,
    useRef,
    useState,
    useSyncExternalStore,
} from "react";
import { createPortal } from "react-dom";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Menu, X } from "lucide-react";

import LogoutButton from "@/components/layout/LogoutButton";
import ProjectSwitcher from "@/components/project/ProjectSwitcher";
import ThemeToggle from "@/components/layout/ThemeToggle";
import { cn } from "@/lib/utils";

/* Hydration check without a mount effect (mirrors `confirm-modal.tsx`): the
   server snapshot is false, the client snapshot true, so `createPortal` is
   only ever called where there is a document to portal into. */
const neverChanges = () => () => {};
const onClient = () => true;
const onServer = () => false;

const LINKS = [
    { href: "/sessions", label: "Sessions" },
    { href: "/knowledge", label: "Knowledge" },
    // Training data and comparison were separate entries, which split one task
    // across two places: you cannot tell whether fine-tuning helped without
    // comparing against the general model.
    { href: "/fine-tune", label: "Fine tune" },
];

/** The mark: a verdict tick, because a verdict is what this product produces. */
export function Wordmark({ className }: { className?: string }) {
    return (
        <Link
            href="/"
            className={cn(
                "group inline-flex items-center gap-2 font-mono text-sm font-semibold tracking-tight text-foreground",
                className
            )}
        >
            <span
                className={cn(
                    "grid h-6 w-6 place-items-center rounded-[4px] bg-pass text-[12px] leading-none text-white",
                    // A tick that settles as the page loads, then lifts on hover.
                    "shadow-sm shadow-pass/25 transition-transform duration-200 group-hover:-translate-y-px",
                    "dark:text-[color:var(--paper)]"
                )}
                aria-hidden="true"
            >
                ✓
            </span>
            <span>
                QE<span className="text-muted-foreground">-</span>AGENT
            </span>
        </Link>
    );
}

/**
 * The single top bar for every page. Previously each page hand-rolled its own
 * link row, which is how they drifted out of sync.
 *
 * The active link is marked with a rule flush to the header's own bottom
 * border rather than a filled pill — the whole design language is hairline
 * rules, and a grey pill was the one place it reverted to a generic chip.
 *
 * Below `md` the links, the theme control and sign-out move into a drawer
 * behind a hamburger, leaving the bar itself to the wordmark plus whatever the
 * page put in `actions` — on a 360px screen that row could not hold all of it.
 */
export default function AppNav({
    actions,
    className,
}: {
    /** Page-specific controls, shown before the account actions. */
    actions?: React.ReactNode;
    className?: string;
}) {
    const pathname = usePathname();
    const scrolled = useScrolled();
    const [menuOpen, setMenuOpen] = useState(false);
    const menuId = useId();
    const triggerRef = useRef<HTMLButtonElement>(null);

    const isActive = (href: string) =>
        pathname === href || pathname.startsWith(`${href}/`);

    // Navigating is the point of the menu, so it closes itself on arrival. This
    // is the "adjust state during render" pattern rather than an effect —
    // React re-renders before committing, so the open drawer never paints on
    // the new route. Covers back/forward too, which a link's onClick does not.
    const [lastPath, setLastPath] = useState(pathname);
    if (pathname !== lastPath) {
        setLastPath(pathname);
        setMenuOpen(false);
    }

    // Resizing up to the desktop layout reveals the real nav; leaving the
    // drawer "open" would keep the body scroll-locked with nothing on screen
    // to close.
    useEffect(() => {
        if (!menuOpen) return;
        const query = window.matchMedia("(min-width: 768px)");
        const sync = () => {
            if (query.matches) setMenuOpen(false);
        };
        query.addEventListener("change", sync);
        return () => query.removeEventListener("change", sync);
    }, [menuOpen]);

    const closeMenu = useCallback(() => {
        setMenuOpen(false);
        triggerRef.current?.focus();
    }, []);

    return (
        <>
            <header
                className={cn(
                    "sticky top-0 z-30 border-b bg-background/80 backdrop-blur-md transition-colors duration-200",
                    // Once the page moves under it the bar earns a firmer edge, so it
                    // reads as a layer above the content instead of floating in it.
                    scrolled ? "border-rule-strong/60" : "border-rule",
                    className
                )}
            >
                <div className="app-shell flex h-14 items-center gap-3">
                    <Wordmark />

                    {/* Before the links: it is the widest piece of context on
                        screen, and every link to its right means something
                        different depending on which project is selected. */}
                    <ProjectSwitcher className="hidden sm:block" />

                    <nav className="ml-2 hidden h-full items-center gap-0.5 md:flex" aria-label="Main">
                        {LINKS.map(({ href, label }) => {
                            const active = isActive(href);
                            return (
                                <Link
                                    key={href}
                                    href={href}
                                    aria-current={active ? "page" : undefined}
                                    className={cn(
                                        "relative flex h-full items-center px-3 text-[0.8125rem] font-medium transition-colors",
                                        // The indicator sits ON the header border (-bottom-px)
                                        // so the two rules read as one continuous line.
                                        "after:absolute after:inset-x-2.5 after:-bottom-px after:h-0.5 after:rounded-full after:transition-all after:duration-200 after:content-['']",
                                        active
                                            ? "text-foreground after:bg-pass"
                                            : "text-muted-foreground hover:text-foreground after:bg-transparent hover:after:bg-rule-strong"
                                    )}
                                >
                                    {label}
                                </Link>
                            );
                        })}
                    </nav>

                    <div className="ml-auto flex items-center gap-2">
                        {actions}

                        {/* A hairline keeps page-specific controls from reading as
                            part of the account cluster. */}
                        {actions ? (
                            <span className="hidden h-5 w-px bg-rule md:block" aria-hidden="true" />
                        ) : null}

                        <div className="hidden items-center gap-2 md:flex">
                            <ThemeToggle />
                            <LogoutButton />
                        </div>

                        <button
                            ref={triggerRef}
                            type="button"
                            onClick={() => setMenuOpen((open) => !open)}
                            aria-expanded={menuOpen}
                            aria-controls={menuId}
                            aria-label={menuOpen ? "Close menu" : "Open menu"}
                            className="-mr-1.5 grid h-9 w-9 place-items-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground md:hidden"
                        >
                            {menuOpen ? (
                                <X className="h-5 w-5" aria-hidden="true" />
                            ) : (
                                <Menu className="h-5 w-5" aria-hidden="true" />
                            )}
                        </button>
                    </div>
                </div>
            </header>

            <MobileMenu
                id={menuId}
                open={menuOpen}
                onClose={closeMenu}
                isActive={isActive}
            />
        </>
    );
}

function MobileMenu({
    id,
    open,
    onClose,
    isActive,
}: {
    id: string;
    open: boolean;
    onClose: () => void;
    isActive: (href: string) => boolean;
}) {
    const isHydrated = useSyncExternalStore(neverChanges, onClient, onServer);
    const panelRef = useRef<HTMLDivElement>(null);

    // `leaving` keeps the panel mounted long enough to animate out. The phase
    // is adjusted during render when `open` flips; only the timer that ends the
    // exit writes state, and it does so from a callback rather than
    // synchronously inside the effect body.
    const [phase, setPhase] = useState<"closed" | "open" | "leaving">("closed");
    const [prevOpen, setPrevOpen] = useState(false);
    if (open !== prevOpen) {
        setPrevOpen(open);
        setPhase(open ? "open" : "leaving");
    }

    useEffect(() => {
        if (phase !== "leaving") return;
        // Reduced-motion users have every animation collapsed globally, so this
        // delay is imperceptible for them.
        const timer = window.setTimeout(() => setPhase("closed"), 190);
        return () => window.clearTimeout(timer);
    }, [phase]);

    const leaving = phase === "leaving";

    // Escape closes; Tab stays inside the panel while it is modal.
    useEffect(() => {
        if (!open) return;
        const onKey = (e: KeyboardEvent) => {
            if (e.key === "Escape") {
                e.preventDefault();
                onClose();
                return;
            }
            if (e.key !== "Tab") return;
            const focusables = panelRef.current?.querySelectorAll<HTMLElement>(
                'a[href], button:not([disabled]), [tabindex]:not([tabindex="-1"])'
            );
            if (!focusables?.length) return;
            const first = focusables[0];
            const last = focusables[focusables.length - 1];
            if (e.shiftKey && document.activeElement === first) {
                e.preventDefault();
                last.focus();
            } else if (!e.shiftKey && document.activeElement === last) {
                e.preventDefault();
                first.focus();
            }
        };
        window.addEventListener("keydown", onKey);
        return () => window.removeEventListener("keydown", onKey);
    }, [open, onClose]);

    // Lock body scroll and move focus into the panel.
    useEffect(() => {
        if (!open) return;
        const previousOverflow = document.body.style.overflow;
        document.body.style.overflow = "hidden";
        panelRef.current?.querySelector<HTMLElement>("a[href]")?.focus();
        return () => {
            document.body.style.overflow = previousOverflow;
        };
    }, [open]);

    if (!isHydrated || phase === "closed") return null;

    // Portalled to <body>: the header sets `backdrop-blur`, which makes it a
    // containing block for fixed descendants — mounted inside it, this panel
    // would be clipped to the bar instead of covering the viewport.
    return createPortal(
        <div className="fixed inset-0 z-40 md:hidden" id={id}>
            <div
                className="absolute inset-0 animate-[overlay-in_180ms_ease-out] bg-black/45 backdrop-blur-[2px]"
                onClick={onClose}
                aria-hidden="true"
            />

            <div
                ref={panelRef}
                role="dialog"
                aria-modal="true"
                aria-label="Menu"
                className={cn(
                    "absolute inset-y-0 right-0 flex w-[min(19rem,85vw)] flex-col border-l border-rule bg-popover shadow-2xl shadow-black/30",
                    leaving
                        ? "animate-[sheet-out_190ms_ease-in_forwards]"
                        : "animate-[sheet-in_220ms_cubic-bezier(0.32,0.72,0,1)]"
                )}
            >
                <div className="flex h-14 shrink-0 items-center justify-between border-b border-rule pl-5 pr-3">
                    <span className="eyebrow text-muted-foreground">Menu</span>
                    <button
                        type="button"
                        onClick={onClose}
                        aria-label="Close menu"
                        className="grid h-9 w-9 place-items-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                    >
                        <X className="h-5 w-5" aria-hidden="true" />
                    </button>
                </div>

                <nav className="scrollbar-thin flex-1 space-y-0.5 overflow-y-auto p-3" aria-label="Main">
                    {LINKS.map(({ href, label }) => {
                        const active = isActive(href);
                        return (
                            <Link
                                key={href}
                                href={href}
                                aria-current={active ? "page" : undefined}
                                onClick={onClose}
                                // The signal gutter, the same device the panels use,
                                // so the active row is marked by a rule here too.
                                data-signal={active ? "pass" : ""}
                                className={cn(
                                    "gutter-rule flex items-center rounded-md py-2.5 pr-3 text-sm font-medium transition-colors",
                                    active
                                        ? "bg-pass-soft text-pass-ink"
                                        : "text-muted-foreground hover:bg-muted hover:text-foreground"
                                )}
                            >
                                {label}
                            </Link>
                        );
                    })}
                </nav>

                <div className="shrink-0 space-y-4 border-t border-rule px-5 py-4">
                    {/* w-full overrides the fixed width both carry in the top
                        bar. Nothing sits beside them in the drawer, so there is
                        no layout to keep still — only a column to fill. */}
                    <div>
                        <p className="eyebrow pb-1.5 text-muted-foreground">Project</p>
                        <ProjectSwitcher className="w-full" />
                    </div>
                    <div className="flex items-center justify-between gap-3">
                        <ThemeToggle />
                        <LogoutButton />
                    </div>
                </div>
            </div>
        </div>,
        document.body
    );
}

/** True once the page has scrolled off the top. Passive listener, no layout reads. */
function useScrolled(threshold = 4) {
    const [scrolled, setScrolled] = useState(false);

    useEffect(() => {
        const onScroll = () => setScrolled(window.scrollY > threshold);
        onScroll();
        window.addEventListener("scroll", onScroll, { passive: true });
        return () => window.removeEventListener("scroll", onScroll);
    }, [threshold]);

    return scrolled;
}
