import '@testing-library/jest-dom';
import { vi } from 'vitest';

// The Supabase browser client is constructed at module scope in
// `lib/supabase/client.ts`, which `lib/api/client.ts` imports. Anything that
// reaches the API client therefore needs these present just to IMPORT — the
// values are never used, because tests never let a real request out.
process.env.NEXT_PUBLIC_SUPABASE_URL ||= 'http://localhost:54321';
process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY ||= 'test-anon-key';
process.env.NEXT_PUBLIC_API_URL ||= 'http://localhost:8000';

// jsdom ships no ResizeObserver. Components that measure their own layout (e.g.
// whether clamped text actually overflows) would throw on construction without
// it. `observe` fires once, as the real one does for the initial observation,
// so the measurement runs in tests too — inside React's act() scope, because it
// is called synchronously from the effect that observes.
class ResizeObserverStub {
    private readonly callback: ResizeObserverCallback;

    constructor(callback: ResizeObserverCallback) {
        this.callback = callback;
    }

    observe() {
        this.callback([], this as unknown as ResizeObserver);
    }

    unobserve() {}
    disconnect() {}
}

window.ResizeObserver ??= ResizeObserverStub as unknown as typeof ResizeObserver;
globalThis.ResizeObserver ??= window.ResizeObserver;

Object.defineProperty(window, 'matchMedia', {
    writable: true,
    value: vi.fn().mockImplementation(query => ({
        matches: false,
        media: query,
        onchange: null,
        addListener: vi.fn(), // Deprecated
        removeListener: vi.fn(), // Deprecated
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        dispatchEvent: vi.fn(),
    })),
});
